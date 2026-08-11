import axios, { AxiosError, type AxiosInstance, type AxiosRequestConfig } from "axios";
import { applyAuthTokensFromRefreshResponse, useAuthStore } from "@/stores/authStore";
import type { AuthTokens } from "@/types/api";

declare module "axios" {
  export interface AxiosRequestConfig {
    _retry?: boolean;
    _skipAuth?: boolean;
  }
}

/** Same-origin tunnel; `next.config.mjs` rewrites → `BACKEND_INTERNAL_URL` (default http://127.0.0.1:8000). */
const INGEST_PROXY = "/ingest-reforge";

function resolveApiBaseURL(): string {
  const fromPublic = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  if (fromPublic) return fromPublic.replace(/\/$/, "");
  if (typeof window !== "undefined") return INGEST_PROXY;
  return "http://127.0.0.1:8000";
}

export const apiClient: AxiosInstance = axios.create({
  headers: { "content-type": "application/json" },
});

let refreshPromise: Promise<AuthTokens> | null = null;

function redirectToLogin() {
  if (typeof window === "undefined") return;
  window.location.assign("/login");
}

apiClient.interceptors.request.use((config) => {
  config.baseURL = resolveApiBaseURL();
  return config;
});

apiClient.interceptors.request.use((config) => {
  if (config._skipAuth) return config;
  const token = useAuthStore.getState().accessToken;
  if (token) {
    config.headers = config.headers ?? {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Default instance header is application/json — that breaks multipart uploads (422: missing form fields).
apiClient.interceptors.request.use((config) => {
  if (typeof FormData === "undefined" || !(config.data instanceof FormData)) return config;
  const h = config.headers;
  if (!h) return config;
  if (typeof (h as AxiosHeadersMut).delete === "function") {
    (h as AxiosHeadersMut).delete?.("Content-Type");
    (h as AxiosHeadersMut).delete?.("content-type");
  }
  Reflect.deleteProperty(h as object, "Content-Type");
  Reflect.deleteProperty(h as object, "content-type");
  return config;
});

type AxiosHeadersMut = {
  delete?: (name: string) => unknown;
};

apiClient.interceptors.response.use(
  (res) => res,
  async (error: AxiosError) => {
    const status = error.response?.status;
    const originalRequest = error.config as AxiosRequestConfig | undefined;

    if (!originalRequest || status !== 401 || originalRequest._retry) {
      throw error;
    }

    const refreshToken = useAuthStore.getState().refreshToken;
    if (!refreshToken) {
      await useAuthStore.getState().logout();
      redirectToLogin();
      throw error;
    }

    originalRequest._retry = true;

    try {
      if (!refreshPromise) {
        refreshPromise = apiClient
          .post<AuthTokens>(
            "/api/v1/auth/refresh",
            { refresh_token: refreshToken },
            { _skipAuth: true }
          )
          .then((r) => r.data)
          .finally(() => {
            refreshPromise = null;
          });
      }

      const tokens = await refreshPromise;
      applyAuthTokensFromRefreshResponse(tokens);

      return await apiClient(originalRequest);
    } catch (e) {
      await useAuthStore.getState().logout();
      redirectToLogin();
      throw e;
    }
  }
);

export const api = {
  get: async <T>(path: string, config?: AxiosRequestConfig): Promise<T> => {
    const res = await apiClient.get<T>(path, config);
    return res.data;
  },
  post: async <T>(
    path: string,
    json?: unknown,
    config?: AxiosRequestConfig
  ): Promise<T> => {
    const res = await apiClient.post<T>(path, json, config);
    return res.data;
  },
  patch: async <T>(
    path: string,
    json?: unknown,
    config?: AxiosRequestConfig
  ): Promise<T> => {
    const res = await apiClient.patch<T>(path, json, config);
    return res.data;
  }
};

