import { Suspense } from "react";
import ConnectionsClient from "./ui";

export default function ConnectionsPage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
          <div className="rounded-2xl border border-slate-200 bg-slate-50 p-6 text-slate-700">
            Loading…
          </div>
        </main>
      }
    >
      <ConnectionsClient />
    </Suspense>
  );
}

