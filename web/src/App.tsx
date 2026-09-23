import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './store/AuthContext';
import { MachineProvider } from './store/MachineContext';
import { ProtectedRoute } from './components/ProtectedRoute';
import { useAuth } from './store/AuthContext';
import { Login } from './views/Login';
import { PreStart } from './views/PreStart';
import { Hud } from './views/Hud';
import { IdleHub } from './views/IdleHub';
import { Supervisor } from './views/Supervisor';
import { Admin } from './views/Admin';
import { IncidentPage } from './views/IncidentPage';

/** Sends a signed-in user to their role's home; unauthenticated -> login. */
function RoleHome() {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  if (user.role === 'OPERATOR') return <Navigate to="/operator/prestart" replace />;
  if (user.role === 'SUPERVISOR') return <Navigate to="/supervisor" replace />;
  return <Navigate to="/admin" replace />;
}

function App() {
  return (
    <div className="min-h-screen bg-background text-foreground dark">
      <AuthProvider>
        <MachineProvider>
          <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            
            {/* Operator Routes */}
            <Route element={<ProtectedRoute allowedRoles={['OPERATOR']} />}>
              <Route path="/operator/prestart" element={<PreStart />} />
              <Route path="/operator/hud" element={<Hud />} />
              <Route path="/operator/idle" element={<IdleHub />} />
            </Route>

            {/* Supervisor Routes */}
            <Route element={<ProtectedRoute allowedRoles={['SUPERVISOR']} />}>
              <Route path="/supervisor" element={<Supervisor />} />
            </Route>

            {/* Admin Routes */}
            <Route element={<ProtectedRoute allowedRoles={['ADMIN']} />}>
              <Route path="/admin" element={<Admin />} />
            </Route>

            {/* Incident detail — reachable by both supervisors and admins */}
            <Route element={<ProtectedRoute allowedRoles={['SUPERVISOR', 'ADMIN']} />}>
              <Route path="/supervisor/incidents/:incidentId" element={<IncidentPage />} />
            </Route>

            {/* Catch-all */}
            <Route path="/" element={<RoleHome />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
        </MachineProvider>
      </AuthProvider>
    </div>
  );
}

export default App;
