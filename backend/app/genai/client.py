# ALL Gemini calls live here and ONLY here.
# Stubs with type signatures — no implementation yet.

from typing import Dict, Any

async def explain_incident(incident_id: str, rbac_scope: Dict[str, Any]) -> Dict[str, Any]:
    pass

async def generate_lesson(incident_id: str, operator_id: str) -> Dict[str, Any]:
    pass

async def summarize_shift(operator_id: str, shift_id: str) -> Dict[str, Any]:
    pass
