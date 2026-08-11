import { Suspense } from "react";
import LoginClient from "./ui";

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto flex min-h-[calc(100vh-160px)] max-w-6xl items-center px-4 py-12 sm:px-6">
          <div className="w-full rounded-2xl border border-slate-200 bg-slate-50 p-6 text-slate-700">
            Loading…
          </div>
        </main>
      }
    >
      <LoginClient />
    </Suspense>
  );
}

