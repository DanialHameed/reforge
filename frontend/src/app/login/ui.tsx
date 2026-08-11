"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { motion } from "framer-motion";
import { useAuth } from "@/hooks/useAuth";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Card, CardContent } from "@/components/ui/Card";

export default function LoginClient() {
  const router = useRouter();
  const params = useSearchParams();
  const next = useMemo(() => params.get("next") ?? "/dashboard", [params]);

  const { login, isLoading } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await login(email, password);
      router.replace(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    }
  }

  return (
    <main className="mx-auto flex min-h-[calc(100vh-160px)] max-w-6xl items-center px-4 py-12 sm:px-6">
      <div className="grid w-full grid-cols-1 gap-10 lg:grid-cols-2">
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: [0.2, 0.8, 0.2, 1] }}
          className="flex flex-col justify-center"
        >
          <div className="inline-flex w-fit items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1 text-xs text-slate-600">
            <span className="h-2 w-2 rounded-full bg-gradient-to-r from-emerald-500 to-blue-500" />
            Secure access • Token refresh • Protected routes
          </div>
          <h1 className="mt-4 text-3xl font-semibold tracking-tight text-slate-900">
            Welcome back
          </h1>
          <p className="mt-3 text-slate-600">
            Sign in to continue building content with a premium AI workflow.
          </p>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.08, ease: [0.2, 0.8, 0.2, 1] }}
          className="ai-gradient-border bg-white/70 p-[1px]"
        >
          <div className="rounded-2xl bg-white p-6 shadow-sm">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold text-slate-900">Login</h2>
              <span className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-600">
                /login
              </span>
            </div>

            <form onSubmit={onSubmit} className="mt-6 space-y-4">
              <div>
                <label className="text-sm font-medium text-slate-700">Email</label>
                <Input
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  type="email"
                  autoComplete="email"
                  required
                  className="mt-1"
                  placeholder="you@company.com"
                />
              </div>
              <div>
                <label className="text-sm font-medium text-slate-700">Password</label>
                <Input
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  type="password"
                  autoComplete="current-password"
                  required
                  className="mt-1"
                  placeholder="••••••••"
                />
              </div>

              {error ? (
                <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
                  {error}
                </div>
              ) : null}

              <Button type="submit" disabled={isLoading} variant="primary" size="lg" className="w-full">
                <span className="relative z-10">{isLoading ? "Signing in…" : "Sign in"}</span>
                <span className="pointer-events-none absolute inset-0 opacity-0 transition group-hover:opacity-100">
                  <span className="absolute inset-0 bg-gradient-to-r from-emerald-500/20 via-blue-500/20 to-fuchsia-500/20" />
                </span>
              </Button>
            </form>

            <div className="mt-5 text-sm text-slate-600">
              New here?{" "}
              <Link href="/register" className="font-medium text-slate-900 hover:underline">
                Create an account
              </Link>
            </div>
          </div>
        </motion.div>
      </div>
    </main>
  );
}

