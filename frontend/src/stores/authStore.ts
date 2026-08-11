import { create } from "zustand";
import type { AuthTokens, User } from "@/types/api";

type LoginResponse = AuthTokens & {
  user: User;
};

type RefreshResponse = AuthTokens & {
  user?: User;
};

type AuthState = {
  user: User | null;
  accessToken: string | null;
  refreshToken: string | null;
  isLoading: boolean;
};

type AuthActions = {
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName?: string) => Promise<void>;
  logout: () => Promise<void>;
  setUser: (user: User | null) => void;
  setTokens: (tokens: AuthTokens | null) => void;
  hydrate: () => void;
};

const STORAGE_KEY = "reforge_auth";

function safeParse<T>(raw: string | null): T | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function setIsAuthenticatedCookie(value: "1" | "0") {
  if (typeof document === "undefined") return;
  if (value === "1") {
    document.cookie = "is_authenticated=1; Path=/; SameSite=Lax";
  } else {
    document.cookie =
      "is_authenticated=; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/; SameSite=Lax";
  }
}

function persistAuth(state: Pick<AuthState, "user" | "accessToken" | "refreshToken">) {
  if (typeof window === "undefined") return;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function clearPersistedAuth() {
  if (typeof window === "undefined") return;
  localStorage.removeItem(STORAGE_KEY);
}

export const useAuthStore = create<AuthState & AuthActions>((set, get) => ({
  user: null,
  accessToken: null,
  refreshToken: null,
  isLoading: false,

  hydrate: () => {
    if (typeof window === "undefined") return;
    const data = safeParse<{
      user: User | null;
      accessToken: string | null;
      refreshToken: string | null;
    }>(localStorage.getItem(STORAGE_KEY));

    if (!data) return;
    set({
      user: data.user ?? null,
      accessToken: data.accessToken ?? null,
      refreshToken: data.refreshToken ?? null
    });

    setIsAuthenticatedCookie(data.accessToken ? "1" : "0");
  },

  setUser: (user) => {
    set({ user });
    const { accessToken, refreshToken } = get();
    persistAuth({ user, accessToken, refreshToken });
  },

  setTokens: (tokens) => {
    const next = tokens
      ? { accessToken: tokens.access_token, refreshToken: tokens.refresh_token }
      : { accessToken: null, refreshToken: null };
    set(next);

    const { user } = get();
    persistAuth({ user, accessToken: next.accessToken, refreshToken: next.refreshToken });
    setIsAuthenticatedCookie(next.accessToken ? "1" : "0");
  },

  login: async (email, password) => {
    set({ isLoading: true });
    try {
      const { api } = await import("@/lib/api");
      const res = await api.post<LoginResponse>("/api/v1/auth/login", {
        email,
        password
      });

      set({
        user: res.user,
        accessToken: res.access_token,
        refreshToken: res.refresh_token
      });

      persistAuth({
        user: res.user,
        accessToken: res.access_token,
        refreshToken: res.refresh_token
      });
      setIsAuthenticatedCookie("1");
    } finally {
      set({ isLoading: false });
    }
  },

  register: async (email, password, displayName) => {
    set({ isLoading: true });
    try {
      const { api } = await import("@/lib/api");
      const res = await api.post<LoginResponse>("/api/v1/auth/register", {
        email,
        password,
        display_name: displayName
      });

      set({
        user: res.user,
        accessToken: res.access_token,
        refreshToken: res.refresh_token
      });

      persistAuth({
        user: res.user,
        accessToken: res.access_token,
        refreshToken: res.refresh_token
      });
      setIsAuthenticatedCookie("1");
    } finally {
      set({ isLoading: false });
    }
  },

  logout: async () => {
    set({ isLoading: true });
    try {
      try {
        const { api } = await import("@/lib/api");
        const refreshToken = get().refreshToken;
        if (refreshToken) {
          await api.post("/api/v1/auth/logout", { refresh_token: refreshToken });
        }
      } catch {
        // best-effort
      }
    } finally {
      set({ user: null, accessToken: null, refreshToken: null, isLoading: false });
      clearPersistedAuth();
      setIsAuthenticatedCookie("0");
    }
  }
}));

export function applyAuthTokensFromRefreshResponse(res: RefreshResponse) {
  useAuthStore.getState().setTokens({
    access_token: res.access_token,
    refresh_token: res.refresh_token
  });
  if (res.user) useAuthStore.getState().setUser(res.user);
}

