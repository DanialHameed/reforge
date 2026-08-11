"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/hooks/useAuth";

/**
 * Root landing page.
 *
 * Demo polish (D1): the previous landing was a developer-facing API
 * starter card with instructions like "Connect external AI providers in
 * backend/app/services/...", which is the wrong first impression for an
 * evaluator. We now route them straight into the product:
 *
 *   * Authenticated → ``/dashboard``
 *   * Not yet authenticated → ``/login``
 *
 * The visible body is a polite splash that renders only for the brief
 * moment between "auth state hydrating" and the redirect firing, and as
 * a graceful fallback if JS is disabled (the inline links still work).
 */
export default function HomePage() {
  const router = useRouter();
  const { isAuthenticated, isLoading } = useAuth();

  useEffect(() => {
    if (isLoading) return;
    router.replace(isAuthenticated ? "/dashboard" : "/login");
  }, [isAuthenticated, isLoading, router]);

  return (
    <main className="mx-auto flex min-h-[calc(100vh-160px)] max-w-3xl flex-col items-center justify-center px-4 py-16 text-center sm:px-6">
      <div className="rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
        <h1 className="text-3xl font-semibold tracking-tight text-slate-900">ReForge</h1>
        <p className="mt-3 text-slate-600">
          AI-powered content automation. Upload once, publish everywhere.
        </p>
        <p className="mt-6 text-sm text-slate-500">
          Taking you to the right place…
        </p>
        <div className="mt-6 flex items-center justify-center gap-3">
          <Link
            href="/login"
            className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800"
          >
            Sign in
          </Link>
          <Link
            href="/register"
            className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-900 hover:bg-slate-50"
          >
            Create account
          </Link>
        </div>
      </div>
    </main>
  );
}
