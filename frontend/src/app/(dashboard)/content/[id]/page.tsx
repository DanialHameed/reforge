"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { motion } from "framer-motion";
import { AIProcessingState } from "@/components/AIProcessingState";
import Image from "next/image";
import { isProbablyVideoUrl } from "@/lib/mediaUrl";
import { FadeIn, PageTransition, fadeUpItem } from "@/components/Motion";
import { useContentPolling, type ContentData, fetchContentData } from "@/hooks/useContentPolling";
import { api } from "@/lib/api";
import { useToast } from "@/components/ui/Toast";
import ContentStatusBadge from "@/components/ContentStatusBadge";
import {
  FacebookLogo,
  InstagramLogo,
  LinkedInLogo,
  XLogo,
  YouTubeLogo
} from "@/components/connections/platformLogos";

const PLATFORM_COLORS: Record<string, { bg: string; border: string }> = {
  instagram: {
    bg: "bg-gradient-to-br from-purple-50 to-pink-50",
    border: "border-purple-100"
  },
  twitter: {
    bg: "bg-blue-50",
    border: "border-blue-100"
  },
  linkedin: {
    bg: "bg-sky-50",
    border: "border-sky-100"
  },
  facebook: {
    bg: "bg-indigo-50",
    border: "border-indigo-100"
  },
  youtube: {
    bg: "bg-red-50",
    border: "border-red-100"
  }
};

function PlatformBrandMark({ platform }: { platform: string }) {
  const raw = String(platform || "").toLowerCase();
  const key = raw === "x" ? "twitter" : raw;
  const shell =
    "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-white/80 bg-white/95 shadow-sm";
  if (key === "youtube") {
    return (
      <div className={shell}>
        <YouTubeLogo className="h-6 w-6 text-red-600" />
      </div>
    );
  }
  if (key === "facebook") {
    return (
      <div className={shell}>
        <FacebookLogo className="h-6 w-6 text-[#1877F2]" />
      </div>
    );
  }
  if (key === "linkedin") {
    return (
      <div className={shell}>
        <LinkedInLogo className="h-6 w-6 text-[#0A66C2]" />
      </div>
    );
  }
  if (key === "twitter") {
    return (
      <div className={shell}>
        <XLogo className="h-6 w-6 text-slate-900" />
      </div>
    );
  }
  return (
    <div className={shell}>
      <InstagramLogo className="h-6 w-6" />
    </div>
  );
}

type PlatformPreviewVariant = NonNullable<ContentData["platform_variants"]>[number];

function variantCaptionEditKey(platform: string | null | undefined): string {
  const p = String(platform ?? "").toLowerCase();
  return p === "x" ? "twitter" : p || "_unknown";
}

function captionMappedText(
  v: PlatformPreviewVariant,
  platforms?: ContentData["platforms"]
): string {
  const rawPlatform = String(v.platform || "").toLowerCase();
  const mapped =
    rawPlatform === "youtube"
      ? platforms?.youtube?.title
      : rawPlatform === "instagram"
        ? platforms?.instagram?.caption
        : rawPlatform === "twitter" || rawPlatform === "x"
          ? platforms?.twitter?.tweet
          : rawPlatform === "linkedin"
            ? platforms?.linkedin?.post
            : rawPlatform === "facebook"
              ? platforms?.facebook?.post
              : undefined;
  return String(mapped ?? v.caption ?? "").trim();
}

type PlatformPreviewCardProps = {
  v: PlatformPreviewVariant;
  platforms?: ContentData["platforms"];
  contentId: string;
  onPublishYouTube: (contentId: string) => Promise<void>;
  fallbackPublishBlocked: boolean;
  captionEditKey: string;
  isEditingCaption: boolean;
  editedContentRow: Record<string, unknown> | undefined;
  captionSaving: boolean;
  isProcessing: boolean;
  onValidateMedia: (variantId: string) => Promise<void>;
  onBeginCaptionDraft: (variant: PlatformPreviewVariant, baseline: string) => void;
  onCaptionDraftChange: (platformKey: string, text: string) => void;
  onSaveCaptionEdit: (platformKey: string) => void;
  onCancelCaptionEdit: () => void;
};

function PlatformPreviewCard({
  v,
  platforms,
  contentId,
  onPublishYouTube,
  fallbackPublishBlocked,
  captionEditKey,
  isEditingCaption,
  editedContentRow,
  captionSaving,
  isProcessing,
  onValidateMedia,
  onBeginCaptionDraft,
  onCaptionDraftChange,
  onSaveCaptionEdit,
  onCancelCaptionEdit
}: PlatformPreviewCardProps) {
  const rawPlatform = String(v.platform || "").toLowerCase();
  const colorKey = rawPlatform === "x" ? "twitter" : rawPlatform;
  const colors = PLATFORM_COLORS[colorKey] ?? PLATFORM_COLORS.instagram;

  const captionText = captionMappedText(v, platforms) || "No caption yet.";
  const descriptionYoutube =
    rawPlatform === "youtube" ? String(platforms?.youtube?.description ?? "").trim() : "";
  const metadata = (v.metadata ?? {}) as Record<string, unknown>;
  const mediaAutofix = Boolean(metadata["media_autofix"]);
  const mediaAutofixNote =
    mediaAutofix ? String(metadata["media_autofix_note"] ?? "").trim() : "";

  const tagPills =
    rawPlatform === "youtube"
      ? (platforms?.youtube?.tags ?? []).map((t) => (String(t).startsWith("#") ? String(t) : `#${String(t)}`))
      : (v.hashtags ?? []).slice(0, 12).map((h) => (h.startsWith("#") ? h : `#${h.replace(/^#/, "")}`));

  const showYouTubePublish =
    rawPlatform === "youtube" && v.status === "scheduled" && !fallbackPublishBlocked;

  const draftLine =
    (editedContentRow?.caption as string | undefined) ??
    (editedContentRow?.tweet as string | undefined) ??
    (editedContentRow?.post as string | undefined) ??
    "";

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      whileHover={{ y: -4, transition: { duration: 0.2 } }}
      className="overflow-x-hidden overflow-y-visible"
    >
      <div
        className={`rounded-2xl border ${colors.border} ${colors.bg} shadow-sm transition-shadow duration-200 hover:shadow-md`}
      >
        <div className="flex items-center justify-between gap-3 border-b border-white/60 px-4 py-3">
          <div className="flex min-w-0 items-center gap-3">
            <PlatformBrandMark platform={v.platform ?? ""} />
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <span className="text-sm font-bold capitalize tracking-tight text-gray-900">
                  {v.platform ?? "Platform"}
                </span>
                <span className="text-xs font-medium text-gray-400">Variant</span>
              </div>
            </div>
          </div>
          {showYouTubePublish ? (
            <motion.button
              whileTap={{ scale: 0.95 }}
              onClick={() => void onPublishYouTube(contentId)}
              className="rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-slate-800"
            >
              Publish to YouTube
            </motion.button>
          ) : (
            <ContentStatusBadge status={v.status} />
          )}
        </div>

        <div className="px-4 py-3">
          {v.media_url ? (
            <div className="mb-3 overflow-hidden rounded-xl border border-gray-100 bg-black/5">
              <div className="relative h-40 w-full">
                {isProbablyVideoUrl(v.media_url) ? (
                  <video
                    src={v.media_url}
                    className="h-full w-full object-contain"
                    controls
                    playsInline
                    preload="metadata"
                  />
                ) : (
                  <Image
                    src={v.media_url}
                    alt=""
                    fill
                    sizes="(max-width: 768px) 100vw, 50vw"
                    className="object-cover"
                  />
                )}
              </div>
            </div>
          ) : null}

          {isEditingCaption ? (
            <div className="space-y-2 px-1">
              <textarea
                className="w-full resize-none rounded-lg border border-blue-300 bg-white px-3 py-2 text-sm leading-relaxed text-gray-700 shadow-inner focus:outline-none focus:ring-2 focus:ring-blue-400"
                rows={4}
                value={draftLine}
                disabled={isProcessing || captionSaving}
                onChange={(e) => onCaptionDraftChange(captionEditKey, e.target.value)}
                placeholder="Edit your caption..."
              />
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={isProcessing || captionSaving}
                  onClick={() => void onSaveCaptionEdit(captionEditKey)}
                  className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
                >
                  {captionSaving ? "Saving…" : "Save"}
                </button>
                <button
                  type="button"
                  disabled={captionSaving}
                  onClick={onCancelCaptionEdit}
                  className="rounded-lg bg-gray-100 px-3 py-1.5 text-xs font-semibold text-gray-600 transition-colors hover:bg-gray-200 disabled:opacity-50"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="group relative">
              <div className="min-h-[60px] rounded-lg border border-gray-100 bg-white px-3 py-2.5 text-sm leading-relaxed text-gray-700 shadow-inner">
                {captionText}
              </div>
              <button
                type="button"
                disabled={isProcessing}
                onClick={() => onBeginCaptionDraft(v, captionMappedText(v, platforms))}
                className="absolute right-3 top-2 rounded-md border border-gray-200 bg-white px-2 py-1 text-xs text-gray-500 opacity-0 shadow-sm transition-opacity hover:text-gray-700 group-hover:opacity-100 disabled:pointer-events-none disabled:opacity-0"
              >
                ✏️ Edit
              </button>
            </div>
          )}

          {v.media_url ? (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                type="button"
                disabled={isProcessing}
                onClick={() => void onValidateMedia(String(v.id))}
                className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-900 shadow-sm hover:bg-slate-50 disabled:opacity-60"
              >
                Re-validate media
              </button>
              <span className="text-xs text-slate-500">
                Runs compatibility checks without publishing.
              </span>
            </div>
          ) : null}

          {rawPlatform === "youtube" && descriptionYoutube ? (
            <div className="mt-2 rounded-lg border border-gray-100 bg-white/90 px-3 py-2 text-sm text-gray-600 shadow-inner">
              <div className="whitespace-pre-wrap">{descriptionYoutube}</div>
            </div>
          ) : null}

          {v.error_message ? (
            <div className="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900">
              {String(v.error_message).toLowerCase().startsWith("media validation failed:") ? (
                <div className="space-y-1">
                  <div className="font-semibold">Media incompatible for this platform</div>
                  <div className="text-rose-800">{v.error_message}</div>
                  <div className="text-rose-800/90">
                    Try re-uploading media with a supported aspect ratio/duration, or switch to a different variant type
                    (e.g. image vs video). If your media is on Cloudinary, ReForge may auto-adjust the crop when possible.
                  </div>
                </div>
              ) : (
                v.error_message
              )}
            </div>
          ) : null}

          {mediaAutofix ? (
            <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              <div className="font-semibold">Auto-adjusted media for compatibility</div>
              <div className="mt-1 text-amber-800">
                {mediaAutofixNote
                  ? mediaAutofixNote
                  : "A safe Cloudinary crop/aspect transform was applied to reduce publish-time rejections."}
              </div>
            </div>
          ) : null}
        </div>

        {tagPills.length ? (
          <div className="flex flex-wrap gap-1.5 px-4 pb-4">
            {tagPills.map((tag) => (
              <span
                key={tag}
                className="inline-flex items-center rounded-full border border-gray-200 bg-white px-2.5 py-0.5 text-xs font-medium text-gray-600 shadow-sm"
              >
                {tag}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </motion.div>
  );
}

export default function ContentDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const [item, setItem] = useState<ContentData | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  useContentPolling({
    contentId: id,
    enabled: Boolean(id),
    onComplete: (d) => {
      setItem(d);
      setLoading(false);
    },
    onError: (e) => {
      setError(e.message);
    }
  });
  const { push } = useToast();
  const [publishing, setPublishing] = useState(false);
  const [broadcasting, setBroadcasting] = useState(false);
  const [publishingPoll, setPublishingPoll] = useState(false);
  const [reprocessing, setReprocessing] = useState(false);
  const [selectiveOpen, setSelectiveOpen] = useState(false);
  const [selectedPlatforms, setSelectedPlatforms] = useState<Record<string, boolean>>({
    instagram: true,
    facebook: true,
    linkedin: true,
    x: true,
    youtube: true
  });
  const [editingPlatform, setEditingPlatform] = useState<string | null>(null);
  const [editedContent, setEditedContent] = useState<Record<string, Record<string, unknown>>>({});
  const [captionSavingKey, setCaptionSavingKey] = useState<string | null>(null);
  const [publishDespitePlaceholderAck, setPublishDespitePlaceholderAck] = useState(false);
  const placeholderAckResetKeyRef = useRef<string>("");

  const isProcessing = item?.status === "processing";
  const isPlaceholderFallbackStatus =
    item?.status === "completed_fallback" ||
    item?.status === "error_fallback" ||
    item?.status === "timeout_fallback";

  /** Only reset opt-in when the loaded item identity or lifecycle status actually changes — not on every poll refresh. */
  useEffect(() => {
    const key = `${item?.id ?? ""}:${item?.status ?? ""}`;
    if (!item?.id) return;
    if (placeholderAckResetKeyRef.current === key) return;
    const hadPrev = placeholderAckResetKeyRef.current !== "";
    placeholderAckResetKeyRef.current = key;
    if (!hadPrev) return;
    setPublishDespitePlaceholderAck(false);
  }, [item?.id, item?.status]);

  /** Generic/placeholder captions: require explicit opt-in before publish controls work. */
  const publishBlocked = isPlaceholderFallbackStatus && !publishDespitePlaceholderAck;

  const refreshItem = useCallback(async (): Promise<ContentData | null> => {
    if (!id) return null;
    try {
      const data = await fetchContentData(String(id));
      setItem(data);
      return data;
    } catch {
      return null;
    }
  }, [id]);

  async function validateMedia(variantId: string) {
    try {
      const res = await api.post<{
        ok: boolean;
        platform?: string;
        error?: string;
        autofix_applied?: boolean;
      }>(`/api/v1/platforms/variants/${encodeURIComponent(variantId)}/validate-media`, {});

      if (res.ok) {
        push({
          kind: "success",
          message: res.autofix_applied
            ? "Media is compatible (auto-fix applied)."
            : "Media is compatible for publishing.",
        });
      } else {
        push({
          kind: "error",
          message: res.error ? `Media incompatible: ${res.error}` : "Media incompatible for this platform.",
        });
      }
      await refreshItem();
    } catch (e) {
      push({ kind: "error", message: e instanceof Error ? e.message : "Media validation failed." });
    }
  }

  function isPublishTerminal(vStatus: string | null | undefined): boolean {
    const s = String(vStatus || "").toLowerCase();
    return s === "published" || s === "failed" || s === "assisted" || s === "scheduled";
  }

  useEffect(() => {
    if (!publishingPoll) return;
    let cancelled = false;
    let timeoutId: number | null = null;
    const startedAt = Date.now();

    const clearTimer = () => {
      if (timeoutId != null) {
        clearTimeout(timeoutId);
        timeoutId = null;
      }
    };

    const tick = async () => {
      if (cancelled) return;
      const latest = await refreshItem();
      if (cancelled) return;
      const variants = latest?.platform_variants ?? [];
      const allDone = variants.length > 0 && variants.every((v) => isPublishTerminal(v.status));
      const timedOut = Date.now() - startedAt > 90_000;
      if (allDone || timedOut) {
        setPublishingPoll(false);
        return;
      }
      timeoutId = window.setTimeout(() => void tick(), 2000);
    };

    timeoutId = window.setTimeout(() => void tick(), 1200);
    return () => {
      cancelled = true;
      clearTimer();
    };
  }, [publishingPoll, refreshItem]);

  async function handleReprocess() {
    if (!item?.id || reprocessing) return;
    setReprocessing(true);
    try {
      await api.post(`/api/v1/content/${item.id}/process`, {});
      push({ kind: "success", message: "Re-processing started. This may take a moment." });
    } catch (e) {
      push({ kind: "error", message: e instanceof Error ? e.message : "Re-process failed." });
    } finally {
      setReprocessing(false);
    }
  }

  async function publishYouTube(contentId: string) {
    if (publishing || publishBlocked) return;
    setPublishing(true);
    try {
      const res = await api.post<{
        ok?: boolean;
        status?: string;
        reason?: string;
        youtube_video_id?: string;
        error?: string;
      }>(`/api/v1/platforms/youtube/publish/${contentId}`, {});
      if (res?.status === "blocked" && res.reason) {
        push({ kind: "error", message: res.reason });
      } else if (res?.ok) {
        push({
          kind: "success",
          message:
            "YouTube upload started (video is set to public when complete). Status will update in a few seconds."
        });
        setPublishingPoll(true);
      } else {
        push({ kind: "error", message: res?.error ? `Upload failed: ${res.error}` : "Upload failed." });
      }
    } catch (e) {
      push({ kind: "error", message: e instanceof Error ? e.message : "Upload failed." });
    } finally {
      setPublishing(false);
    }
  }

  async function publishAll(contentId: string) {
    if (broadcasting || publishBlocked) return;
    setBroadcasting(true);
    try {
      const res = await api.post<{
        status?: string;
        message?: string;
        evaluation_mode?: boolean;
        dry_run?: boolean;
        dispatched_to?: string[];
        errors?: Array<{ platform: string; error: string; detail?: string }>;
      }>(`/api/v1/platforms/publish-all/${contentId}`, {
        acknowledge_placeholder_captions: publishDespitePlaceholderAck,
      });
      if (res?.status === "blocked" && res.message) {
        push({ kind: "error", message: res.message });
        return;
      }
      if (res?.evaluation_mode && res?.dry_run) {
        push({
          kind: "info",
          message:
            res.message ??
            "Evaluation mode: publish dry run — no jobs were queued and no external APIs were called.",
        });
        return;
      }
      const dispatched = res?.dispatched_to ?? [];
      const errs = res?.errors ?? [];
      const formatErrLine = (e: { platform: string; error: string; detail?: string }) =>
        `${e.platform}: ${(e.detail && e.detail.trim()) || e.error}`;
      const errJoined = errs.length ? errs.map(formatErrLine).join(" · ") : "";
      if (dispatched.length) {
        push({ kind: "success", message: `Broadcasted to: ${dispatched.join(", ")}` });
        setPublishingPoll(true);
      }
      if (errs.length) {
        push({
          kind: dispatched.length ? "info" : "error",
          message: dispatched.length ? `Some platforms failed: ${errJoined}` : errJoined
        });
      } else if (!dispatched.length) {
        push({ kind: "error", message: "No platforms were published. Check connections and try again." });
      }
    } catch (e) {
      push({ kind: "error", message: e instanceof Error ? e.message : "Broadcast failed." });
    } finally {
      setBroadcasting(false);
    }
  }

  async function publishSelected(contentId: string) {
    if (broadcasting || publishBlocked) return;
    const platforms = Object.entries(selectedPlatforms)
      .filter(([, on]) => on)
      .map(([k]) => k);
    if (!platforms.length) {
      push({ kind: "error", message: "Select at least one platform." });
      return;
    }
    setBroadcasting(true);
    try {
      const res = await api.post<{
        status?: string;
        message?: string;
        evaluation_mode?: boolean;
        dry_run?: boolean;
        dispatched_to?: string[];
        errors?: Array<{ platform: string; error: string; detail?: string }>;
      }>(`/api/v1/platforms/publish-selected/${contentId}`, {
        platforms,
        acknowledge_placeholder_captions: publishDespitePlaceholderAck,
      });
      if (res?.status === "blocked" && res.message) {
        push({ kind: "error", message: res.message });
        return;
      }
      if (res?.evaluation_mode && res?.dry_run) {
        push({
          kind: "info",
          message:
            res.message ??
            "Evaluation mode: publish dry run — no jobs were queued and no external APIs were called.",
        });
        return;
      }
      const dispatched = res?.dispatched_to ?? [];
      const errs = res?.errors ?? [];
      const formatErrLine = (e: { platform: string; error: string; detail?: string }) =>
        `${e.platform}: ${(e.detail && e.detail.trim()) || e.error}`;
      const errJoined = errs.length ? errs.map(formatErrLine).join(" · ") : "";
      if (dispatched.length) push({ kind: "success", message: `Published to: ${dispatched.join(", ")}` });
      if (dispatched.length) setPublishingPoll(true);
      if (errs.length) {
        push({
          kind: dispatched.length ? "info" : "error",
          message: dispatched.length ? `Some platforms failed: ${errJoined}` : errJoined
        });
      } else if (!dispatched.length) {
        push({ kind: "error", message: "Nothing was published. Check connections or media requirements." });
      }
      setSelectiveOpen(false);
    } catch (e) {
      push({ kind: "error", message: e instanceof Error ? e.message : "Publish failed." });
    } finally {
      setBroadcasting(false);
    }
  }

  // Persists caption (and optional hashtags/description) via PATCH /api/v1/content/{id}/variants/{platform}
  async function handleSaveEdit(platform: string) {
    if (!id) return;
    try {
      setCaptionSavingKey(platform);
      await api.patch(`/api/v1/content/${id}/variants/${encodeURIComponent(platform)}`, {
        data: editedContent[platform]
      });
      setEditingPlatform(null);
      const data = await fetchContentData(String(id));
      setItem(data);
      push({ kind: "success", message: "Caption saved." });
    } catch (err) {
      console.error("Failed to save edit:", err);
      push({
        kind: "error",
        message: err instanceof Error ? err.message : "Failed to save caption."
      });
    } finally {
      setCaptionSavingKey(null);
    }
  }

  function handleBeginCaptionDraft(variant: PlatformPreviewVariant, baseline: string) {
    const key = variantCaptionEditKey(variant.platform);
    setEditingPlatform(key);
    setEditedContent((prev) => ({
      ...prev,
      [key]: {
        ...(prev[key] ?? {}),
        ...variant,
        caption: baseline,
        tweet: baseline,
        post: baseline,
        title: baseline,
      },
    }));
  }

  function handleCaptionDraftChange(platformKey: string, text: string) {
    setEditedContent((prev) => ({
      ...prev,
      [platformKey]: {
        ...(prev[platformKey] ?? {}),
        caption: text,
        tweet: text,
        post: text,
        title: text,
      },
    }));
  }

  const banner = useMemo(() => {
    const s = item?.status;
    if (s === "completed_fallback") return null;
    if (s === "error_fallback" || s === "timeout_fallback") {
      return {
        text: "AI generation timed out. Safe fallback content was generated."
      };
    }
    return null;
  }, [item?.status]);

  return (
    <PageTransition>
      <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Link href="/content" className="text-sm font-semibold text-slate-600 hover:text-slate-900">
                ← Content
              </Link>
              {item ? <ContentStatusBadge status={item.status} isFallback={Boolean(item.is_fallback)} /> : null}
            </div>
            <h1 className="mt-3 truncate text-2xl font-semibold tracking-tight text-slate-900">
              {item?.title ?? (loading ? "Loading…" : "Untitled")}
            </h1>
            <div className="mt-2 text-sm text-slate-600">
              {item?.updated_at ? new Date(item.updated_at).toLocaleString() : "—"}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {item ? (
              <motion.button
                whileTap={{ scale: 0.95 }}
                disabled={broadcasting || publishBlocked}
                title={
                  publishBlocked
                    ? "Check the acknowledgement box below to enable publishing (or Re-process)."
                    : item.status === "completed_fallback"
                      ? "Publish to all connected platforms"
                      : item.status === "error_fallback" || item.status === "timeout_fallback"
                        ? "Publish to all connected platforms"
                        : "Publish to all platforms"
                }
                onClick={() => void publishAll(String(item.id))}
                    className={`rounded-xl px-4 py-2 text-sm font-semibold text-white shadow-sm transition-all duration-200 ${
                  publishBlocked
                    ? "cursor-not-allowed opacity-50 bg-gray-400"
                    : "bg-purple-600 hover:bg-purple-700"
                } ${broadcasting && !publishBlocked ? "cursor-wait opacity-70" : ""}`}
              >
                {broadcasting ? "Publishing..." : "Publish Everywhere"}
              </motion.button>
            ) : null}
            {item ? (
              <motion.button
                whileTap={{ scale: 0.95 }}
                disabled={publishBlocked}
                onClick={() => setSelectiveOpen((v) => !v)}
                className={`rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-semibold shadow-sm transition-all duration-200 hover:bg-slate-50 hover:shadow-md ${
                  publishBlocked ? "cursor-not-allowed opacity-50" : "text-slate-900"
                }`}
                title={
                  publishBlocked
                    ? "Check the acknowledgement box below to enable publishing (or Re-process)."
                    : undefined
                }
              >
                Choose Platforms
              </motion.button>
            ) : null}
            {item ? (
              <motion.button
                whileTap={{ scale: 0.95 }}
                disabled={reprocessing}
                onClick={() => void handleReprocess()}
                className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-900 shadow-sm transition-all duration-200 hover:bg-slate-50 hover:shadow-md disabled:opacity-60"
              >
                {reprocessing ? "Starting…" : "Re-process"}
              </motion.button>
            ) : null}
            {item?.original_file_url ? (
              <motion.a
                whileTap={{ scale: 0.95 }}
                href={item.original_file_url}
                target="_blank"
                rel="noreferrer"
                className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-all duration-200 hover:bg-slate-800"
              >
                Open file
              </motion.a>
            ) : null}
          </div>
        </div>

        {error ? (
          <div className="mt-6 rounded-2xl border border-rose-200 bg-rose-50 p-6 text-rose-900">
            <div className="font-semibold">Couldn’t load content item</div>
            <div className="mt-1 text-sm text-rose-800">{error}</div>
          </div>
        ) : null}

        {isProcessing ? (
          <div className="mt-6">
            <AIProcessingState />
          </div>
        ) : null}

        {item?.status === "completed_fallback" && (
          <div className="relative z-10 mt-6 mb-6 rounded-xl border border-amber-200 bg-amber-50 p-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 flex-shrink-0">
                <svg className="h-5 w-5 text-amber-500" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                  <path
                    fillRule="evenodd"
                    d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z"
                    clipRule="evenodd"
                  />
                </svg>
              </div>
              <div className="flex-1">
                <h3 className="text-sm font-semibold text-amber-800">
                  AI Generation Unavailable — Generic Content Shown
                </h3>
                <p className="mt-1 text-sm text-amber-700">
                  Gemini AI was unreachable during processing. The captions below are placeholder content — review
                  and edit before publishing when possible.
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    disabled={reprocessing}
                    onClick={() => void handleReprocess()}
                    className="inline-flex items-center rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-amber-700 disabled:opacity-60"
                  >
                    Re-process with AI
                  </button>
                </div>
                <label className="mt-4 flex cursor-pointer items-start gap-3 rounded-lg border border-amber-300/70 bg-white/60 px-3 py-2">
                  <input
                    type="checkbox"
                    className="mt-1 h-4 w-4 rounded border-amber-400 text-amber-700"
                    checked={publishDespitePlaceholderAck}
                    onChange={(e) => setPublishDespitePlaceholderAck(e.target.checked)}
                  />
                  <span className="text-xs font-medium text-amber-900">
                    I understand these captions may be generic placeholders. I still want to enable Publish (everywhere /
                    selective / YouTube) for this item.
                  </span>
                </label>
              </div>
            </div>
          </div>
        )}

        {banner ? (
          <div
            className={`relative z-10 mt-6 rounded-2xl border border-orange-200 bg-orange-50 p-4 text-sm text-orange-900`}
          >
            <div className="font-semibold">{banner.text}</div>
            <div className="mt-1 text-xs opacity-80">
              You can re-process later for a higher-quality result when traffic drops.
            </div>
            <label className="mt-3 flex cursor-pointer items-start gap-3 rounded-lg border border-orange-300/70 bg-white/60 px-3 py-2">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 rounded border-orange-400 text-orange-800"
                checked={publishDespitePlaceholderAck}
                onChange={(e) => setPublishDespitePlaceholderAck(e.target.checked)}
              />
              <span className="text-xs font-medium text-orange-950">
                I understand these captions may be generic placeholders. Enable publishing anyway.
              </span>
            </label>
          </div>
        ) : null}

        {selectiveOpen ? (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
            className="mt-6 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-sm font-semibold text-slate-900">Selective Publish</div>
                <div className="mt-1 text-xs text-slate-600">Choose where to publish this content.</div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setSelectiveOpen(false)}
                  className="rounded-xl px-3 py-2 text-xs font-semibold text-slate-600 hover:text-slate-900"
                >
                  Close
                </button>
                {item ? (
                  <button
                    type="button"
                    disabled={broadcasting || publishBlocked}
                    onClick={() => void publishSelected(String(item.id))}
                    className={`rounded-xl px-3 py-2 text-xs font-semibold text-white ${
                      publishBlocked
                        ? "cursor-not-allowed bg-gray-400 opacity-50"
                        : "bg-slate-900 hover:bg-slate-800"
                    } ${broadcasting && !publishBlocked ? "cursor-wait opacity-70" : ""}`}
                  >
                    {broadcasting ? "Publishing..." : "Publish Selected"}
                  </button>
                ) : null}
              </div>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-5">
              {(
                [
                  { key: "instagram", label: "Instagram", icon: <InstagramLogo className="h-5 w-5" /> },
                  { key: "facebook", label: "Facebook", icon: <FacebookLogo className="h-5 w-5 text-blue-600" /> },
                  { key: "linkedin", label: "LinkedIn", icon: <LinkedInLogo className="h-5 w-5 text-sky-700" /> },
                  { key: "x", label: "Twitter/X", icon: <XLogo className="h-5 w-5 text-slate-900" /> },
                  { key: "youtube", label: "YouTube", icon: <YouTubeLogo className="h-5 w-5 text-rose-600" /> }
                ] as const
              ).map((p) => (
                <label
                  key={p.key}
                  className={`flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 ${
                    publishBlocked ? "cursor-not-allowed opacity-50" : "cursor-pointer hover:bg-slate-100"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={Boolean(selectedPlatforms[p.key])}
                    disabled={publishBlocked}
                    onChange={(e) =>
                      setSelectedPlatforms((prev) => ({
                        ...prev,
                        [p.key]: e.target.checked
                      }))
                    }
                    className="h-4 w-4 rounded border-slate-300 text-slate-900"
                  />
                  <span className="inline-flex items-center gap-2 text-xs font-semibold text-slate-900">
                    {p.icon}
                    {p.label}
                  </span>
                </label>
              ))}
            </div>
          </motion.div>
        ) : null}

        <FadeIn className="mt-8 grid grid-cols-1 gap-6 md:grid-cols-2">
          {item?.platform_variants?.length ? (
            item.platform_variants.map((v) => {
              const captionKey = variantCaptionEditKey(v.platform);
              return (
              <motion.div key={v.id} variants={fadeUpItem}>
                <PlatformPreviewCard
                  v={v}
                  platforms={item.platforms}
                  contentId={String(item.id)}
                  onPublishYouTube={publishYouTube}
                  fallbackPublishBlocked={publishBlocked}
                  captionEditKey={captionKey}
                  isEditingCaption={editingPlatform === captionKey}
                  editedContentRow={editedContent[captionKey]}
                  captionSaving={captionSavingKey === captionKey}
                  isProcessing={Boolean(isProcessing)}
                  onValidateMedia={validateMedia}
                  onBeginCaptionDraft={handleBeginCaptionDraft}
                  onCaptionDraftChange={handleCaptionDraftChange}
                  onSaveCaptionEdit={(platformKey) => void handleSaveEdit(platformKey)}
                  onCancelCaptionEdit={() => setEditingPlatform(null)}
                />
              </motion.div>
            );
            })
          ) : loading ? (
            <div className="ai-shimmer h-40 rounded-2xl bg-slate-100" />
          ) : (
            <motion.div
              variants={fadeUpItem}
              className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm transition-all duration-200 hover:shadow-md text-slate-700"
            >
              No platform variants yet. If this item is new, start processing.
            </motion.div>
          )}
        </FadeIn>
      </main>
    </PageTransition>
  );
}
