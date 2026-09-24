import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../lib/api';
import { ShieldCheck, Activity, Server, RefreshCw, CheckCircle2, XCircle, Loader2 } from 'lucide-react';
import { cn } from '../lib/utils';
import { relativeTime } from '../lib/format';
import type { WorkerStatus } from '../lib/types';
import { AppLayout } from '../components/layout/AppLayout';

const WORKER_ORDER = ['hot', 'warm', 'correlator', 'cold'] as const;
const WORKER_DESC: Record<string, string> = {
  hot: 'Real-time safety',
  warm: 'Window aggregation',
  correlator: 'Incident correlation',
  cold: 'AI explanation',
};

export function Admin() {
  const [verifyResult, setVerifyResult] = useState<{ valid: boolean; error?: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [workerStatus, setWorkerStatus] = useState<WorkerStatus | null>(null);
  const [workerError, setWorkerError] = useState<string | null>(null);

  const loadWorkers = useCallback(async () => {
    try {
      const res = await apiFetch('/admin/workers');
      if (!res.ok) throw new Error('Failed to load worker status');
      setWorkerStatus(await res.json());
      setWorkerError(null);
    } catch (err: any) {
      setWorkerError(err.message);
    }
  }, []);

  useEffect(() => {
    loadWorkers();
    const interval = window.setInterval(loadWorkers, 10000);
    return () => window.clearInterval(interval);
  }, [loadWorkers]);

  const handleVerify = async () => {
    setLoading(true);
    try {
      const res = await apiFetch('/audit/verify');
      if (!res.ok) throw new Error('Verification request failed');
      const data = await res.json();
      setVerifyResult({ valid: data.valid });
    } catch (err: any) {
      setVerifyResult({ valid: false, error: err.message });
    } finally {
      setLoading(false);
    }
  };

  return (
    <AppLayout>
      <div className="p-8 max-w-4xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <div className="data-label mb-1.5">System Administration</div>
          <h1 className="text-3xl font-black text-foreground tracking-tight">Demo Director</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Worker status, stream health, and cryptographic audit verification.
          </p>
        </div>

        <div className="space-y-6">
          {/* Worker Status */}
          <div className="glass-card p-6">
            <div className="flex items-center justify-between mb-5">
              <div className="flex items-center gap-2.5">
                <Server className="w-5 h-5 text-primary" />
                <h2 className="text-base font-bold text-foreground">Worker Status</h2>
              </div>
              <button
                onClick={loadWorkers}
                className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors px-2 py-1 rounded hover:bg-secondary"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                Refresh
              </button>
            </div>

            {workerError ? (
              <p className="text-sm text-status-critical">{workerError}</p>
            ) : !workerStatus ? (
              <div className="flex items-center gap-2 text-muted-foreground text-sm">
                <Loader2 className="w-4 h-4 animate-spin" />
                Loading…
              </div>
            ) : (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {WORKER_ORDER.map((name) => {
                  const info = workerStatus.workers[name];
                  const heartbeat = info?.last_ok_ts;
                  const alive = heartbeat ? Date.now() - new Date(heartbeat).getTime() < 20000 : false;
                  const latency = info?.last_latency_ms ? `${Number(info.last_latency_ms).toFixed(0)}ms` : null;
                  return (
                    <div key={name} className={cn(
                      'rounded-lg border p-4 flex flex-col gap-2.5 transition-colors',
                      alive
                        ? 'border-status-normal/25 bg-status-normal/5'
                        : 'border-border/60 bg-secondary/30'
                    )}>
                      <div className="flex items-center gap-2">
                        <span className={cn(
                          'w-2 h-2 rounded-full shrink-0',
                          alive ? 'bg-status-normal intel-live-dot' : 'bg-status-offline'
                        )} />
                        <span className="text-sm font-bold capitalize text-foreground">{name}</span>
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        {WORKER_DESC[name] ?? name}
                      </div>
                      <div className="text-[10px] font-mono text-muted-foreground/70">
                        {heartbeat ? relativeTime(heartbeat) : 'No heartbeat'}
                        {latency && <span className="ml-1 text-primary/70">· {latency}</span>}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Stream lag */}
            {workerStatus && Object.keys(workerStatus.stream_lag).length > 0 && (
              <div className="mt-5 pt-5 border-t border-border/40">
                <div className="data-label mb-3">Stream Lag</div>
                <div className="intel-scroll max-h-36 overflow-y-auto space-y-1.5">
                  {Object.entries(workerStatus.stream_lag).map(([key, v]) => (
                    <div key={key} className="flex items-center justify-between text-xs font-mono">
                      <span className="text-muted-foreground truncate">{key}</span>
                      <span className="intel-num text-foreground/70 shrink-0 ml-3">
                        pending {v.pending} · lag {v.lag}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Simulator Controls — aspirational, no API backing */}
            <div className="glass-card p-6">
              <div className="flex items-center gap-2.5 mb-4">
                <Activity className="w-5 h-5 text-muted-foreground" />
                <h2 className="text-base font-bold text-foreground">Simulator Controls</h2>
                <span className="ml-auto text-[9px] font-bold uppercase tracking-wider bg-secondary text-muted-foreground px-2 py-1 rounded">
                  Aspirational
                </span>
              </div>
              <p className="text-xs text-muted-foreground mb-4">
                No API endpoint for simulator control exists yet. Use the Makefile:
              </p>
              <div className="space-y-2 opacity-40 pointer-events-none">
                <button disabled className="w-full bg-secondary text-muted-foreground py-2.5 rounded-md text-xs cursor-not-allowed font-medium">
                  Inject Anomaly
                </button>
                <button disabled className="w-full bg-secondary text-muted-foreground py-2.5 rounded-md text-xs cursor-not-allowed font-medium">
                  Toggle Rain / Muddy
                </button>
                <button disabled className="w-full bg-secondary text-muted-foreground py-2.5 rounded-md text-xs cursor-not-allowed font-medium">
                  Speed Slider
                </button>
              </div>
              <p className="text-[10px] text-muted-foreground/50 mt-4 font-mono">
                $ make demo · make tamper
              </p>
            </div>

            {/* Audit Chain Verification */}
            <div className="glass-card p-6">
              <div className="flex items-center gap-2.5 mb-4">
                <ShieldCheck className="w-5 h-5 text-primary" />
                <h2 className="text-base font-bold text-foreground">Audit Chain Verification</h2>
              </div>

              <p className="text-xs text-muted-foreground mb-5">
                Cryptographic SHA-256 verification of the append-only audit log. Each row's hash is computed from <span className="font-mono">prev_hash + payload</span> — any tampered row breaks the chain.
              </p>

              <button
                onClick={handleVerify}
                disabled={loading}
                className={cn(
                  'w-full font-bold py-3 rounded-lg transition-all text-sm tracking-wide flex items-center justify-center gap-2',
                  loading
                    ? 'bg-secondary text-muted-foreground cursor-not-allowed'
                    : 'bg-primary text-primary-foreground hover:bg-primary/90 shadow-[0_0_20px_rgb(255_203_5/_0.15)]'
                )}
              >
                {loading ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Verifying Chain…
                  </>
                ) : (
                  <>
                    <ShieldCheck className="w-4 h-4" />
                    Verify Cryptographic Chain
                  </>
                )}
              </button>

              {verifyResult && (
                <div className={cn(
                  'mt-4 p-4 rounded-lg border flex items-start gap-3',
                  verifyResult.valid
                    ? 'bg-status-normal/8 border-status-normal/30 text-status-normal'
                    : 'bg-status-critical/8 border-status-critical/30 text-status-critical'
                )}>
                  {verifyResult.valid
                    ? <CheckCircle2 className="w-5 h-5 shrink-0 mt-0.5" />
                    : <XCircle className="w-5 h-5 shrink-0 mt-0.5" />
                  }
                  <div>
                    <div className="font-bold text-sm">
                      {verifyResult.valid ? '✓ Chain Valid' : '✗ Chain Compromised'}
                    </div>
                    <div className="text-xs opacity-80 mt-1">
                      {verifyResult.valid
                        ? 'All SHA-256 hashes verify deterministically. No tampering detected.'
                        : (verifyResult.error ?? 'A hash mismatch was found. The chain has been tampered with.')}
                    </div>
                  </div>
                </div>
              )}

              <p className="text-[10px] text-muted-foreground/40 mt-4 text-center font-mono">
                Run `make tamper` to corrupt a row for demonstration
              </p>
            </div>
          </div>
        </div>
      </div>
    </AppLayout>
  );
}
