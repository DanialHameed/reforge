"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import type { ContentItem, Paginated } from "@/types/api";
import { AIProcessingState } from "@/components/AIProcessingState";
import { FadeIn, PageTransition, fadeUpItem } from "@/components/Motion";

function StatusPill({ status }: { status: string }) {
  const cls =
    status === "processing"
      ? "bg-blue-50 text-blue-900 border-blue-200"
      : status === "published"
        ? "bg-emerald-50 text-emerald-900 border-emerald-200"
        : status === "scheduled"
          ? "bg-violet-50 text-violet-900 border-violet-200"
          : "bg-slate-100 text-slate-800 border-slate-200";
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-1 text-xs font-semibold ${cls}`}>
      {status}
    </span>
  );
}

export default function ContentPage() {
  const [items, setItems] = useState<ContentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const anyProcessing = useMemo(
    () => items.some((i) => i.status === "processing"),
    [items]
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get<Paginated<ContentItem>>("/api/v1/content?limit=25");
      setItems(res.items ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load content");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** Poll only while something is processing; slow down when the tab is hidden. */
  useEffect(() => {
    if (!anyProcessing) return;
    void load();
    let id: number | null = null;
    const intervalMs = () => (typeof document !== "undefined" && document.hidden ? 12_000 : 3000);
    const arm = () => {
      if (id != null) window.clearInterval(id);
      id = window.setInterval(() => void load(), intervalMs());
    };
    arm();
    document.addEventListener("visibilitychange", arm);
    return () => {
      document.removeEventListener("visibilitychange", arm);
      if (id != null) window.clearInterval(id);
    };
  }, [anyProcessing, load]);

  return (
    <PageTransition>
      <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            Content
          </h1>
          <p className="mt-2 text-slate-600">
            Upload a file, start processing, then review your platform variants.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <motion.button
            whileTap={{ scale: 0.95 }}
            onClick={() => void load()}
            className="bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md hover:bg-slate-50 transition-all duration-200 px-4 py-2 text-sm font-semibold text-slate-900"
          >
            Refresh
          </motion.button>
          <motion.div whileTap={{ scale: 0.95 }}>
            <Link
              href="/content/upload"
              className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800 transition-all duration-200 shadow-sm"
            >
              Upload
            </Link>
          </motion.div>
        </div>
      </div>

      {anyProcessing ? (
        <div className="mt-6">
          <AIProcessingState title="Processing in progress" />
        </div>
      ) : null}

      <FadeIn className="mt-8">
        <motion.div
          variants={fadeUpItem}
          className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm transition-all duration-200 hover:shadow-md"
        >
        <div className="border-b border-slate-200 px-6 py-4 text-sm text-slate-600">
          {loading ? "Loading…" : error ? "Error" : `${items.length} items`}
        </div>

        {error ? (
          <div className="px-6 py-6 text-rose-900">
            <div className="font-semibold">Couldn’t load content</div>
            <div className="mt-1 text-sm text-rose-800">{error}</div>
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {items.map((i) => (
              <motion.div
                key={i.id}
                variants={fadeUpItem}
                className="flex flex-col gap-2 px-6 py-4 md:flex-row md:items-center md:justify-between"
              >
                <div className="min-w-0">
                  <Link
                    href={`/content/${i.id}`}
                    className="truncate font-semibold text-slate-900 hover:underline"
                  >
                    {i.title ?? "Untitled"}
                  </Link>
                  <div className="mt-1 text-sm text-slate-600">
                    {i.updated_at ? new Date(i.updated_at).toLocaleString() : "—"}
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <StatusPill status={i.status} />
                  <motion.div whileTap={{ scale: 0.95 }}>
                    <Link
                      href={`/content/${i.id}`}
                      className="bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md hover:bg-slate-50 transition-all duration-200 px-3 py-2 text-sm font-semibold text-slate-900"
                    >
                      Open
                    </Link>
                  </motion.div>
                </div>
              </motion.div>
            ))}

            {!loading && items.length === 0 ? (
              <div className="px-6 py-10 text-center text-slate-600">
                No content yet.{" "}
                <Link href="/content/upload" className="font-semibold text-slate-900 hover:underline">
                  Upload your first file
                </Link>
                .
              </div>
            ) : null}
          </div>
        )}
        </motion.div>
      </FadeIn>
      </main>
    </PageTransition>
  );
}

