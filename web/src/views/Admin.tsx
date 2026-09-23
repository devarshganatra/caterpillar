import { useState } from 'react';
import { apiFetch } from '../lib/api';
import { ShieldCheck, ShieldAlert, Activity } from 'lucide-react';
import { cn } from '../lib/utils';

export function Admin() {
  const [verifyResult, setVerifyResult] = useState<{ valid: boolean, error?: string } | null>(null);
  const [loading, setLoading] = useState(false);

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
    <div className="min-h-screen p-8 max-w-4xl mx-auto space-y-8">
      <div>
        <h1 className="text-3xl font-bold text-primary">Demo Director (Admin)</h1>
        <p className="text-muted-foreground mt-2">Manage the simulator and verify system integrity.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
        
        {/* Controls Stub */}
        <div className="glass-card p-6 space-y-4">
          <div className="flex items-center gap-3 border-b border-border pb-4">
            <Activity className="text-primary" />
            <h2 className="text-xl font-semibold">Simulator Controls</h2>
          </div>
          
          <div className="pt-4 space-y-2 opacity-50">
            <p className="text-sm text-muted-foreground italic">
              These controls are currently unavailable in the API. Use the Makefile commands (e.g. `make demo`) to control the simulator directly.
            </p>
            <button disabled className="w-full bg-secondary py-2 rounded text-sm cursor-not-allowed">Inject Anomaly</button>
            <button disabled className="w-full bg-secondary py-2 rounded text-sm cursor-not-allowed">Toggle Rain</button>
          </div>
        </div>

        {/* Audit Chain Verify */}
        <div className="glass-card p-6 space-y-4">
          <div className="flex items-center gap-3 border-b border-border pb-4">
            <ShieldCheck className="text-primary" />
            <h2 className="text-xl font-semibold">Audit Chain Verification</h2>
          </div>

          <div className="pt-4 space-y-6">
            <p className="text-sm text-muted-foreground">
              Run a cryptographic verification of the append-only audit chain. This ensures no modifications have been made to historical safety decisions.
            </p>

            <button
              onClick={handleVerify}
              disabled={loading}
              className="w-full bg-primary text-primary-foreground font-semibold py-3 rounded hover:bg-primary/90 transition-colors disabled:opacity-50"
            >
              {loading ? 'Verifying Chain...' : 'Verify Cryptographic Chain'}
            </button>

            {verifyResult && (
              <div className={cn(
                "p-4 rounded border flex items-start gap-3",
                verifyResult.valid 
                  ? "bg-status-normal/10 border-status-normal text-status-normal"
                  : "bg-destructive/10 border-destructive text-destructive"
              )}>
                {verifyResult.valid ? <ShieldCheck className="w-6 h-6 shrink-0" /> : <ShieldAlert className="w-6 h-6 shrink-0" />}
                <div>
                  <div className="font-bold">{verifyResult.valid ? 'Chain Valid' : 'Chain Compromised'}</div>
                  <div className="text-sm opacity-80 mt-1">
                    {verifyResult.valid 
                      ? 'All hashes map deterministically. No tampering detected.'
                      : (verifyResult.error || 'A cryptographic hash mismatch was found. The chain has been tampered with.')}
                  </div>
                </div>
              </div>
            )}
            
            <p className="text-xs text-muted-foreground italic mt-4 text-center">
              (Use `make tamper` in the terminal to corrupt a row for demonstration purposes)
            </p>
          </div>
        </div>

      </div>
    </div>
  );
}
