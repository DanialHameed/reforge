"use client";

import React from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import { staggerContainer, fadeUpItem } from "@/components/Motion";
import {
  YouTubeLogo,
  InstagramLogo,
  FacebookLogo,
  XLogo,
  LinkedInLogo
} from "@/components/connections/platformLogos";
import type { PlatformKey, PlatformStatus } from "@/components/connections/PlatformConnectionCard";

const PLATFORM_META: Record<PlatformKey, { name: string; logo: React.ReactNode }> = {
  youtube: { name: "YouTube", logo: <YouTubeLogo className="h-5 w-5" /> },
  instagram: { name: "Instagram", logo: <InstagramLogo className="h-5 w-5" /> },
  facebook: { name: "Facebook", logo: <FacebookLogo className="h-5 w-5" /> },
  x: { name: "X / Twitter", logo: <XLogo className="h-5 w-5" /> },
  linkedin: { name: "LinkedIn", logo: <LinkedInLogo className="h-5 w-5" /> }
};

const PLATFORM_ORDER: PlatformKey[] = ["youtube", "x", "facebook", "instagram", "linkedin"];

export function PlatformHealthStrip() {
  const [statuses, setStatuses] = React.useState<PlatformStatus[] | null>(null);
  const [failed, setFailed] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;
    api
      .get<{ platforms: PlatformStatus[] }>("/api/v1/platforms/status")
      .then((res) => {
        if (!cancelled) setStatuses(res.platforms ?? []);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (failed) return null;

  const byPlatform = new Map((statuses ?? []).map((s) => [s.platform, s]));
  const connectedCount = (statuses ?? []).filter((s) => s.connected).length;

  return (
    <motion.div
      variants={staggerContainer(0.05)}
      initial="hidden"
      animate="show"
      className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm"
    >
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Platform health</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            {statuses ? `${connectedCount} of ${PLATFORM_ORDER.length} connected` : "Checking connections…"}
          </p>
        </div>
        <Link
          href="/connections"
          className="shrink-0 text-xs font-semibold text-indigo-600 hover:text-indigo-700"
        >
          Manage →
        </Link>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        {PLATFORM_ORDER.map((key) => {
          const meta = PLATFORM_META[key];
          const status = byPlatform.get(key);
          const connected = Boolean(status?.connected);
          const loading = !statuses;
          return (
            <motion.div key={key} variants={fadeUpItem}>
              <Link
                href="/connections"
                className={`flex items-center gap-2.5 rounded-xl border px-3 py-2.5 transition-colors ${
                  connected
                    ? "border-emerald-200 bg-emerald-50/60 hover:bg-emerald-50"
                    : "border-slate-200 bg-slate-50/60 hover:bg-slate-100"
                }`}
              >
                <span className="shrink-0 text-slate-700">{meta.logo}</span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-semibold text-slate-900">
                    {meta.name}
                  </span>
                  <span className="flex items-center gap-1 text-[11px] font-medium">
                    <span
                      className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                        loading
                          ? "animate-pulse bg-slate-300"
                          : connected
                            ? "bg-emerald-500"
                            : "bg-slate-300"
                      }`}
                    />
                    <span className={connected ? "text-emerald-700" : "text-slate-500"}>
                      {loading ? "Checking" : connected ? "Connected" : "Not connected"}
                    </span>
                  </span>
                </span>
              </Link>
            </motion.div>
          );
        })}
      </div>
    </motion.div>
  );
}
