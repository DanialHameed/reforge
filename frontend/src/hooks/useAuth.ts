"use client";

import { useEffect } from "react";
import { useAuthStore } from "@/stores/authStore";

let hydrated = false;

export function useAuth() {
  const { user, accessToken, isLoading, login, logout, hydrate } = useAuthStore();

  useEffect(() => {
    if (hydrated) return;
    hydrated = true;
    hydrate();
  }, [hydrate]);

  return {
    user,
    isAuthenticated: Boolean(accessToken),
    isLoading,
    login,
    logout
  };
}

