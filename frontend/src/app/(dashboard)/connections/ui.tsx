"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";

import { api } from "@/lib/api";
import { FadeIn, PageTransition, staggerContainer, fadeUpItem } from "@/components/Motion";
import { AIProcessingState } from "@/components/AIProcessingState";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { isEvaluationMode } from "@/lib/evaluationMode";
import {
  PlatformConnectionCard,
  type PlatformKey,
  type PlatformStatus
} from "@/components/connections/PlatformConnectionCard";
import type { MetaPagesResponse } from "@/types/api";
import {
  FacebookLogo,
  InstagramLogo,
  LinkedInLogo,
  XLogo,
  YouTubeLogo
} from "@/components/connections/platformLogos";

type PlatformsStatusResponse = {
  platforms: PlatformStatus[];
  evaluation_mode?: boolean;
};

type AuthorizeResponse = {
  authorize_url: string;
};

const AUTHORIZE_PATHS: Record<PlatformKey, string> = {
  youtube: "/api/v1/connections/youtube/authorize",
  facebook: "/api/v1/connections/facebook/authorize",
  instagram: "/api/v1/connections/instagram/authorize",
  linkedin: "/api/v1/connections/linkedin/authorize",
  x: "/api/v1/connections/twitter/authorize",
};

type Capabilities = {
  platform: PlatformKey;
  automated: string[];
  manual: string[];
  limits: string[];
};

const CAPABILITIES: Capabilities[] = [
  {
    platform: "youtube",
    automated: [
      "Upload scheduled videos",
      "Set title/description/tags",
      "Auto-thumbnails (optional)"
    ],
    manual: ["Copyright claims review", "Community moderation"],
    limits: ["Quota-based API requests", "Upload size limits depend on account"]
  },
  {
    platform: "instagram",
    automated: [
      "Schedule posts (where supported)",
      "Generate captions + hashtags",
      "Media transforms for feed/reels"
    ],
    manual: ["Some accounts require assisted publishing"],
    limits: ["Rate limits vary by app review + permissions"]
  },
  {
    platform: "facebook",
    automated: ["Publish to pages", "Schedule posts", "Auto caption variants"],
    manual: ["Certain permissions require business verification"],
    limits: ["Graph API limits per app/user/page"]
  },
  {
    platform: "x",
    automated: ["Generate threads", "Schedule posts (plan dependent)"],
    manual: ["Media uploads may vary by access tier"],
    limits: ["API tier limits; posting caps may apply"]
  },
  {
    platform: "linkedin",
    automated: [
      "Draft post variants",
      "Assisted deep-link to composer",
      "Media packaging"
    ],
    manual: ["Final posting confirmation (assisted)"],
    limits: ["Strict API access; organization permissions required for some actions"]
  }
];

function cx(...parts: Array<string | undefined | false>) {
  return parts.filter(Boolean).join(" ");
}

function Toast({
  toast,
  onClose
}: {
  toast: { kind: "success" | "error"; message: string } | null;
  onClose: () => void;
}) {
  return (
    <AnimatePresence>
      {toast ? (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 12 }}
          className="fixed bottom-6 right-6 z-50"
        >
          <div
            className={cx(
              "rounded-2xl border px-4 py-3 text-sm font-semibold shadow-lg backdrop-blur",
              toast.kind === "success"
                ? "border-emerald-200 bg-emerald-50 text-emerald-900"
                : "border-rose-200 bg-rose-50 text-rose-900"
            )}
          >
            <div className="flex items-center gap-3">
              <div className="min-w-0">{toast.message}</div>
              <button
                onClick={onClose}
                className="rounded-xl bg-white/60 px-2 py-1 text-xs font-semibold text-slate-700 hover:bg-white"
              >
                Close
              </button>
            </div>
          </div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

function ConfirmModal({
  open,
  title,
  description,
  confirmText,
  onConfirm,
  onClose
}: {
  open: boolean;
  title: string;
  description: string;
  confirmText: string;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <AnimatePresence>
      {open ? (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-end justify-center bg-black/30 p-6 md:items-center"
          onClick={onClose}
        >
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 12 }}
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-lg rounded-2xl border border-slate-200 bg-white p-5 shadow-xl"
          >
            <div className="text-sm font-semibold text-slate-900">{title}</div>
            <div className="mt-2 text-sm text-slate-600">{description}</div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={onClose}
                className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-900 hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  onConfirm();
                  onClose();
                }}
                className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-2 text-sm font-semibold text-rose-900 hover:bg-rose-100"
              >
                {confirmText}
              </button>
            </div>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

function CapabilityAccordion({ caps }: { caps: Capabilities }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-2xl border border-slate-200 bg-white">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-6 py-4 text-left"
      >
        <div className="text-sm font-semibold text-slate-900">
          {(caps.platform === "x" ? "Twitter / X" : caps.platform).toUpperCase()} capabilities
        </div>
        <div className="text-sm font-semibold text-slate-600">{open ? "Hide" : "Show"}</div>
      </button>
      <AnimatePresence>
        {open ? (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
            className="border-t border-slate-200 px-6 py-4"
          >
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <div>
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Automated
                </div>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-700">
                  {caps.automated.map((x) => (
                    <li key={x}>{x}</li>
                  ))}
                </ul>
              </div>
              <div>
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Manual action
                </div>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-700">
                  {caps.manual.map((x) => (
                    <li key={x}>{x}</li>
                  ))}
                </ul>
              </div>
              <div>
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  API limits
                </div>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-700">
                  {caps.limits.map((x) => (
                    <li key={x}>{x}</li>
                  ))}
                </ul>
              </div>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

export default function ConnectionsClient() {
  const router = useRouter();
  const params = useSearchParams();

  const [confirmPlatform, setConfirmPlatform] = useState<PlatformKey | null>(null);
  const [metaPagesOpen, setMetaPagesOpen] = useState(false);
  const [metaSelectedPageId, setMetaSelectedPageId] = useState<string | null>(null);
  const { push } = useToast();

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["platforms-status"],
    queryFn: async () => api.get<PlatformsStatusResponse>("/api/v1/platforms/status"),
    refetchInterval: 5 * 60 * 1000
  });

  const metaPagesQuery = useQuery({
    queryKey: ["meta-pages"],
    queryFn: async () => api.get<MetaPagesResponse>("/api/v1/connections/meta/pages"),
    enabled: metaPagesOpen,
  });

  useEffect(() => {
    if (!metaPagesQuery.data) return;
    setMetaSelectedPageId(metaPagesQuery.data.selected_page_id ?? null);
  }, [metaPagesQuery.data]);

  useEffect(() => {
    const status = params.get("status");
    const platform = (params.get("platform") || "").toLowerCase();
    const legacySuccess = params.get("success");
    const err = params.get("error");
    const reason = params.get("reason");

    const platformLabel =
      platform === "youtube"
        ? "YouTube"
        : platform === "facebook"
          ? "Facebook"
          : platform === "instagram"
            ? "Instagram"
            : platform === "twitter" || platform === "x"
              ? "Twitter / X"
              : platform === "linkedin"
                ? "LinkedIn"
                : "Platform";

    if (status === "success") {
      push({ kind: "success", message: `${platformLabel} Connected Successfully!` });
      void refetch();
      router.replace("/connections");
      return;
    }
    if (status === "error") {
      push({
        kind: "error",
        message: reason
          ? `Connection failed: ${reason}`
          : "Connection failed. Please try again.",
      });
      void refetch();
      router.replace("/connections");
      return;
    }

    if (legacySuccess === "true") {
      push({ kind: "success", message: "Platform connected successfully." });
      void refetch();
      router.replace("/connections");
    } else if (err) {
      push({ kind: "error", message: `Connection failed: ${err}` });
      void refetch();
      router.replace("/connections");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const statuses = useMemo(() => {
    const fallback: PlatformStatus[] = [
      { platform: "youtube", connected: false, tier: "full_auto" },
      { platform: "instagram", connected: false, tier: "semi_auto" },
      { platform: "facebook", connected: false, tier: "semi_auto" },
      { platform: "x", connected: false, tier: "assisted" },
      { platform: "linkedin", connected: false, tier: "assisted" }
    ];
    const list = data?.platforms?.length ? data.platforms : fallback;
    const by = new Map(list.map((s) => [s.platform, s] as const));
    for (const f of fallback) if (!by.has(f.platform)) by.set(f.platform, f);
    return Array.from(by.values());
  }, [data]);

  const disconnectLocked = Boolean(data?.evaluation_mode) || isEvaluationMode();

  async function connect(platform: PlatformKey) {
    try {
      const path = AUTHORIZE_PATHS[platform];
      if (!path) throw new Error(`Unsupported platform: ${platform}`);
      const res = await api.get<AuthorizeResponse>(path);
      if (!res.authorize_url) throw new Error("Missing authorize_url");
      window.location.href = res.authorize_url;
    } catch (e) {
      push({ kind: "error", message: e instanceof Error ? e.message : "Failed to start OAuth" });
    }
  }

  async function disconnect(platform: PlatformKey) {
    if (disconnectLocked) {
      push({
        kind: "info",
        message: "Evaluation mode: disconnect is disabled for demo safety.",
      });
      return;
    }
    try {
      const { apiClient } = await import("@/lib/api");
      await apiClient.delete(`/api/v1/platforms/${platform}`);
      push({ kind: "success", message: "Disconnected." });
      await refetch();
    } catch (e) {
      push({ kind: "error", message: e instanceof Error ? e.message : "Disconnect failed" });
    }
  }

  async function saveMetaPageSelection() {
    try {
      if (!metaSelectedPageId) {
        push({ kind: "error", message: "Select a Facebook Page first." });
        return;
      }
      const { apiClient } = await import("@/lib/api");
      await apiClient.post("/api/v1/connections/meta/select-page", null, {
        params: { page_id: metaSelectedPageId },
      });
      push({ kind: "success", message: "Meta Page selected. Facebook + Instagram publishing will use it." });
      setMetaPagesOpen(false);
      await refetch();
    } catch (e) {
      push({ kind: "error", message: e instanceof Error ? e.message : "Failed to save selection" });
    }
  }

  async function refreshToken(platform: PlatformKey) {
    try {
      const { apiClient } = await import("@/lib/api");
      await apiClient.post(`/api/v1/platforms/${platform}/refresh`, {});
      push({ kind: "success", message: "Token refreshed." });
      await refetch();
    } catch (e) {
      push({ kind: "error", message: e instanceof Error ? e.message : "Refresh failed" });
    }
  }

  const cards = [
    {
      key: "youtube" as const,
      name: "YouTube",
      logo: <YouTubeLogo className="h-8 w-8 text-rose-600" />,
      bullets: [
        "Auto-generate titles/descriptions",
        "Schedule uploads",
        "Metadata + tags optimization"
      ]
    },
    {
      key: "instagram" as const,
      name: "Instagram",
      logo: <InstagramLogo className="h-8 w-8" />,
      bullets: ["Captions + hashtags", "Feed/Reel formatting", "Assisted publishing when required"]
    },
    {
      key: "facebook" as const,
      name: "Facebook",
      logo: <FacebookLogo className="h-8 w-8 text-blue-600" />,
      bullets: ["Page posting automation", "Scheduling", "A/B caption variants"]
    },
    {
      key: "x" as const,
      name: "Twitter / X",
      logo: <XLogo className="h-8 w-8 text-slate-900" />,
      bullets: ["Threads + hooks generation", "Post scheduling (tier dependent)", "Engagement-friendly formatting"]
    },
    {
      key: "linkedin" as const,
      name: "LinkedIn",
      logo: <LinkedInLogo className="h-8 w-8 text-sky-700" />,
      bullets: ["Assisted posting workflow", "Post + hashtag variants", "Media packaging for higher reach"]
    }
  ];

  return (
    <PageTransition>
      <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            Connections
          </h1>
          <p className="mt-2 text-slate-600">
            Connect your platforms once. ReForge handles tokens, refresh, and publishing workflows.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button onClick={() => void refetch()} variant="secondary">
            {isFetching ? "Refreshing…" : "Refresh"}
          </Button>
          <a
            href="/docs"
            target="_blank"
            rel="noreferrer"
            className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800 transition-all duration-200 shadow-sm"
          >
            API Docs
          </a>
        </div>
      </div>

      {disconnectLocked && !isLoading ? (
        <div
          role="status"
          className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-medium text-amber-950"
        >
          Evaluation mode: disconnect is disabled here; publish with no matching connection returns a dry-run
          response (no external APIs). Set{" "}
          <code className="rounded bg-white/70 px-1 py-0.5 text-xs">EVALUATION_MODE=false</code> for full
          behavior.
        </div>
      ) : null}

      {isLoading ? (
        <div className="mt-6">
          <AIProcessingState title="Fetching connection status" />
        </div>
      ) : error ? (
        <div className="mt-6 rounded-2xl border border-rose-200 bg-rose-50 p-6 text-rose-900">
          <div className="font-semibold">Couldn’t load platform status</div>
          <div className="mt-1 text-sm text-rose-800">
            {error instanceof Error ? error.message : "Unknown error"}
          </div>
          <div className="mt-3 text-sm text-rose-800">
            Backend endpoint expected:{" "}
            <code className="rounded bg-white/60 px-1 py-0.5">GET /api/v1/platforms/status</code>
          </div>
        </div>
      ) : null}

      <FadeIn className="mt-8">
        <motion.div
          variants={staggerContainer(0.1)}
          initial="hidden"
          animate="show"
          className="grid grid-cols-1 gap-6 md:grid-cols-2"
        >
          {cards.map((c) => {
            const st = statuses.find((s) => s.platform === c.key) as PlatformStatus;
            return (
              <motion.div key={c.key} variants={fadeUpItem} className="h-full">
                <PlatformConnectionCard
                  name={c.name}
                  logo={c.logo}
                  bullets={c.bullets}
                  status={st}
                  onConnect={connect}
                  onDisconnect={disconnect}
                  onRefreshToken={refreshToken}
                  onConfirmDisconnect={(p) => setConfirmPlatform(p)}
                  disconnectDisabled={disconnectLocked}
                />
              </motion.div>
            );
          })}
        </motion.div>
      </FadeIn>

      <div className="mt-10 rounded-2xl border border-slate-200 bg-white p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-slate-900">Meta (Facebook + Instagram) publishing page</div>
            <div className="mt-1 text-sm text-slate-600">
              If your Meta account has multiple Pages, select the one to use for consistent Facebook + Instagram publishing.
            </div>
          </div>
          <Button onClick={() => setMetaPagesOpen(true)} variant="secondary">
            Choose Page
          </Button>
        </div>

        {metaPagesOpen ? (
          <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50 p-4">
            {metaPagesQuery.isLoading ? (
              <div className="text-sm text-slate-700">Loading pages…</div>
            ) : metaPagesQuery.error ? (
              <div className="text-sm text-rose-800">
                {metaPagesQuery.error instanceof Error ? metaPagesQuery.error.message : "Failed to load pages"}
              </div>
            ) : (
              <div className="space-y-3">
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Available Pages
                </div>
                <div className="grid grid-cols-1 gap-2">
                  {(metaPagesQuery.data?.pages ?? []).map((p) => (
                    <label
                      key={p.page_id}
                      className="flex cursor-pointer items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 hover:bg-slate-50"
                    >
                      <div className="flex items-center gap-3">
                        <input
                          type="radio"
                          name="meta-page"
                          checked={metaSelectedPageId === p.page_id}
                          onChange={() => setMetaSelectedPageId(p.page_id)}
                          className="h-4 w-4"
                        />
                        <div className="min-w-0">
                          <div className="truncate text-sm font-semibold text-slate-900">{p.page_name || p.page_id}</div>
                          <div className="mt-0.5 text-xs text-slate-600">
                            Instagram business linked:{" "}
                            <span className={p.has_instagram_business_account ? "text-emerald-700 font-semibold" : "text-amber-700 font-semibold"}>
                              {p.has_instagram_business_account ? "Yes" : "No"}
                            </span>
                          </div>
                        </div>
                      </div>
                      <div className="text-xs text-slate-500">{p.page_id}</div>
                    </label>
                  ))}
                </div>

                <div className="flex flex-wrap justify-end gap-2 pt-2">
                  <button
                    onClick={() => setMetaPagesOpen(false)}
                    className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-900 hover:bg-slate-50"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => void saveMetaPageSelection()}
                    className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800"
                  >
                    Save selection
                  </button>
                </div>
              </div>
            )}
          </div>
        ) : null}
      </div>

      <div className="mt-12">
        <FadeIn>
          <div className="flex items-end justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-slate-900">Platform capabilities</h2>
              <p className="mt-1 text-sm text-slate-600">
                Understand what’s fully automated vs assisted, and where platform API limits apply.
              </p>
            </div>
            <Link href="/queue" className="text-sm font-semibold text-slate-900 hover:underline">
              Go to Queue →
            </Link>
          </div>
        </FadeIn>

        <div className="mt-4 space-y-3">
          {CAPABILITIES.map((c) => (
            <CapabilityAccordion key={c.platform} caps={c} />
          ))}
        </div>
      </div>

      <Dialog
        open={Boolean(confirmPlatform)}
        title="Disconnect platform?"
        description="This will remove your saved tokens and require reconnecting to publish again."
        confirmText="Disconnect"
        confirmVariant="danger"
        onConfirm={() => {
          if (!confirmPlatform) return;
          void disconnect(confirmPlatform);
        }}
        onClose={() => setConfirmPlatform(null)}
      />
      </main>
    </PageTransition>
  );
}

