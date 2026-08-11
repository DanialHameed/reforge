"use client";

import { staggerContainer, fadeUpItem } from "@/components/Motion";
import { motion } from "framer-motion";
import Image from "next/image";

export type AutomationTier = "full_auto" | "semi_auto" | "assisted";

export type PlatformKey = "youtube" | "instagram" | "facebook" | "x" | "linkedin";

export type PlatformStatus = {
  platform: PlatformKey;
  connected: boolean;
  account?: {
    username: string;
    profile_image_url?: string | null;
  } | null;
  token_expires_at?: string | null; // ISO
  last_used_at?: string | null; // ISO
  tier: AutomationTier;
};

function cx(...parts: Array<string | undefined | false>) {
  return parts.filter(Boolean).join(" ");
}

function tierBadge(tier: AutomationTier) {
  if (tier === "full_auto")
    return { label: "Full Auto", cls: "bg-emerald-50 text-emerald-900 border-emerald-200" };
  if (tier === "semi_auto")
    return { label: "Semi‑Auto", cls: "bg-blue-50 text-blue-900 border-blue-200" };
  return { label: "Assisted", cls: "bg-orange-50 text-orange-900 border-orange-200" };
}

function expiresText(expiresAtIso?: string | null) {
  if (!expiresAtIso) return { text: "No expiry info", cls: "text-slate-500" };
  const ms = new Date(expiresAtIso).getTime() - Date.now();
  const days = Math.max(0, Math.round(ms / (1000 * 60 * 60 * 24)));
  const text = `Expires in ${days} day${days === 1 ? "" : "s"}`;
  const cls =
    days > 7 ? "text-emerald-700" : days >= 3 ? "text-amber-700" : "text-rose-700";
  return { text, cls };
}

export function PlatformConnectionCard({
  name,
  logo,
  bullets,
  status,
  onConnect,
  onDisconnect,
  onRefreshToken,
  onConfirmDisconnect,
  disconnectDisabled = false
}: {
  name: string;
  logo: React.ReactNode;
  bullets: string[];
  status: PlatformStatus;
  onConnect: (platform: PlatformKey) => void;
  onDisconnect: (platform: PlatformKey) => void;
  onRefreshToken: (platform: PlatformKey) => void;
  onConfirmDisconnect: (platform: PlatformKey) => void;
  /** When true, Disconnect is not offered (evaluation / demo safety). */
  disconnectDisabled?: boolean;
}) {
  const tier = tierBadge(status.tier);
  const exp = expiresText(status.token_expires_at);
  const lastUsed = status.last_used_at ? new Date(status.last_used_at).toLocaleString() : "—";

  return (
    <div className="h-full">
      <motion.div
        variants={staggerContainer(0.08)}
        initial="hidden"
        animate="show"
        className="ai-gradient-border h-full bg-white/70 p-[1px]"
      >
        <div className="flex h-full flex-col rounded-2xl bg-white p-5 shadow-sm">
          <motion.div variants={fadeUpItem} className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="text-slate-900">{logo}</div>
              <div className="min-w-0">
                <div className="text-sm font-semibold text-slate-900">{name}</div>
                <div className="mt-1 text-xs text-slate-500">
                  OAuth connection • Secure tokens • Fine-grained scopes
                </div>
              </div>
            </div>
            <span className={cx("rounded-full border px-2 py-1 text-xs font-semibold", tier.cls)}>
              {tier.label}
            </span>
          </motion.div>

          <motion.div variants={fadeUpItem} className="mt-4">
            {status.connected ? (
              <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-xs font-semibold text-emerald-900">
                    Connected
                  </span>
                  {disconnectDisabled ? (
                    <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-900">
                      Disconnect locked (evaluation)
                    </span>
                  ) : null}
                </div>
                <div className="mt-3 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <div className="relative h-10 w-10 overflow-hidden rounded-full bg-white">
                      {status.account?.profile_image_url ? (
                        <Image
                          src={status.account.profile_image_url}
                          alt=""
                          fill
                          sizes="40px"
                          className="object-cover"
                        />
                      ) : (
                        <div className="absolute inset-0 bg-gradient-to-br from-slate-200 to-slate-100" />
                      )}
                    </div>
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold text-slate-900">
                        @{status.account?.username ?? "connected"}
                      </div>
                      <div className="mt-1 text-xs text-slate-600">
                        <span className={cx("font-semibold", exp.cls)}>{exp.text}</span>
                        <span className="mx-2 text-slate-300">•</span>
                        Last used: {lastUsed}
                      </div>
                    </div>
                  </div>
                </div>

                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => onRefreshToken(status.platform)}
                    className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-900 hover:bg-slate-50"
                  >
                    Refresh Token
                  </button>
                  <button
                    type="button"
                    disabled={disconnectDisabled}
                    title={
                      disconnectDisabled
                        ? "Disconnect is disabled in evaluation mode"
                        : undefined
                    }
                    onClick={() => {
                      if (!disconnectDisabled) onConfirmDisconnect(status.platform);
                    }}
                    className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-900 hover:bg-rose-100 disabled:pointer-events-none disabled:opacity-50"
                  >
                    Disconnect
                  </button>
                </div>
              </div>
            ) : (
              <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <span className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-xs font-semibold text-slate-600">
                    Not connected
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => onConnect(status.platform)}
                  className="w-full rounded-2xl bg-gradient-to-r from-indigo-600 to-purple-600 px-4 py-3 text-sm font-semibold text-white shadow-sm transition hover:from-indigo-500 hover:to-purple-500"
                >
                  Connect {name}
                </button>
                <ul className="mt-4 list-disc space-y-2 pl-5 text-sm text-slate-700">
                  {bullets.map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </div>
            )}
          </motion.div>

          <div className="mt-auto pt-4">
            {status.connected ? (
              <button
                onClick={() => onDisconnect(status.platform)}
                className="hidden"
                aria-hidden="true"
              />
            ) : null}
          </div>
        </div>
      </motion.div>
    </div>
  );
}

