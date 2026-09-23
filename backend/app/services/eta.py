"""
ETA prediction, SHAP explanation, live blending and slip detection.
Pure functions except for reading the lazy-loaded ModelRegistry — no stream
or DB access here (that's Batch 3D's warm worker).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import shap

from backend.app.services.ml_registry import get_registry, ModelRegistry
from contracts.intelligence import EtaFactor
from ml.constants import ETA_FACTOR_GROUPS
from ml.features import eta_feature_row, encode_eta_row

logger = logging.getLogger(__name__)

_explainer_cache: dict[str, "shap.TreeExplainer"] = {}
_explainer_cache_model_version: str | None = None


@dataclass
class EtaPrediction:
    p10_min: float
    p50_min: float
    p90_min: float
    factors: list[EtaFactor]
    base_value_min: float
    defaults_used: list[str]
    model_version: str


@dataclass
class EtaLive:
    remaining_p10_min: float
    remaining_p50_min: float
    remaining_p90_min: float
    eta_total_p50_min: float
    progress: float
    blend_weight_model: float
    observed_cycle_s: float | None
    cycles_done: int
    target_cycles: int


def _get_explainer(registry: ModelRegistry, task_type: str):
    global _explainer_cache_model_version
    model_version = registry.eta_meta.get("model_version") if registry.eta_meta else None
    if _explainer_cache_model_version != model_version:
        _explainer_cache.clear()
        _explainer_cache_model_version = model_version

    if task_type not in _explainer_cache:
        background = registry.eta_background.get(task_type)
        if background is None or len(background) == 0:
            # Fall back to any available background sample rather than failing outright.
            for v in registry.eta_background.values():
                background = v
                break
        _explainer_cache[task_type] = shap.TreeExplainer(
            registry.eta_p50_model, data=background, feature_perturbation="interventional"
        )
    return _explainer_cache[task_type]


def explain(x_row: np.ndarray, task_type: str, registry: ModelRegistry | None = None) -> tuple[list[EtaFactor], float]:
    """Returns (grouped factors, base_value_min). Invariant: base + sum(factor minutes) == p50 prediction."""
    registry = registry or get_registry()
    if registry.eta_status != "READY":
        return [], 0.0

    explainer = _get_explainer(registry, task_type)
    shap_values = explainer.shap_values(x_row.reshape(1, -1))[0]
    base_value = float(explainer.expected_value)

    feature_columns = registry.eta_meta["feature_columns"]
    # column -> group
    col_to_group = {}
    for group, fields in ETA_FACTOR_GROUPS.items():
        for col in feature_columns:
            field_name = col.split("__")[0]
            if field_name in fields:
                col_to_group[col] = group

    group_sums: dict[str, float] = {g: 0.0 for g in ETA_FACTOR_GROUPS}
    group_top: dict[str, tuple[str, float, float]] = {}  # group -> (feature, |shap|, value)
    for col, sv, val in zip(feature_columns, shap_values, x_row):
        group = col_to_group.get(col)
        if group is None:
            continue
        group_sums[group] += float(sv)
        if group not in group_top or abs(sv) > group_top[group][1]:
            group_top[group] = (col, abs(float(sv)), float(val))

    factors = []
    for group in ETA_FACTOR_GROUPS:
        top_feature, _, top_val = group_top.get(group, (list(ETA_FACTOR_GROUPS[group])[0], 0.0, 0.0))
        factors.append(EtaFactor(
            group=group, minutes=round(group_sums[group], 1),
            top_feature=top_feature, top_feature_value=top_val,
        ))

    return factors, base_value


def predict_task(task_ctx: dict, registry: ModelRegistry | None = None) -> EtaPrediction | None:
    registry = registry or get_registry()
    if registry.eta_status != "READY":
        return None

    row = eta_feature_row(task_ctx)
    defaults_used = row.pop("_defaults_used")
    feature_columns = registry.eta_meta["feature_columns"]
    x = encode_eta_row(row, feature_columns)

    p50 = float(registry.eta_p50_model.predict(x.reshape(1, -1))[0])
    q_pred = registry.eta_q_model.predict(x.reshape(1, -1))[0]
    p10, p90 = float(q_pred[0]), float(q_pred[1])

    # Crossing guard: quantile models are trained independently and can cross.
    if p10 > p50:
        p10 = p50
    if p90 < p50:
        p90 = p50

    task_type = row.get("task_type", "TRUCK_LOADING")
    factors, base_value = explain(x, task_type, registry)

    p50_rounded = round(p50, 2)
    base_rounded = round(base_value, 2)
    # SHAP guarantees base_value + sum(raw factor values) == raw p50 exactly.
    # Each factor is independently rounded to 1dp for display, which can
    # accumulate a residual of a few hundredths across several groups (ARCH
    # 6.8 requires the displayed numbers to sum EXACTLY to the displayed
    # p50) — absorb that residual into the largest-magnitude factor so the
    # displayed breakdown always adds up.
    residual = p50_rounded - base_rounded - sum(f.minutes for f in factors)
    if factors and abs(residual) > 1e-9:
        biggest = max(range(len(factors)), key=lambda i: abs(factors[i].minutes))
        factors[biggest] = factors[biggest].model_copy(
            update={"minutes": round(factors[biggest].minutes + residual, 2)}
        )

    return EtaPrediction(
        p10_min=round(p10, 2), p50_min=p50_rounded, p90_min=round(p90, 2),
        factors=factors, base_value_min=base_rounded,
        defaults_used=defaults_used, model_version=registry.eta_meta["model_version"],
    )


def blend(
    p50_total_min: float, p10_total_min: float, p90_total_min: float,
    elapsed_min: float, cycles_done: int, target_cycles: int,
    observed_cycle_s: float | None, eta_min_cycles_for_blend: int = 3,
) -> EtaLive:
    progress = max(0.0, min(1.0, cycles_done / target_cycles)) if target_cycles > 0 else 0.0
    model_remaining = max(p50_total_min - elapsed_min, 0.0)

    if observed_cycle_s is not None and observed_cycle_s > 0:
        observed_remaining = max(target_cycles - cycles_done, 0) * observed_cycle_s / 60.0
    else:
        observed_remaining = model_remaining

    # ARCH: w = 1 - progress, but pure-model until we have enough cycles to
    # trust an observed rate.
    w = 1.0 if cycles_done < eta_min_cycles_for_blend else max(0.0, 1.0 - progress)

    remaining = w * model_remaining + (1 - w) * observed_remaining
    remaining_p10 = max(remaining - w * (p50_total_min - p10_total_min), 0.0)
    remaining_p90 = remaining + w * (p90_total_min - p50_total_min)

    return EtaLive(
        remaining_p10_min=round(remaining_p10, 2),
        remaining_p50_min=round(remaining, 2),
        remaining_p90_min=round(remaining_p90, 2),
        eta_total_p50_min=round(elapsed_min + remaining, 2),
        progress=round(progress, 3),
        blend_weight_model=round(w, 3),
        observed_cycle_s=observed_cycle_s,
        cycles_done=cycles_done,
        target_cycles=target_cycles,
    )


def slip(
    baseline_p50_total_min: float, current_total_min: float,
    last_emitted_band: int, slip_threshold: float, warning_threshold: float,
) -> tuple[float, int, str | None]:
    """
    Returns (slip_pct, band, severity_or_None). severity is None when no new
    event should fire (band did not increase beyond last_emitted_band).
    """
    if baseline_p50_total_min <= 0:
        return 0.0, 0, None
    slip_pct = (current_total_min - baseline_p50_total_min) / baseline_p50_total_min
    if slip_pct < slip_threshold:
        return round(slip_pct, 4), 0, None

    import math
    band = int(math.floor(slip_pct / slip_threshold))
    if band <= last_emitted_band:
        return round(slip_pct, 4), band, None

    severity = "WARNING" if slip_pct >= warning_threshold else "INFO"
    return round(slip_pct, 4), band, severity
