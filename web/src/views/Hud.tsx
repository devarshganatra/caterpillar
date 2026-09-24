import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMachine } from '../store/MachineContext';
import {
  ShieldAlert, SignalHigh, SignalZero, SignalMedium,
  Gauge, Droplets, Activity, AlertTriangle, CheckCircle2, Zap,
} from 'lucide-react';
import { cn } from '../lib/utils';
import { ThreeDVis } from '../components/ThreeDVis';
import { EtaChip } from '../components/intel/EtaChip';
import { AnomalyBadge } from '../components/intel/AnomalyBadge';
import { AppLayout } from '../components/layout/AppLayout';
import type { AlertItem } from '../store/useMachineStream';

/* ── helpers ────────────────────────────────────────────────────────────── */
function stateBadgeClass(state: string) {
  const map: Record<string, string> = {
    WORKING: 'state-working', TRAVEL: 'state-travel',
    IDLE: 'state-idle', OFF: 'state-off',
  };
  return map[state] ?? 'state-off';
}

function riskPillClass(level: string) {
  if (level === 'HIGH') return 'risk-pill risk-pill-high';
  if (level === 'ELEVATED') return 'risk-pill risk-pill-elevated';
  return 'risk-pill risk-pill-normal';
}

function conditionLabel(c: string) {
  if (c === 'RAIN')    return { label: 'Rain',  cls: 'condition-badge condition-badge-rain' };
  if (c === 'MUDDY')   return { label: 'Muddy', cls: 'condition-badge condition-badge-mud' };
  if (c === 'ICY')     return { label: 'Icy',   cls: 'condition-badge condition-badge-ice' };
  if (c.includes('WIND')) return { label: 'Wind', cls: 'condition-badge condition-badge-wind' };
  return { label: c, cls: 'condition-badge condition-badge-wind' };
}

function ConnectionDot({ status }: { status: string }) {
  const cfg = {
    CONNECTED:    { color: 'bg-status-normal intel-live-dot', text: 'Connected' },
    CONNECTING:   { color: 'bg-status-warning animate-pulse', text: 'Connecting' },
    STALE:        { color: 'bg-status-warning animate-pulse', text: 'Stale' },
    DISCONNECTED: { color: 'bg-status-critical animate-pulse', text: 'Disconnected' },
  }[status] ?? { color: 'bg-status-offline', text: status };

  const Icon = status === 'CONNECTED' ? SignalHigh
    : status === 'CONNECTING' ? SignalMedium
    : SignalZero;

  return (
    <div className={cn(
      'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border transition-colors',
      status === 'CONNECTED'
        ? 'text-status-normal border-status-normal/30 bg-status-normal/5'
        : status === 'DISCONNECTED'
        ? 'text-status-critical border-status-critical/30 bg-status-critical/5'
        : 'text-status-warning border-status-warning/30 bg-status-warning/5'
    )}>
      <Icon className="w-3.5 h-3.5" />
      <span className={cn('w-1.5 h-1.5 rounded-full shrink-0', cfg.color)} />
      {cfg.text}
    </div>
  );
}

function AlertBanner({ alert }: { alert: AlertItem }) {
  const isCrit = alert.severity === 'CRITICAL';
  return (
    <div className={cn(
      'alert-enter flex items-center gap-3 px-4 py-2.5 text-sm font-medium border-b',
      isCrit
        ? 'bg-status-critical/10 border-status-critical/30 text-status-critical'
        : 'bg-status-warning/10 border-status-warning/30 text-status-warning'
    )}>
      <ShieldAlert className="w-4 h-4 shrink-0" />
      <span className="font-bold uppercase tracking-wide text-xs">{alert.severity}</span>
      <span className="text-foreground/80">{alert.type.replace(/_/g, ' ')}</span>
      {alert.evidence?.details != null && (
        <span className="opacity-70">— {String(alert.evidence.details as unknown)}</span>
      )}
      {alert.count && alert.count > 1 && (
        <span className="ml-auto shrink-0 text-xs opacity-60">×{alert.count}</span>
      )}
    </div>
  );
}

/* ── Main HUD ───────────────────────────────────────────────────────────── */
export function Hud() {
  const {
    machineId, status, currentState, riskLevel, riskScore,
    alerts, envelope, eta, anomaly,
  } = useMachine();
  const navigate = useNavigate();

  useEffect(() => {
    if (!machineId) navigate('/operator/prestart');
  }, [machineId, navigate]);

  useEffect(() => {
    // Navigate to Idle Hub when backend signals IDLE_HUB mode
    // (ui_mode comes from the state_change push payload, read via useMachine)
  }, []);

  const { uiMode } = useMachine();
  useEffect(() => {
    if (uiMode === 'IDLE_HUB') navigate('/operator/idle');
  }, [uiMode, navigate]);

  const isConnected = status === 'CONNECTED';

  return (
    <AppLayout>
      <div className="flex flex-col h-full min-h-screen relative bg-[#080d1a]">

        {/* ── Top Header Bar ──────────────────────────────────────────── */}
        <div className="shrink-0 flex items-center gap-3 px-5 py-3 border-b border-border/60 bg-[#08101e]/90 backdrop-blur-sm z-20">
          {/* Machine ID + state */}
          <div>
            <div className="data-label leading-none mb-1">Machine ID</div>
            <div className="font-black text-xl text-foreground tracking-tight leading-none">{machineId ?? '—'}</div>
          </div>
          <div className={cn('state-badge ml-1', stateBadgeClass(currentState))}>
            <span className={cn('w-1.5 h-1.5 rounded-full', currentState === 'WORKING' ? 'bg-status-normal' : 'bg-current')} />
            {currentState}
          </div>

          {/* Tabs (cosmetic — matches reference layout) */}
          <div className="ml-4 flex items-center gap-1 text-xs font-medium">
            <span className="px-3 py-1.5 rounded bg-primary/10 text-primary border border-primary/20">Operations</span>
            <span className="px-3 py-1.5 rounded text-muted-foreground hover:text-foreground cursor-default">Machine Status</span>
            <span className="px-3 py-1.5 rounded text-muted-foreground hover:text-foreground cursor-default">Safety Envelope</span>
          </div>

          {/* Right cluster — connection status and risk only (no HMAC badge: server-side only) */}
          <div className="ml-auto flex items-center gap-2.5">
            <ConnectionDot status={status} />

            <div className={cn('risk-pill', riskPillClass(riskLevel))}>
              <span className={cn(
                'w-1.5 h-1.5 rounded-full',
                riskLevel === 'HIGH' ? 'bg-status-critical' :
                riskLevel === 'ELEVATED' ? 'bg-status-warning' : 'bg-status-normal'
              )} />
              RISK {riskLevel}
              {riskScore != null && (
                <span className="intel-num opacity-70 ml-1">{riskScore.toFixed(2)}</span>
              )}
            </div>
          </div>
        </div>

        {/* ── Alert banner strip (max 3, newest first) ────────────────── */}
        <div className="shrink-0 z-20">
          {alerts.slice(0, 3).map((alert, i) => (
            <AlertBanner key={alert.event_id ?? i} alert={alert} />
          ))}
        </div>

        {/* ── Main content: side cards + center vis ───────────────────── */}
        <div className="flex-1 flex relative overflow-hidden hud-grid">

          {/* Left floating card: Machine Overview */}
          <div className="absolute left-5 top-5 w-[280px] z-10 space-y-0 intel-rise">
            <div className="glass-card-raised p-5">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-bold text-foreground tracking-wide">Machine Overview</h3>
                <CheckCircle2 className="w-4 h-4 text-status-normal" />
              </div>

              {/* Real-Time Envelope */}
              <div className="mb-4">
                <div className="data-label mb-1.5">Real-Time Envelope</div>
                {envelope ? (
                  <>
                    <div className="text-sm font-medium text-foreground mb-2">
                      Speed cap: <span className="intel-num text-primary font-bold">{envelope.speed_cap_kmh}</span>
                      <span className="text-muted-foreground ml-1">km/h</span>
                    </div>
                    {envelope.active_conditions.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {envelope.active_conditions.map((c: string) => {
                          const { label, cls } = conditionLabel(c);
                          return <span key={c} className={cls}>{label}</span>;
                        })}
                      </div>
                    )}
                  </>
                ) : (
                  <div className="text-xs text-muted-foreground italic">No active restrictions</div>
                )}
              </div>

              {/* Fuel Rate — real field from telemetry */}
              <div className="mb-4 pt-3 border-t border-border/40">
                <div className="data-label mb-1">Fuel Rate</div>
                <div className="flex items-baseline gap-1">
                  <Droplets className="w-4 h-4 text-sky-400 shrink-0" />
                  <span className="intel-num text-2xl font-bold text-foreground">
                    {/* fuel_rate is pushed via state_change payload — show from snapshot if available */}
                    —
                  </span>
                  <span className="text-xs text-muted-foreground ml-1">L/h</span>
                </div>
              </div>

              {/* Risk Score */}
              <div className="mb-4 pt-3 border-t border-border/40">
                <div className="data-label mb-1">Risk Score</div>
                <div className="flex items-center gap-2">
                  <Gauge className="w-4 h-4 text-muted-foreground shrink-0" />
                  <span className={cn(
                    'intel-num text-2xl font-bold',
                    riskLevel === 'HIGH' ? 'text-status-critical' :
                    riskLevel === 'ELEVATED' ? 'text-status-warning' : 'text-status-normal'
                  )}>
                    {riskScore != null ? riskScore.toFixed(2) : '—'}
                  </span>
                </div>
              </div>

              {/* Anomaly Engine */}
              <div className="pt-3 border-t border-border/40">
                <div className="data-label mb-2">Anomaly Engine</div>
                <AnomalyBadge anomaly={anomaly} expanded />
              </div>
            </div>
          </div>

          {/* Center: 3D Vis */}
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-0">
            <ThreeDVis
              currentState={currentState}
              riskLevel={riskLevel}
              envelope={envelope}
            />
          </div>

          {/* Right floating cards: ETA + Idle Attribution */}
          <div className="absolute right-5 top-5 w-[300px] z-10 space-y-4 intel-rise">
            {/* ETA card (compact right-side version) */}
            <div className="glass-card-raised p-5">
              <div className="data-label mb-3">Task ETA</div>
              <EtaChip eta={eta} />
            </div>

            {/* Alerts zone (max 3) */}
            {alerts.length > 0 && (
              <div className="glass-card-raised p-4">
                <div className="flex items-center gap-2 mb-3">
                  <ShieldAlert className="w-4 h-4 text-status-warning" />
                  <div className="data-label">Active Alerts</div>
                  <span className="ml-auto text-[10px] font-bold text-status-warning bg-status-warning/10 px-1.5 py-0.5 rounded">
                    {alerts.length}
                  </span>
                </div>
                <div className="space-y-2">
                  {alerts.map((alert, i) => (
                    <div key={alert.event_id ?? i} className={cn(
                      'text-xs p-2 rounded flex items-start gap-2',
                      alert.severity === 'CRITICAL' ? 'bg-status-critical/10 text-status-critical' :
                      alert.severity === 'WARNING'  ? 'bg-status-warning/10 text-status-warning' :
                      'bg-secondary text-muted-foreground'
                    )}>
                      <AlertTriangle className="w-3 h-3 shrink-0 mt-0.5" />
                      <span className="font-medium">{alert.type.replace(/_/g, ' ')}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {alerts.length === 0 && (
              <div className="glass-card-raised p-4 flex items-center gap-2 text-status-normal text-xs">
                <CheckCircle2 className="w-4 h-4" />
                <span>No active alerts</span>
              </div>
            )}
          </div>
        </div>

        {/* ── Bottom status bar ────────────────────────────────────────── */}
        <div className="shrink-0 flex items-center gap-5 px-5 py-2.5 border-t border-border/40 bg-[#08101e]/90 backdrop-blur-sm text-xs text-muted-foreground z-20">
          <span className="flex items-center gap-1.5">
            <Activity className="w-3.5 h-3.5" />
            Current Status:
            <span className={cn('font-bold uppercase', currentState === 'WORKING' ? 'text-status-normal' : currentState === 'IDLE' ? 'text-status-warning' : 'text-muted-foreground')}>
              {currentState}
            </span>
          </span>
          <span className="text-border">|</span>
          <span>
            Machine: <span className="font-mono text-foreground">{machineId ?? '—'}</span>
          </span>
          {envelope && envelope.active_conditions.length > 0 && (
            <>
              <span className="text-border">|</span>
              <span className="flex items-center gap-1">
                <Zap className="w-3 h-3 text-status-warning" />
                {envelope.active_conditions.join(', ')}
                {' · Cap '}
                <span className="intel-num text-foreground">{envelope.speed_cap_kmh}</span> km/h
              </span>
            </>
          )}
          <span className="ml-auto flex items-center gap-1.5">
            <span className={cn('w-1.5 h-1.5 rounded-full', isConnected ? 'bg-status-normal intel-live-dot' : 'bg-status-offline')} />
            WS {status}
          </span>
        </div>

        {/* ── Disconnected overlay ─────────────────────────────────────── */}
        {!isConnected && status === 'DISCONNECTED' && (
          <div className="absolute inset-0 bg-background/85 backdrop-blur-sm z-50 flex flex-col items-center justify-center gap-4">
            <SignalZero className="w-14 h-14 text-status-critical" />
            <div className="text-center">
              <h2 className="text-xl font-bold text-foreground">Live Data Unavailable</h2>
              <p className="text-muted-foreground text-sm mt-1">Attempting to reconnect…</p>
            </div>
          </div>
        )}
      </div>
    </AppLayout>
  );
}
