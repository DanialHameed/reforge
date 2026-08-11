"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  Radar,
  RadarChart,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";

import { api } from "@/lib/api";
import type { AnalyticsSummary } from "@/types/api";
import { FadeIn, PageTransition } from "@/components/Motion";
import { AIProcessingState } from "@/components/AIProcessingState";
import { motion } from "framer-motion";

type RangePreset = "7" | "30" | "90" | "custom";

function cx(...parts: Array<string | undefined | false>) {
  return parts.filter(Boolean).join(" ");
}

const PLATFORM_KEYS = ["youtube", "instagram", "facebook", "x", "linkedin"] as const;

function platformColor(platform: string) {
  switch (platform) {
    case "youtube":
      return "#ef4444";
    case "instagram":
      return "#a855f7";
    case "facebook":
      return "#2563eb";
    case "x":
      return "#0f172a";
    case "linkedin":
      return "#0284c7";
    default:
      return "#64748b";
  }
}

function intensityClass(v: number) {
  if (v <= 0) return "bg-indigo-100";
  if (v <= 1) return "bg-indigo-200";
  if (v <= 3) return "bg-indigo-400";
  if (v <= 6) return "bg-indigo-600";
  return "bg-indigo-800";
}

function downloadCSV(filename: string, rows: Record<string, unknown>[]) {
  const cols = Array.from(
    rows.reduce((set, r) => {
      Object.keys(r).forEach((k) => set.add(k));
      return set;
    }, new Set<string>())
  );

  const escape = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
    return s;
  };

  const csv = [
    cols.join(","),
    ...rows.map((r) => cols.map((c) => escape(r[c])).join(","))
  ].join("\n");

  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export default function AnalyticsPage() {
  const [preset, setPreset] = useState<RangePreset>("30");
  const [customDays, setCustomDays] = useState(30);

  const days = preset === "custom" ? customDays : Number(preset);

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["analytics-summary", days],
    queryFn: async () => api.get<AnalyticsSummary>(`/api/v1/analytics/summary?days=${days}`),
    refetchInterval: 5 * 60 * 1000
  });

  const lineData = useMemo(() => {
    if (!data?.published_by_day_platform?.length) {
      // fallback to single series if extra field absent
      return (data?.published_by_day ?? []).map((d) => ({ date: d.date, total: d.count }));
    }

    type LineRow = { date: string } & Record<string, number>;
    const byDate = new Map<string, LineRow>();
    for (const row of data.published_by_day_platform) {
      const rec = byDate.get(row.date) ?? ({ date: row.date } as LineRow);
      const key = String(row.platform);
      rec[key] = (rec[key] ?? 0) + row.count;
      byDate.set(row.date, rec);
    }
    const sorted = Array.from(byDate.values()).sort((a, b) =>
      String(a.date).localeCompare(String(b.date))
    );
    // Ensure all platforms exist as keys for smooth line rendering
    return sorted.map((r) => {
      for (const p of PLATFORM_KEYS) r[p] = r[p] ?? 0;
      return r;
    });
  }, [data]);

  const radarData = useMemo(() => {
    const rates = data?.success_rate_by_platform ?? {};
    return PLATFORM_KEYS.map((p) => ({
      platform: p,
      success: Number(rates[p] ?? 0)
    }));
  }, [data]);

  const barData = useMemo(() => {
    const rows = data?.content_type_breakdown ?? [];
    const types = ["video", "image", "article"] as const;

    // BarChart expects one row per platform with type fields
    const byPlatform = new Map<string, Record<string, unknown>>();
    for (const p of PLATFORM_KEYS) byPlatform.set(p, { platform: p, video: 0, image: 0, article: 0 });
    for (const r of rows) {
      const p = (r.platform ?? "unknown").toLowerCase();
      const t = (r.content_type ?? "article").toLowerCase();
      if (!byPlatform.has(p)) byPlatform.set(p, { platform: p, video: 0, image: 0, article: 0 });
      const rec = byPlatform.get(p)!;
      if (types.includes(t as (typeof types)[number])) rec[t] = Number(rec[t] ?? 0) + r.count;
      else rec.article = Number(rec.article ?? 0) + r.count;
    }
    return Array.from(byPlatform.values());
  }, [data]);

  const heatmap = useMemo(() => {
    // 7 rows (Mon-Sun) x 24 cols (hours).
    const grid = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => 0));
    for (const cell of data?.published_heatmap ?? []) {
      // API returns SQLite weekday 0=Sun..6=Sat; convert to Mon=0..Sun=6
      const w = ((cell.weekday + 6) % 7);
      const h = cell.hour;
      if (w >= 0 && w < 7 && h >= 0 && h < 24) grid[w][h] = cell.count;
    }
    return grid;
  }, [data]);

  const weekdayLabels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  return (
    <PageTransition>
      <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Analytics</h1>
          <p className="mt-2 text-slate-600">
            A comprehensive view of publishing volume, success rate, and timing.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="rounded-xl border border-slate-200 bg-white p-1">
            {([
              { key: "7", label: "Last 7 Days" },
              { key: "30", label: "Last 30 Days" },
              { key: "90", label: "Last 90 Days" },
              { key: "custom", label: "Custom" }
            ] as const).map((r) => (
              <motion.button
                whileTap={{ scale: 0.95 }}
                key={r.key}
                onClick={() => setPreset(r.key)}
                className={cx(
                  "rounded-lg px-3 py-2 text-sm font-semibold transition",
                  preset === r.key ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-50"
                )}
              >
                {r.label}
              </motion.button>
            ))}
          </div>

          {preset === "custom" ? (
            <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2">
              <div className="text-sm font-semibold text-slate-700">Days</div>
              <input
                value={customDays}
                onChange={(e) => setCustomDays(Math.max(1, Math.min(365, Number(e.target.value) || 1)))}
                type="number"
                min={1}
                max={365}
                className="w-20 text-sm text-slate-900 outline-none"
              />
            </div>
          ) : null}

          <motion.button
            whileTap={{ scale: 0.95 }}
            onClick={() => void refetch()}
            className="bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md hover:bg-slate-50 transition-all duration-200 px-4 py-2 text-sm font-semibold text-slate-900"
          >
            {isFetching ? "Refreshing…" : "Refresh"}
          </motion.button>

          <motion.button
            whileTap={{ scale: 0.95 }}
            onClick={() => {
              if (!data) return;
              downloadCSV(`analytics_${days}d.csv`, [
                { metric: "total_published", value: data.total_published },
                ...Object.entries(data.published_by_platform ?? {}).map(([k, v]) => ({
                  metric: "published_by_platform",
                  platform: k,
                  value: v
                })),
                ...Object.entries(data.success_rate_by_platform ?? {}).map(([k, v]) => ({
                  metric: "success_rate_by_platform",
                  platform: k,
                  value: v
                })),
                ...(data.published_by_day ?? []).map((r) => ({
                  metric: "published_by_day",
                  date: r.date,
                  value: r.count
                }))
              ]);
            }}
            className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800 transition-all duration-200 shadow-sm"
          >
            Export CSV
          </motion.button>
        </div>
      </div>

      {isLoading ? (
        <div className="mt-6">
          <AIProcessingState title="Crunching analytics" />
        </div>
      ) : error ? (
        <div className="mt-6 rounded-2xl border border-rose-200 bg-rose-50 p-6 text-rose-900">
          <div className="font-semibold">Couldn’t load analytics</div>
          <div className="mt-1 text-sm text-rose-800">
            {error instanceof Error ? error.message : "Unknown error"}
          </div>
        </div>
      ) : null}

      <div className="mt-8 grid grid-cols-1 gap-6">
        <FadeIn className="bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md transition-all duration-200 p-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-slate-900">Platform comparison</div>
              <div className="mt-1 text-sm text-slate-600">Posts published by day</div>
            </div>
            <div className="text-sm font-semibold text-slate-900">
              Total: {data?.total_published ?? 0}
            </div>
          </div>
          <div className="mt-4 h-[320px]">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={lineData}>
                <CartesianGrid stroke="#e2e8f0" strokeDasharray="4 4" />
                <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
                <Tooltip />
                <Legend />
                {data?.published_by_day_platform?.length
                  ? PLATFORM_KEYS.map((p) => (
                      <Line
                        key={p}
                        type="monotone"
                        dataKey={p}
                        stroke={platformColor(p)}
                        strokeWidth={2}
                        dot={false}
                        isAnimationActive
                      />
                    ))
                  : (
                      <Line
                        type="monotone"
                        dataKey="total"
                        stroke="#2563eb"
                        strokeWidth={2}
                        dot={false}
                        isAnimationActive
                      />
                    )}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </FadeIn>

        <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
          <FadeIn className="bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md transition-all duration-200 p-5">
            <div className="text-sm font-semibold text-slate-900">Success rate</div>
            <div className="mt-1 text-sm text-slate-600">Published vs failed</div>
            <div className="mt-4 h-[320px]">
              <ResponsiveContainer width="100%" height="100%">
                <RadarChart data={radarData}>
                  <PolarGrid />
                  <PolarAngleAxis dataKey="platform" />
                  <PolarRadiusAxis angle={30} domain={[0, 100]} />
                  <Radar
                    name="Success %"
                    dataKey="success"
                    stroke="#6366f1"
                    fill="#6366f1"
                    fillOpacity={0.25}
                    isAnimationActive
                  />
                  <Tooltip />
                </RadarChart>
              </ResponsiveContainer>
            </div>
          </FadeIn>

          <FadeIn className="bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md transition-all duration-200 p-5">
            <div className="text-sm font-semibold text-slate-900">Content type breakdown</div>
            <div className="mt-1 text-sm text-slate-600">Video vs Image vs Article per platform</div>
            <div className="mt-4 h-[320px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={barData}>
                  <CartesianGrid stroke="#e2e8f0" strokeDasharray="4 4" />
                  <XAxis dataKey="platform" tick={{ fontSize: 12 }} />
                  <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
                  <Tooltip />
                  <Legend />
                  <Bar dataKey="video" fill="#2563eb" radius={[6, 6, 0, 0]} />
                  <Bar dataKey="image" fill="#a855f7" radius={[6, 6, 0, 0]} />
                  <Bar dataKey="article" fill="#0ea5e9" radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </FadeIn>
        </div>

        <FadeIn className="bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md transition-all duration-200 p-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-slate-900">Daily activity heatmap</div>
              <div className="mt-1 text-sm text-slate-600">
                Publishing intensity by weekday and hour
              </div>
            </div>
            <div className="text-xs text-slate-500">Mon–Sun × 24h</div>
          </div>

          <div className="mt-4 overflow-x-auto">
            <div className="min-w-[980px]">
              <div className="grid grid-cols-[60px_repeat(24,1fr)] gap-1">
                <div />
                {Array.from({ length: 24 }, (_, h) => (
                  <div key={h} className="text-center text-[10px] font-semibold text-slate-500">
                    {h}
                  </div>
                ))}
                {heatmap.map((row, rIdx) => (
                  <>
                    <div
                      key={`lbl-${rIdx}`}
                      className="flex items-center text-xs font-semibold text-slate-600"
                    >
                      {weekdayLabels[rIdx]}
                    </div>
                    {row.map((v, h) => (
                      <div
                        key={`${rIdx}-${h}`}
                        title={`${weekdayLabels[rIdx]} ${h}:00 — ${v}`}
                        className={cx(
                          "h-6 rounded-md border border-slate-100",
                          intensityClass(v)
                        )}
                      />
                    ))}
                  </>
                ))}
              </div>
            </div>
          </div>
        </FadeIn>

        <FadeIn className="bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md transition-all duration-200 p-5">
          <div className="text-sm font-semibold text-slate-900">Top content</div>
          <div className="mt-1 text-sm text-slate-600">Most published items</div>
          <div className="mt-4 overflow-hidden rounded-2xl border border-slate-200">
            <div className="divide-y divide-slate-100">
              {(data?.top_content ?? []).map((t) => (
                <div key={t.id} className="flex items-center justify-between px-5 py-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-slate-900">
                      {t.title ?? "Untitled"}
                    </div>
                    <div className="mt-1 text-xs text-slate-500">{t.id}</div>
                  </div>
                  <div className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">
                    {t.published_count} published
                  </div>
                </div>
              ))}
              {!data?.top_content?.length ? (
                <div className="px-5 py-8 text-center text-sm text-slate-600">
                  No published content in this range yet.
                </div>
              ) : null}
            </div>
          </div>
        </FadeIn>
      </div>
      </main>
    </PageTransition>
  );
}

