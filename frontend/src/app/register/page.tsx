"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { motion } from "framer-motion";
import { useAuthStore } from "@/stores/authStore";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

export default function RegisterPage() {
  const router = useRouter();
  const { register, isLoading } = useAuthStore();

  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await register(email, password, displayName || undefined);
      router.replace("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    }
  }

  return (
    <main className="mx-auto flex min-h-[calc(100vh-160px)] max-w-6xl items-center px-4 py-12 sm:px-6">
      <div className="mx-auto w-full max-w-lg">
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: [0.2, 0.8, 0.2, 1] }}
          className="ai-gradient-border bg-white/70 p-[1px]"
        >
          <div className="rounded-2xl bg-white p-6 shadow-sm">
            <div className="flex items-center justify-between">
              <h1 className="text-xl font-semibold tracking-tight text-slate-900">
                Create your account
              </h1>
              <span className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-600">
                /register
              </span>
            </div>

            <p className="mt-2 text-sm text-slate-600">
              Start with a clean, premium workflow and let ReForge do the heavy lifting.
            </p>

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
                <label className="text-sm font-medium text-slate-700">
                  Display name <span className="text-slate-400">(optional)</span>
                </label>
                <Input
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  type="text"
                  autoComplete="nickname"
                  className="mt-1"
                  placeholder="Tech"
                />
              </div>

              <div>
                <label className="text-sm font-medium text-slate-700">Password</label>
                <Input
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={8}
                  className="mt-1"
                  placeholder="Minimum 8 characters"
                />
              </div>

              {error ? (
                <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
                  {error}
                </div>
              ) : null}

              <Button type="submit" disabled={isLoading} variant="primary" size="lg" className="w-full">
                {isLoading ? "Creating account…" : "Create account"}
              </Button>
            </form>

            <div className="mt-5 text-sm text-slate-600">
              Already have an account?{" "}
              <Link href="/login" className="font-medium text-slate-900 hover:underline">
                Sign in
              </Link>
            </div>
          </div>
        </motion.div>
      </div>
    </main>
  );
}

