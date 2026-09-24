import { useState, type FormEvent } from 'react';
import { apiFetch } from '../lib/api';
import { useAuth } from '../store/AuthContext';
import { useNavigate } from 'react-router-dom';
import { AlertCircle, Lock, User } from 'lucide-react';

export function Login() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const response = await apiFetch('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      if (!response.ok) throw new Error('Invalid credentials');
      const data = await response.json();
      login(data.access_token);
      navigate('/');
    } catch (err: any) {
      setError(err.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="flex min-h-screen items-center justify-center p-4 hud-grid relative"
      style={{ background: '#080d1a' }}
    >
      {/* Subtle radial glow behind the card */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background: 'radial-gradient(ellipse 60% 40% at 50% 50%, rgb(255 203 5 / 0.04) 0%, transparent 70%)',
        }}
      />

      <div className="w-full max-w-sm relative z-10">
        {/* Brand */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 bg-primary rounded-xl mb-4 cat-pulse-ring">
            <span className="text-primary-foreground font-black text-xl tracking-tighter">CAT</span>
          </div>
          <h1 className="text-2xl font-black text-foreground tracking-tight">CO-PILOT</h1>
          <p className="text-xs text-muted-foreground mt-1.5 tracking-widest uppercase">
            Industrial Operating System
          </p>
          <p className="text-[11px] text-muted-foreground/60 mt-3">
            Intelligent Heavy Machinery · Real-Time Operations
          </p>
        </div>

        {/* Card */}
        <div className="glass-card-raised p-7">
          <p className="text-xs text-muted-foreground mb-5 uppercase tracking-wider font-semibold">
            Sign in to continue
          </p>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="data-label block mb-1.5">Username</label>
              <div className="relative">
                <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="w-full bg-input border border-border rounded-md pl-9 pr-3 py-2.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary transition-colors placeholder:text-muted-foreground/40"
                  placeholder="operator / supervisor / admin"
                  required
                />
              </div>
            </div>

            <div>
              <label className="data-label block mb-1.5">Password</label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full bg-input border border-border rounded-md pl-9 pr-3 py-2.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary transition-colors placeholder:text-muted-foreground/40"
                  placeholder="••••••••"
                  required
                />
              </div>
            </div>

            {error && (
              <div className="flex items-center gap-2 text-status-critical text-sm bg-status-critical/10 p-3 rounded-md border border-status-critical/20">
                <AlertCircle className="w-4 h-4 shrink-0" />
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-primary text-primary-foreground font-bold py-3 px-4 rounded-md hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed text-sm tracking-wide mt-1"
            >
              {loading ? 'Authenticating…' : 'Sign In'}
            </button>
          </form>

          <div className="mt-5 pt-4 border-t border-border/40">
            <p className="text-[10px] text-muted-foreground/50 text-center">
              Demo: operator · supervisor · admin — password <span className="font-mono">demo123</span>
            </p>
          </div>
        </div>

        <p className="text-center text-[10px] text-muted-foreground/30 mt-4 tracking-wider uppercase">
          CAT Co-Pilot v2.4 · Secured Session
        </p>
      </div>
    </div>
  );
}
