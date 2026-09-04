import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { CheckCircle2, CircleAlert, Info, X } from 'lucide-react';
import { api, AUTH_SESSION_EVENT } from '@/lib/api';
import type { Session } from '@/types';

interface AuthContextValue {
  session: Session | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => api.auth.session());
  useEffect(() => {
    const syncSession = () => setSession(api.auth.session());
    window.addEventListener(AUTH_SESSION_EVENT, syncSession);
    window.addEventListener('storage', syncSession);
    return () => {
      window.removeEventListener(AUTH_SESSION_EVENT, syncSession);
      window.removeEventListener('storage', syncSession);
    };
  }, []);
  const login = useCallback(async (email: string, password: string) => { const next = await api.auth.login(email, password); setSession(next); }, []);
  const logout = useCallback(async () => { await api.auth.logout(); setSession(null); }, []);
  const value = useMemo(() => ({ session, login, logout }), [session, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside AuthProvider');
  return value;
}

type ToastTone = 'success' | 'error' | 'info';
interface ToastItem { id: number; message: string; tone: ToastTone }
interface ToastContextValue { pushToast: (message: string, tone?: ToastTone) => void }
const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const pushToast = useCallback((message: string, tone: ToastTone = 'success') => {
    const next = { id: Date.now() + Math.random(), message, tone }; setItems((current) => [...current, next]);
    window.setTimeout(() => setItems((current) => current.filter((item) => item.id !== next.id)), 3600);
  }, []);
  return <ToastContext.Provider value={{ pushToast }}>
    {children}
    <div className="toast-stack" aria-live="polite">
      {items.map((item) => <div key={item.id} className={`toast toast--${item.tone}`}>
        {item.tone === 'success' ? <CheckCircle2 /> : item.tone === 'error' ? <CircleAlert /> : <Info />}
        <span>{item.message}</span><button onClick={() => setItems((current) => current.filter((toast) => toast.id !== item.id))} aria-label="Dismiss"><X /></button>
      </div>)}
    </div>
  </ToastContext.Provider>;
}

export function useToast(): ToastContextValue {
  const value = useContext(ToastContext);
  if (!value) throw new Error('useToast must be used inside ToastProvider');
  return value;
}
