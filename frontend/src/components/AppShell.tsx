import Link from "next/link";
import { FadeIn } from "@/components/Motion";
import { TopNav } from "@/components/TopNav";
import { isEvaluationMode } from "@/lib/evaluationMode";

export function AppShell({ children }: { children: React.ReactNode }) {
  const explicit = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  const apiDocsUrl = explicit
    ? `${explicit.replace(/\/$/, "")}/docs`
    : "/ingest-reforge/docs";
  const evalMode = isEvaluationMode();

  return (
    <div className="min-h-screen bg-slate-50/80">
      {evalMode ? (
        <div
          role="status"
          aria-live="polite"
          className="border-b border-amber-300/80 bg-gradient-to-r from-amber-50 via-amber-50 to-orange-50 px-4 py-2.5 text-center text-sm font-semibold text-amber-950 shadow-sm"
        >
          Evaluation mode: deletes and disconnects are disabled in the UI; publish with no
          matching connections runs as a dry run only (no external APIs).
        </div>
      ) : null}
      <header className="sticky top-0 z-30 border-b border-slate-200/90 bg-white/90 shadow-sm backdrop-blur-md supports-[backdrop-filter]:bg-white/80">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-4 gap-y-3 px-4 py-3 sm:px-6">
          <Link href="/" className="text-base font-semibold tracking-tight text-slate-900">
            ReForge
          </Link>
          <div className="flex min-w-0 flex-1 items-center justify-end gap-3 sm:gap-4">
            <a
              href={apiDocsUrl}
              className="hidden shrink-0 text-sm font-medium text-slate-600 hover:text-slate-900 sm:inline"
              target="_blank"
              rel="noreferrer"
            >
              API Docs
            </a>
            <TopNav />
          </div>
        </div>
      </header>
      <FadeIn className="min-h-[50vh]">{children}</FadeIn>
      <footer className="mt-16 border-t border-slate-200 bg-white">
        <div className="mx-auto max-w-6xl px-4 py-8 text-sm text-slate-500 sm:px-6">
          © {new Date().getFullYear()} ReForge
        </div>
      </footer>
    </div>
  );
}

