"use client";

import { motion } from "framer-motion";
import Image from "next/image";
import Link from "next/link";
import type { ContentItem } from "@/types/api";
import { isProbablyVideoUrl } from "@/lib/mediaUrl";
import { CalendarIcon, PencilIcon, RetryIcon, TrashIcon } from "./icons";
import ContentStatusBadge from "@/components/ContentStatusBadge";

type PlatformDot = {
  platform: string;
  status: string;
};

const STATUS_BORDER: Record<string, string> = {
  pending: "border-l-gray-300",
  processing: "border-l-blue-400",
  completed: "border-l-green-400",
  completed_fallback: "border-l-amber-400",
  error_fallback: "border-l-red-400",
  timeout_fallback: "border-l-red-400",
  scheduled: "border-l-violet-400"
};

const PLATFORM_ICONS: Record<string, string> = {
  instagram: "📸",
  twitter: "🐦",
  linkedin: "💼",
  facebook: "👥",
  youtube: "▶️"
};

function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return "—";
  const sec = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const days = Math.floor(hr / 24);
  return `${days}d ago`;
}

export type ContentCardActions = {
  onEdit: (id: string) => void;
  onReschedule: (id: string) => void;
  onDelete: (id: string) => void;
  onRetry: (id: string) => void;
  onToggleSelect: (id: string, checked: boolean) => void;
};

export function ContentCard({
  item,
  selected,
  platforms,
  dragging,
  deleteDisabled,
  actions
}: {
  item: ContentItem;
  selected: boolean;
  platforms: PlatformDot[];
  dragging?: boolean;
  /** When true, the Delete control is disabled (evaluation / demo safety). */
  deleteDisabled?: boolean;
  actions: ContentCardActions;
}) {
  const status = item.status ?? "unknown";

  const normalizedStatus = status === "draft" ? "pending" : status;
  const borderAccent = STATUS_BORDER[normalizedStatus] ?? "border-l-gray-200";

  const platformKeys = Array.from(new Set(platforms.map((p) => (p.platform || "").toLowerCase())));

  const detailHref = `/content/${item.id}`;

  return (
    <motion.div
      layout
      whileHover={!dragging ? { y: -4, transition: { duration: 0.2 } } : undefined}
      className="w-[300px]"
      style={{
        transformOrigin: "center",
        rotate: dragging ? "2deg" : "0deg"
      }}
    >
      <div
        className={`relative flex cursor-pointer flex-col gap-3 rounded-xl border border-gray-100 border-l-4 bg-white p-4 shadow-sm transition-all duration-200 hover:shadow-md ${borderAccent}`}
      >
        {normalizedStatus === "processing" && (
          <div className="pointer-events-none absolute inset-0 overflow-hidden rounded-xl">
            <div className="absolute inset-0 bg-gradient-to-r from-transparent via-blue-50/60 to-transparent animate-shimmer" />
          </div>
        )}

        {normalizedStatus === "completed_fallback" && (
          <div className="absolute right-2 top-2 z-[3]">
            <span className="flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-amber-500" />
            </span>
          </div>
        )}

        <div className="relative z-[2] flex gap-4">
          <div className="relative h-24 w-28 shrink-0 overflow-hidden rounded-lg bg-gray-100">
            <input
              aria-label="Select"
              type="checkbox"
              checked={selected}
              onChange={(e) => actions.onToggleSelect(item.id, e.target.checked)}
              onClick={(e) => e.stopPropagation()}
              className="absolute left-2 top-2 z-10 h-4 w-4 rounded border-gray-300 text-slate-900"
            />
            <Link href={detailHref} className="absolute inset-0 block" aria-label="Open content">
              {item.original_file_url ? (
                isProbablyVideoUrl(item.original_file_url) ? (
                  <video
                    src={item.original_file_url}
                    className="absolute inset-0 h-full w-full object-cover"
                    muted
                    playsInline
                    preload="metadata"
                    aria-hidden
                  />
                ) : (
                  <Image
                    src={item.original_file_url}
                    alt=""
                    fill
                    sizes="112px"
                    className="object-cover"
                    priority={false}
                  />
                )
              ) : (
                <div className="absolute inset-0 ai-shimmer" />
              )}
            </Link>
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-2">
              <Link href={detailHref} className="min-w-0 flex-1">
                <div className="line-clamp-2 text-sm font-semibold leading-snug text-gray-900 hover:underline">
                  {item.title ?? "Untitled"}
                </div>
              </Link>
              <span className="shrink-0">
                <ContentStatusBadge status={normalizedStatus} />
              </span>
            </div>

            <div className="mt-3 flex items-center justify-between gap-2">
              <div className="flex flex-wrap gap-1">
                {platformKeys.length ? (
                  platformKeys.map((p) => {
                    const iconKey = p === "x" ? "twitter" : p;
                    return (
                      <span key={p} className="text-sm" title={p}>
                        {PLATFORM_ICONS[iconKey] ?? "📱"}
                      </span>
                    );
                  })
                ) : (
                  <span className="text-xs text-gray-400">No platforms</span>
                )}
              </div>
              <span className="shrink-0 text-xs text-gray-400" title={item.created_at ?? undefined}>
                {formatRelativeTime(item.created_at)}
              </span>
            </div>
          </div>
        </div>

        <div className="relative z-[2] grid grid-cols-2 gap-2 border-t border-gray-50 pt-3">
          <button
            type="button"
            onClick={() => actions.onEdit(item.id)}
            className="inline-flex items-center justify-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 text-xs font-semibold text-gray-900 hover:bg-gray-50"
          >
            <PencilIcon className="h-4 w-4" />
            Edit
          </button>
          <button
            type="button"
            onClick={() => actions.onReschedule(item.id)}
            className="inline-flex items-center justify-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 text-xs font-semibold text-gray-900 hover:bg-gray-50"
          >
            <CalendarIcon className="h-4 w-4" />
            Reschedule
          </button>
          <button
            type="button"
            disabled={deleteDisabled}
            title={deleteDisabled ? "Delete is disabled in evaluation mode" : undefined}
            onClick={() => actions.onDelete(item.id)}
            className="inline-flex items-center justify-center gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-900 hover:bg-rose-100 disabled:pointer-events-none disabled:opacity-50"
          >
            <TrashIcon className="h-4 w-4" />
            Delete
          </button>
          {status === "failed" ? (
            <button
              type="button"
              onClick={() => actions.onRetry(item.id)}
              className="inline-flex items-center justify-center gap-2 rounded-xl border border-orange-200 bg-orange-50 px-3 py-2 text-xs font-semibold text-orange-900 hover:bg-orange-100"
            >
              <RetryIcon className="h-4 w-4" />
              Retry
            </button>
          ) : (
            <div className="min-h-[38px]" aria-hidden />
          )}
        </div>
      </div>
    </motion.div>
  );
}
