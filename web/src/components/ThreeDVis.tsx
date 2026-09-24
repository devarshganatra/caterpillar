import type { EnvelopeState } from '../lib/types';

interface ThreeDVisProps {
  currentState: string;
  riskLevel: string;
  /** Passed explicitly from the HUD so the vis can reflect real envelope data.
   *  This is the fix for the plan gap (item 71–79): ThreeDVis previously only
   *  read currentState/riskLevel from useMachine() and couldn't access envelope. */
  envelope?: EnvelopeState | null;
}

const STATE_COLOR: Record<string, string> = {
  WORKING: '#FFCB05',
  TRAVEL:  '#818cf8',
  IDLE:    '#f59e0b',
  OFF:     '#475569',
};

const RISK_GLOW: Record<string, string> = {
  HIGH:     '0 0 48px 8px rgb(239 68 68 / 0.4)',
  ELEVATED: '0 0 36px 6px rgb(245 158 11 / 0.3)',
  NORMAL:   '0 0 24px 4px rgb(255 203 5 / 0.15)',
};

/** 2.5D CSS excavator visualization.
 *  Reflects state (color, animations), risk (glow), and envelope (proximity rings).
 *  All values come from real backend-pushed fields — no fabricated data. */
export function ThreeDVis({ currentState, riskLevel, envelope }: ThreeDVisProps) {
  const isWorking = currentState === 'WORKING';
  const isMoving  = currentState === 'TRAVEL';
  const isOff     = currentState === 'OFF';

  const bodyColor = STATE_COLOR[currentState] ?? STATE_COLOR.OFF;
  const glow      = RISK_GLOW[riskLevel] ?? RISK_GLOW.NORMAL;

  // Proximity zone rings from real envelope data — only shown when active conditions exist
  const hasRedZone    = envelope && envelope.active_conditions.length > 0;
  const hasOrangeZone = envelope && envelope.active_conditions.length > 0;

  return (
    <div className="relative w-[380px] h-[280px] flex items-center justify-center select-none">

      {/* Technical grid background */}
      <div className="absolute inset-0 hud-grid-fine opacity-30 rounded-2xl" />

      {/* Proximity zone rings (only when envelope has active conditions) */}
      {hasRedZone && (
        <div
          className="absolute rounded-full border-2 border-status-critical/40 prox-ring"
          style={{ width: 300, height: 300 }}
        />
      )}
      {hasOrangeZone && (
        <div
          className="absolute rounded-full border border-status-warning/30 prox-ring"
          style={{ width: 220, height: 220, animationDelay: '0.4s' }}
        />
      )}

      {/* ── Machine body ──────────────────────────────────────────────── */}
      <div
        className="relative"
        style={{
          width: 260,
          height: 200,
          filter: isOff ? 'saturate(0) brightness(0.4)' : 'none',
          transition: 'filter 0.6s',
        }}
      >
        {/* Tracks */}
        <div
          className="absolute bottom-0 left-0 rounded-full overflow-hidden"
          style={{ width: 240, height: 28, background: '#1e293b', border: '2px solid #334155' }}
        >
          {(isWorking || isMoving) && (
            <div
              className="absolute inset-0 track-animate"
              style={{
                backgroundImage: 'repeating-linear-gradient(90deg, transparent, transparent 8px, rgba(255,255,255,0.08) 8px, rgba(255,255,255,0.08) 16px)',
                backgroundSize: '24px 100%',
              }}
            />
          )}
          {/* Track wheels */}
          {[0, 56, 112, 168, 212].map(x => (
            <div
              key={x}
              className="absolute top-2 rounded-full"
              style={{ left: x + 8, width: 18, height: 18, background: '#0f172a', border: `2px solid ${isOff ? '#334155' : bodyColor}30` }}
            />
          ))}
        </div>

        {/* Main body / chassis */}
        <div
          className="absolute"
          style={{
            left: 20, bottom: 24,
            width: 200, height: 80,
            background: `linear-gradient(135deg, ${bodyColor}ee 0%, ${bodyColor}99 100%)`,
            borderRadius: 8,
            border: `2px solid ${bodyColor}60`,
            boxShadow: glow,
            transition: 'background 0.5s, box-shadow 0.5s',
          }}
        >
          {/* CAT text on body */}
          <div style={{ position: 'absolute', bottom: 10, left: 14, fontWeight: 900, fontSize: 18, letterSpacing: '-0.02em', color: 'rgba(0,0,0,0.6)' }}>
            CAT
          </div>
          {/* Body panel lines */}
          <div style={{ position: 'absolute', top: 16, right: 16, width: 60, height: 3, background: 'rgba(0,0,0,0.2)', borderRadius: 2 }} />
          <div style={{ position: 'absolute', top: 24, right: 16, width: 40, height: 2, background: 'rgba(0,0,0,0.15)', borderRadius: 2 }} />
        </div>

        {/* Cab */}
        <div
          className="absolute"
          style={{
            right: 20, bottom: 100,
            width: 80, height: 70,
            background: `linear-gradient(135deg, ${bodyColor}dd 0%, ${bodyColor}88 100%)`,
            borderRadius: '8px 8px 0 0',
            border: `2px solid ${bodyColor}50`,
            transition: 'background 0.5s',
          }}
        >
          {/* Cab windows */}
          <div style={{ position: 'absolute', top: 12, left: 10, width: 24, height: 24, background: '#0f172a', borderRadius: 3, border: '1px solid rgba(255,255,255,0.15)' }} />
          <div style={{ position: 'absolute', top: 12, right: 10, width: 16, height: 24, background: '#0f172a', borderRadius: 3, border: '1px solid rgba(255,255,255,0.15)' }} />
        </div>

        {/* Boom arm */}
        <div
          className={isWorking ? 'boom-animate' : ''}
          style={{
            position: 'absolute',
            left: 40, bottom: 96,
            width: 140, height: 16,
            background: `linear-gradient(90deg, ${bodyColor}cc 0%, ${bodyColor}66 100%)`,
            borderRadius: 4,
            border: `1.5px solid ${bodyColor}50`,
            transformOrigin: '10px 8px',
            transform: 'rotate(-20deg)',
            transition: 'background 0.5s',
          }}
        />

        {/* Stick */}
        <div
          className={isWorking ? 'boom-animate' : ''}
          style={{
            position: 'absolute',
            left: 148, bottom: 114,
            width: 10, height: 80,
            background: `${bodyColor}99`,
            borderRadius: 3,
            transformOrigin: '5px 0px',
            transform: 'rotate(10deg)',
            animationDelay: '0.2s',
            transition: 'background 0.5s',
          }}
        />

        {/* Bucket */}
        <div
          className={isWorking ? 'boom-animate' : ''}
          style={{
            position: 'absolute',
            left: 140, bottom: 38,
            width: 32, height: 22,
            background: `${bodyColor}dd`,
            borderRadius: '0 0 10px 10px',
            border: `2px solid ${bodyColor}60`,
            animationDelay: '0.3s',
            transition: 'background 0.5s',
          }}
        />

        {/* Telemetry marker dots */}
        <TelemetryDot label="ENG" x={120} y={105} active={!isOff} />
        <TelemetryDot label="HYD" x={210} y={60} active={isWorking} color="#38bdf8" />
        <TelemetryDot label="TRK" x={30}  y={175} active={isMoving || isWorking} />
      </div>

      {/* State label at bottom */}
      <div
        className="absolute bottom-2 left-1/2 -translate-x-1/2 text-[10px] font-mono tracking-widest uppercase opacity-50"
        style={{ color: bodyColor }}
      >
        {isOff ? 'Offline' : `${currentState} · Telemetry Active`}
      </div>
    </div>
  );
}

function TelemetryDot({
  label, x, y, active, color = '#FFCB05',
}: {
  label: string; x: number; y: number; active: boolean; color?: string;
}) {
  return (
    <div
      className={active ? 'intel-live-dot' : ''}
      style={{
        position: 'absolute',
        left: x, top: y,
        display: 'flex',
        alignItems: 'center',
        gap: 4,
        opacity: active ? 1 : 0.3,
        transition: 'opacity 0.4s',
      }}
    >
      <div style={{ width: 7, height: 7, borderRadius: '50%', background: color, border: '1.5px solid rgba(255,255,255,0.4)' }} />
      <span style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.06em', color: '#94a3b8' }}>{label}</span>
    </div>
  );
}
