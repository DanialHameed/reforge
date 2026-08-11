"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useRef, useState } from "react";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import type { ContentItem } from "@/types/api";
import { AIProcessingState } from "@/components/AIProcessingState";
import { FadeIn, PageTransition, fadeUpItem } from "@/components/Motion";

export default function UploadContentPage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement | null>(null);

  const [title, setTitle] = useState("");
  const [uploading, setUploading] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onUpload(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    const file = fileRef.current?.files?.[0];
    if (!file) {
      setError("Please choose a file.");
      return;
    }

    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      if (title.trim()) form.append("title", title.trim());

      const item = await apiClientUpload(form);
      setUploading(false);

      setProcessing(true);
      await api.post(`/api/v1/content/${item.id}/process`, {});
      router.replace(`/content/${item.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      setProcessing(false);
    }
  }

  async function apiClientUpload(form: FormData): Promise<ContentItem> {
    const { apiClient } = await import("@/lib/api");
    // Let axios set multipart boundary — a bare "multipart/form-data" header breaks parsing and yields 500s.
    const res = await apiClient.post<ContentItem>("/api/v1/content/upload", form);
    return res.data;
  }

  return (
    <PageTransition>
      <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            Upload
          </h1>
          <p className="mt-2 text-slate-600">
            Upload an image or video to generate platform-ready variants.
          </p>
        </div>
        <motion.div whileTap={{ scale: 0.95 }}>
          <Link
            href="/content"
            className="bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md hover:bg-slate-50 transition-all duration-200 px-4 py-2 text-sm font-semibold text-slate-900"
          >
            Back
          </Link>
        </motion.div>
      </div>

      {processing ? (
        <div className="mt-6">
          <AIProcessingState title="Starting processing" />
        </div>
      ) : null}

      <FadeIn className="mt-8">
        <motion.div variants={fadeUpItem} className="ai-gradient-border bg-white/70 p-[1px]">
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md transition-all duration-200 p-6">
          <form onSubmit={onUpload} className="space-y-4">
            <div>
              <label className="text-sm font-medium text-slate-700">
                Title <span className="text-slate-400">(optional)</span>
              </label>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-slate-900 outline-none ring-blue-500/20 focus:ring-4"
                placeholder="e.g. Product demo video"
              />
            </div>

            <div>
              <label className="text-sm font-medium text-slate-700">File</label>
              <input
                ref={fileRef}
                type="file"
                accept="image/*,video/*"
                required
                className="mt-1 block w-full cursor-pointer rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 file:mr-4 file:rounded-lg file:border-0 file:bg-slate-900 file:px-3 file:py-2 file:text-sm file:font-semibold file:text-white hover:file:bg-slate-800"
              />
              <div className="mt-2 text-xs text-slate-500">
                Supported: images/videos. (Backend max: 100MB images, 2GB videos.)
              </div>
            </div>

            {error ? (
              <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
                {error}
              </div>
            ) : null}

            <motion.button
              whileTap={{ scale: 0.95 }}
              disabled={uploading || processing}
              className="group relative w-full overflow-hidden rounded-xl bg-slate-900 px-4 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800 disabled:opacity-60"
              type="submit"
            >
              <span className="relative z-10">
                {uploading ? "Uploading…" : processing ? "Processing…" : "Upload & Process"}
              </span>
              <span className="pointer-events-none absolute inset-0 opacity-0 transition group-hover:opacity-100">
                <span className="absolute inset-0 bg-gradient-to-r from-emerald-500/20 via-blue-500/20 to-fuchsia-500/20" />
              </span>
            </motion.button>
          </form>
          </div>
        </motion.div>
      </FadeIn>
      </main>
    </PageTransition>
  );
}

