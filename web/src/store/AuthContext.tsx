import { createContext, useContext, useState, useEffect } from 'react';
import type { ReactNode } from 'react';

export type UserRole = "OPERATOR" | "SUPERVISOR" | "ADMIN";

export interface User {
  sub: string;
  role: UserRole;
}

interface AuthContextType {
  token: string | null;
  user: User | null;
  login: (token: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

function parseJwt(token: string): any {
  try {
    const base64Url = token.split('.')[1];
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
    const jsonPayload = decodeURIComponent(atob(base64).split('').map(function(c) {
        return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
    }).join(''));
    return JSON.parse(jsonPayload);
  } catch {
    return null;
  }
}

/** Synchronous so the FIRST render already has `user` set from a stored
 *  token — deriving it only in a useEffect meant ProtectedRoute saw
 *  `token` truthy but `user` still null on that first render (before the
 *  effect ran) and immediately redirected to /login, on every hard
 *  page load / direct URL open, even with a perfectly valid session. */
function deriveUser(token: string | null): User | null {
  if (!token) return null;
  const payload = parseJwt(token);
  if (payload && payload.exp * 1000 > Date.now()) {
    return { sub: payload.sub, role: payload.role as UserRole };
  }
  return null;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('jwt_token'));
  const [user, setUser] = useState<User | null>(() => deriveUser(localStorage.getItem('jwt_token')));

  useEffect(() => {
    const derived = deriveUser(token);
    if (token && !derived) {
      logout();  // token present but expired/invalid
    } else {
      setUser(derived);
    }
  }, [token]);

  useEffect(() => {
    const handleUnauthorized = () => {
      logout();
    };
    window.addEventListener('auth:unauthorized', handleUnauthorized);
    return () => window.removeEventListener('auth:unauthorized', handleUnauthorized);
  }, []);

  const login = (newToken: string) => {
    localStorage.setItem('jwt_token', newToken);
    setToken(newToken);
  };

  const logout = () => {
    localStorage.removeItem('jwt_token');
    setToken(null);
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ token, user, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
