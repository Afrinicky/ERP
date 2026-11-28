/**
 * Session state.
 *
 * The permission list is used to hide controls the actor cannot use. That is a
 * convenience; every operation is checked again in the application service.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { api, ServiceError, setSessionToken, setUnauthenticatedHandler } from '@/api/client';
import type { Actor, Business, Theme } from '@/api/types';

interface SessionState {
  actor: Actor | null;
  business: Business | null;
  theme: Theme | null;
  status: 'resolving' | 'signed-out' | 'signed-in';
  mustChangePassword: boolean;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
  applyTheme: (theme: Theme) => void;
  can: (permission: string) => boolean;
}

const SessionContext = createContext<SessionState | null>(null);

/** Write the active theme into the document, so every component picks it up. */
export function installTheme(theme: Theme): void {
  const root = document.documentElement;
  root.dataset.theme = theme.preset_code;
  for (const [token, value] of Object.entries(theme.colour)) {
    root.style.setProperty(`--colour-${token}`, value);
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [actor, setActor] = useState<Actor | null>(null);
  const [business, setBusiness] = useState<Business | null>(null);
  const [theme, setTheme] = useState<Theme | null>(null);
  const [status, setStatus] = useState<SessionState['status']>('resolving');
  const [mustChangePassword, setMustChangePassword] = useState(false);

  const applyTheme = useCallback((next: Theme) => {
    installTheme(next);
    setTheme(next);
  }, []);

  const loadContext = useCallback(async () => {
    const [loadedBusiness, loadedTheme] = await Promise.all([
      api.business.get(),
      api.appearance.active(),
    ]);
    setBusiness(loadedBusiness);
    installTheme(loadedTheme);
    setTheme(loadedTheme);
  }, []);

  const refresh = useCallback(async () => {
    const current = await api.session.current();
    setActor(current);
    await loadContext();
    setStatus('signed-in');
  }, [loadContext]);

  const clear = useCallback(() => {
    setSessionToken(null);
    setActor(null);
    setBusiness(null);
    setMustChangePassword(false);
    setStatus('signed-out');
  }, []);

  useEffect(() => {
    setUnauthenticatedHandler(clear);
    return () => setUnauthenticatedHandler(null);
  }, [clear]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const current = await api.session.current();
        if (cancelled) return;
        setActor(current);
        await loadContext();
        if (!cancelled) setStatus('signed-in');
      } catch (error) {
        if (cancelled) return;
        if (error instanceof ServiceError && error.isUnauthenticated) {
          setStatus('signed-out');
        } else {
          setStatus('signed-out');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadContext]);

  const signIn = useCallback(
    async (username: string, password: string) => {
      const result = await api.session.signIn(username, password, navigator.userAgent);
      setSessionToken(result.token);
      setActor(result.actor);
      setMustChangePassword(result.must_change_password);
      await loadContext();
      setStatus('signed-in');
    },
    [loadContext],
  );

  const signOut = useCallback(async () => {
    try {
      await api.session.signOut();
    } finally {
      clear();
    }
  }, [clear]);

  const can = useCallback(
    (permission: string) => actor?.permissions.includes(permission) ?? false,
    [actor],
  );

  const value = useMemo<SessionState>(
    () => ({
      actor,
      business,
      theme,
      status,
      mustChangePassword,
      signIn,
      signOut,
      refresh,
      applyTheme,
      can,
    }),
    [actor, business, theme, status, mustChangePassword, signIn, signOut, refresh, applyTheme, can],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const value = useContext(SessionContext);
  if (!value) throw new Error('useSession requires a SessionProvider');
  return value;
}

/** The currency profile of the active business, for formatting figures. */
export function useCurrency() {
  const { business } = useSession();
  return useMemo(
    () => ({
      symbol: business?.currency_symbol ?? '',
      precision: business?.currency_precision ?? 2,
      code: business?.base_currency ?? '',
    }),
    [business],
  );
}
