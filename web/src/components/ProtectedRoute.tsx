import { Navigate, Outlet } from 'react-router-dom';
import { useAuth } from '../store/AuthContext';
import type { UserRole } from '../store/AuthContext';

interface ProtectedRouteProps {
  allowedRoles?: UserRole[];
}

export function ProtectedRoute({ allowedRoles }: ProtectedRouteProps) {
  const { token, user } = useAuth();

  if (!token || !user) {
    return <Navigate to="/login" replace />;
  }

  if (allowedRoles && !allowedRoles.includes(user.role)) {
    // If authenticated but wrong role, redirect based on their role
    if (user.role === 'OPERATOR') return <Navigate to="/operator/prestart" replace />;
    if (user.role === 'SUPERVISOR') return <Navigate to="/supervisor" replace />;
    if (user.role === 'ADMIN') return <Navigate to="/admin" replace />;
    return <Navigate to="/" replace />;
  }

  return <Outlet />;
}
