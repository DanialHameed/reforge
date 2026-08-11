import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

export interface ContentData {
  id: string;
  status: string;
  // Passthrough fields from the existing FastAPI ContentItemResponse shape.
  title?: string | null;
  original_file_url?: string | null;
  file_type?: string | null;
  updated_at?: string | null;
  platform_variants?: Array<{
    id: string;
    platform: string | null;
    caption: string | null;
    hashtags: string[] | null;
    metadata: Record<string, unknown> | null;
    media_url: string | null;
    status: string;
    error_message: string | null;
  }>;
  platforms?: {
    instagram?: { caption: string; hashtags: string[]; story_text: string };
    twitter?: { tweet: string; thread: string[] };
    linkedin?: { post: string; hashtags: string[] };
    facebook?: { post: string; hashtags: string[] };
    youtube?: { title: string; description: string; tags: string[] };
  };
  image_analysis?: {
    description: string;
    mood: string;
    key_elements: string[];
  };
  is_fallback?: boolean;
  fallback_reason?: string;
}

export interface PollConfig {
  contentId: string;
  enabled: boolean;
  onComplete: (data: ContentData) => void;
  onError?: (err: Error) => void;
  apiBaseUrl?: string;
}

export interface PollState {
  status: "idle" | "polling" | "done" | "timeout" | "error";
  elapsedMs: number;
  attemptCount: number;
}

const POLL_INTERVALS = [1000, 1000, 2000, 2000, 3000, 5000] as const;
// Must comfortably exceed the backend's own ceiling for the analyze task
// (soft_time_limit=8min, time_limit=9min in content_processor.py), plus
// headroom for queueing delay when the worker is busy. The previous 45s
// value meant this UI declared "Couldn't load content item" on essentially
// every real video (and many images) while the backend was still correctly
// processing in the background — the job never actually failed, the
// frontend just stopped watching and showed an alarming error.
const MAX_POLL_DURATION_MS = 12 * 60 * 1000;
const TERMINAL_STATUSES = ["completed", "completed_fallback", "error_fallback", "timeout_fallback"] as const;

function isTerminalStatus(status: string | undefined): boolean {
  return Boolean(status && (TERMINAL_STATUSES as readonly string[]).includes(status));
}

type RawPlatformVariant = NonNullable<ContentData["platform_variants"]>[number];
type RawContentResponse = ContentData & {
  platform_variants?: RawPlatformVariant[];
};

function toStringArray(v: unknown): string[] {
  return Array.isArray(v) ? v.map((x) => String(x)).filter((x) => x.trim().length > 0) : [];
}

function mapPlatformsFromVariants(variants: RawPlatformVariant[] | undefined): NonNullable<ContentData["platforms"]> {
  const out: NonNullable<ContentData["platforms"]> = {};
  for (const v of variants || []) {
    const platform = String(v.platform || "").toLowerCase();
    const caption = String(v.caption || "");
    const hashtags = (v.hashtags || []).filter((x) => typeof x === "string") as string[];
    const md = v.metadata || {};

    if (platform === "youtube") {
      const description = typeof md["description"] === "string" ? String(md["description"]) : "";
      const tags = toStringArray(md["tags"]);
      out.youtube = { title: caption, description, tags };
    } else if (platform === "instagram") {
      const story_text = typeof md["story_text"] === "string" ? String(md["story_text"]) : "";
      out.instagram = { caption, hashtags, story_text };
    } else if (platform === "twitter" || platform === "x") {
      const thread = toStringArray(md["thread_tweets"] ?? md["thread"]);
      out.twitter = { tweet: caption, thread };
    } else if (platform === "linkedin") {
      out.linkedin = { post: caption, hashtags };
    } else if (platform === "facebook") {
      out.facebook = { post: caption, hashtags };
    }
  }
  return out;
}

export function mapRawToContentData(raw: RawContentResponse): ContentData {
  const platforms = mapPlatformsFromVariants(raw.platform_variants);
  const status = String(raw.status || "");
  const isFallback = Boolean(raw.fallback_reason) || status.includes("fallback");

  return {
    ...raw,
    status,
    platforms,
    is_fallback: raw.is_fallback ?? isFallback
  };
}

/** GET /api/v1/content/{id} and map response (same as polling mapper). Used after PATCH variant edits. */
export async function fetchContentData(contentId: string): Promise<ContentData> {
  const raw = await api.get<RawContentResponse>(`/api/v1/content/${contentId}`);
  return mapRawToContentData(raw);
}

/**
 * Polls `/api/v1/content/{id}` with backoff (1s, 1s, 2s, 2s, 3s, then 5s).
 * Stops after 45s hard timeout.
 *
 * Aborts cleanly when `contentId` / `enabled` changes or the component unmounts
 * so an in-flight request cannot schedule another poll for a stale id.
 */
export function useContentPolling(config: PollConfig): PollState {
  const [status, setStatus] = useState<PollState["status"]>("idle");
  const [elapsedMs, setElapsedMs] = useState<number>(0);
  const [attemptCount, setAttemptCount] = useState<number>(0);

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startTimeRef = useRef<number>(0);
  const attemptRef = useRef<number>(0);
  const onCompleteRef = useRef<PollConfig["onComplete"]>(config.onComplete);
  const onErrorRef = useRef<PollConfig["onError"]>(config.onError);

  useEffect(() => {
    onCompleteRef.current = config.onComplete;
  }, [config.onComplete]);
  useEffect(() => {
    onErrorRef.current = config.onError;
  }, [config.onError]);

  const enabled = Boolean(config.enabled);
  const contentId = String(config.contentId || "");

  useEffect(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }

    setElapsedMs(0);
    setAttemptCount(0);

    if (!enabled || !contentId) {
      setStatus("idle");
      return;
    }

    let aborted = false;
    attemptRef.current = 0;
    startTimeRef.current = Date.now();
    setStatus("polling");

    const clearTimer = () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };

    const scheduleNext = (delayMs: number) => {
      if (aborted) return;
      clearTimer();
      timerRef.current = setTimeout(() => {
        void runPoll();
      }, delayMs);
    };

    const runPoll = async () => {
      if (aborted) return;

      const now = Date.now();
      const elapsed = now - startTimeRef.current;
      setElapsedMs(elapsed);

      if (elapsed > MAX_POLL_DURATION_MS) {
        if (aborted) return;
        setStatus("timeout");
        onErrorRef.current?.(new Error("Polling timed out"));
        return;
      }

      try {
        const raw = await api.get<RawContentResponse>(`/api/v1/content/${contentId}`);
        if (aborted) return;

        const data = mapRawToContentData(raw);

        if (isTerminalStatus(data?.status)) {
          setStatus("done");
          onCompleteRef.current(data);
          return;
        }

        attemptRef.current += 1;
        setAttemptCount(attemptRef.current);
        setStatus("polling");
        const delay = POLL_INTERVALS[Math.min(attemptRef.current, POLL_INTERVALS.length - 1)];
        scheduleNext(delay);
      } catch (e) {
        if (aborted) return;
        setStatus("error");
        const err = e instanceof Error ? e : new Error("Polling error");
        onErrorRef.current?.(err);
        attemptRef.current += 1;
        setAttemptCount(attemptRef.current);
        const delay = POLL_INTERVALS[Math.min(attemptRef.current, POLL_INTERVALS.length - 1)];
        scheduleNext(delay);
      }
    };

    void runPoll();

    return () => {
      aborted = true;
      clearTimer();
    };
  }, [contentId, enabled]);

  return { status, elapsedMs, attemptCount };
}

export default useContentPolling;
