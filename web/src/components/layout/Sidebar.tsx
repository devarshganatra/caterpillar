import { NavLink, useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../store/AuthContext';
import {
  ClipboardCheck, Monitor, Coffee,
  Users, AlertOctagon,
  Shield, LogOut, Zap,
} from 'lucide-react';
import { cn } from '../../lib/utils';

/** Role-aware sidebar navigation. No latency/RTT display — only real connection
 *  status is available from the WS hook, and that's shown in the HUD header. */
export function Sidebar() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  return (
    <aside className="w-[220px] shrink-0 flex flex-col h-screen bg-[#080d1a] border-r border-border/60 overflow-y-auto intel-scroll">
      {/* Logo */}
      <div className="px-4 pt-5 pb-4 border-b border-border/40">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 bg-primary rounded flex items-center justify-center shrink-0">
            <span className="text-primary-foreground font-black text-xs tracking-tighter">CAT</span>
          </div>
          <div>
            <div className="font-black text-foreground text-sm tracking-wide leading-none">CO-PILOT</div>
            <div className="text-[9px] text-muted-foreground tracking-widest uppercase mt-0.5 leading-none">OS 2.4</div>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-2 py-3 space-y-0.5">
        {user?.role === 'OPERATOR' && (
          <>
            <div className="sidebar-section-label">Operator Role</div>
            <NavLink
              to="/operator/prestart"
              className={({ isActive }) => cn('sidebar-item', isActive && 'active')}
            >
              <ClipboardCheck className="w-4 h-4 shrink-0" />
              /operator/prestart
            </NavLink>
            <NavLink
              to="/operator/hud"
              className={({ isActive }) => cn('sidebar-item', isActive && 'active')}
            >
              <Monitor className="w-4 h-4 shrink-0" />
              /operator/hud
            </NavLink>
            <NavLink
              to="/operator/idle"
              className={({ isActive }) => cn('sidebar-item', isActive && 'active')}
            >
              <Coffee className="w-4 h-4 shrink-0" />
              /operator/idle
            </NavLink>
          </>
        )}

        {user?.role === 'SUPERVISOR' && (
          <>
            <div className="sidebar-section-label">Supervisor Role</div>
            <NavLink
              to="/supervisor"
              end
              className={({ isActive }) => cn('sidebar-item', isActive && 'active')}
            >
              <Users className="w-4 h-4 shrink-0" />
              /supervisor
            </NavLink>
            <NavLink
              to="/supervisor"
              // Function form is required here: react-router's NavLink
              // auto-appends "active" based on its OWN `to`-prefix match
              // whenever className is a plain string (only the function
              // form replaces that entirely) — with both links pointing
              // at /supervisor, the string form left this permanently
              // "active" regardless of the condition below.
              className={() => cn('sidebar-item', location.pathname.includes('/incidents') && 'active')}
            >
              <AlertOctagon className="w-4 h-4 shrink-0" />
              /supervisor/incidents
            </NavLink>
          </>
        )}

        {user?.role === 'ADMIN' && (
          <>
            <div className="sidebar-section-label">Admin & Governance</div>
            <NavLink
              to="/admin"
              className={({ isActive }) => cn('sidebar-item', isActive && 'active')}
            >
              <Shield className="w-4 h-4 shrink-0" />
              /admin
            </NavLink>
          </>
        )}

        {/* Disabled aspirational items — clearly labeled as future */}
        <div className="sidebar-section-label mt-4 opacity-40">Future (disabled)</div>
        <div className={cn('sidebar-item opacity-30 cursor-not-allowed pointer-events-none')}>
          <Zap className="w-4 h-4 shrink-0" />
          <span>Operator Sim &amp; Training</span>
          <span className="ml-auto text-[9px] bg-secondary px-1 rounded font-mono">v2.6</span>
        </div>
      </nav>

      {/* User info at bottom */}
      <div className="border-t border-border/40 px-3 py-3">
        <div className="flex items-center gap-2.5 mb-2">
          <div className="w-7 h-7 rounded-full bg-primary/20 border border-primary/30 flex items-center justify-center shrink-0">
            <span className="text-primary font-bold text-xs">
              {user?.role?.[0] ?? '?'}
            </span>
          </div>
          <div className="min-w-0">
            <div className="text-xs font-semibold truncate capitalize">
              {user?.role?.toLowerCase() ?? 'unknown'}
            </div>
            <div className="text-[10px] text-muted-foreground truncate font-mono">
              {user?.sub?.slice(0, 8) ?? '—'}
            </div>
          </div>
        </div>
        <button
          onClick={handleLogout}
          className="flex items-center gap-2 w-full text-xs text-muted-foreground hover:text-status-critical transition-colors py-1 px-1 rounded"
        >
          <LogOut className="w-3.5 h-3.5" />
          Sign out
        </button>
      </div>
    </aside>
  );
}
