"use client";

import React from "react";
import { motion } from "framer-motion";
import { Loader2, FilePenLine, CalendarClock, Rocket } from "lucide-react";
import { api } from "@/lib/api";
import type { ContentItem, Paginated } from "@/types/api";
import Link from "next/link";
import { FadeIn, PageTransition, fadeUpItem } from "@/components/Motion";
import ContentStatusBadge from "@/components/ContentStatusBadge";
import { PlatformHealthStrip } from "@/components/PlatformHealthStrip";
import { useAuth } from "@/hooks/useAuth";

type StatCardProps = {
  label: string;
  value: string;
  icon: React.ReactNode;
  iconBg: string;
  trend?: string;
};

const cardVariants = {
  hidden: { opacity: 0, scale: 0.95 },
  show: { opacity: 1, scale: 1 }
} as const;

const tableVariants = {
  hidden: { opacity: 0, y: 8 },
  show: { opacity: 1, y: 0 }
} as const;

function StatCard({ label, value, icon, iconBg, trend }: StatCardProps) {
  return (
    <motion.div
      variants={cardVariants}
      whileHover={{ y: -4, transition: { duration: 0.2 } }}
      className="relative overflow-hidden rounded-2xl border border-gray-100 bg-white p-6 shadow-sm transition-all duration-200 hover:shadow-md"
    >
      <div
        className="pointer-events-none absolute -right-6 -top-6 h-24 w-24 rounded-full opacity-[0.07] blur-sm"
        style={{ background: "radial-gradient(circle, currentColor, transparent 70%)" }}
      />
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm font-medium text-gray-500">{label}</p>
          <p className="mt-1 text-3xl font-bold text-gray-900">{value}</p>
        </div>
        <div className={`shrink-0 rounded-xl p-3 ${iconBg}`}>{icon}</div>
      </div>
      {trend ? (
        <p className="mt-2 text-xs font-medium text-green-600">
          ↑ {trend} from last week
        </p>
      ) : null}
    </motion.div>
  );
}

type RecentContentRow = {
  id: string;
  title: string;
  updatedAt: string;
  status: string;
};

function RecentContentTable({ rows }: { rows: RecentContentRow[] }) {
  return (
    <motion.div
      variants={tableVariants}
      className="bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md transition-all duration-200"
    >
      <div className="border-b border-slate-200 px-6 py-4">
        <h2 className="text-base font-semibold text-slate-900">Recent Content</h2>
        <p className="mt-1 text-sm text-slate-600">
          Latest items across your platforms.
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wide text-slate-500">
            <tr className="border-b border-slate-200">
              <th className="px-6 py-3 font-medium">Title</th>
              <th className="px-6 py-3 font-medium">Updated</th>
              <th className="px-6 py-3 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b border-slate-100 last:border-0">
                <td className="px-6 py-3 font-medium text-slate-900">
                  <Link href={`/content/${r.id}`} className="hover:underline">
                    {r.title}
                  </Link>
                </td>
                <td className="px-6 py-3 text-slate-700">{r.updatedAt}</td>
                <td className="px-6 py-3">
                  <ContentStatusBadge
                    status={r.status}
                    isFallback={r.status.includes("fallback")}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </motion.div>
  );
}

export default function DashboardPage() {
  const { user } = useAuth();
  const [items, setItems] = React.useState<ContentItem[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .get<Paginated<ContentItem>>("/api/v1/content?limit=8")
      .then((res) => {
        if (cancelled) return;
        setItems(res.items ?? []);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Failed to load dashboard");
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const processing = items.filter((i) => i.status === "processing").length;
  const drafts = items.filter((i) => i.status === "draft").length;
  const scheduled = items.filter((i) => i.status === "scheduled").length;
  const published = items.filter((i) => i.status === "published").length;

  const statCards: StatCardProps[] = [
    {
      label: "In processing",
      value: String(processing),
      icon: <Loader2 className="h-6 w-6" aria-hidden />,
      iconBg: "bg-blue-50 text-blue-600"
    },
    {
      label: "Drafts",
      value: String(drafts),
      icon: <FilePenLine className="h-6 w-6" aria-hidden />,
      iconBg: "bg-amber-50 text-amber-700"
    },
    {
      label: "Scheduled",
      value: String(scheduled),
      icon: <CalendarClock className="h-6 w-6" aria-hidden />,
      iconBg: "bg-violet-50 text-violet-700"
    },
    {
      label: "Published",
      value: String(published),
      icon: <Rocket className="h-6 w-6" aria-hidden />,
      iconBg: "bg-emerald-50 text-emerald-700"
    }
  ];

  const recentRows: RecentContentRow[] = items.slice(0, 8).map((i) => ({
    id: i.id,
    title: (i.title && String(i.title).trim()) || "Untitled",
    updatedAt: i.updated_at ? new Date(i.updated_at).toLocaleString() : "—",
    status: i.status
  }));

  const firstName = (user?.display_name || user?.email || "").split(/[\s@]/)[0];

  return (
    <PageTransition>
      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      <div className="relative overflow-hidden rounded-3xl border border-slate-200/70 bg-gradient-to-br from-indigo-600 via-indigo-600 to-purple-600 px-5 py-8 shadow-sm sm:px-8 sm:py-10">
        <div
          className="pointer-events-none absolute -right-16 -top-16 h-64 w-64 rounded-full bg-white/10 blur-2xl"
          aria-hidden
        />
        <div
          className="pointer-events-none absolute -bottom-20 left-1/3 h-56 w-56 rounded-full bg-fuchsia-400/20 blur-2xl"
          aria-hidden
        />
        <div className="relative flex flex-col gap-6 sm:flex-row sm:items-end sm:justify-between">
          <div className="flex flex-col gap-2">
            <span className="w-fit rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-semibold text-white/90 backdrop-blur">
              {loading ? "Syncing…" : error ? "Backend connection issue" : "Live data"}
            </span>
            <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">
              {firstName ? `Welcome back, ${firstName}` : "Dashboard"}
            </h1>
            <p className="max-w-md text-sm text-indigo-100 sm:text-base">
              A high-signal overview of your content pipeline across every platform.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <motion.div whileTap={{ scale: 0.95 }}>
              <Link
                href="/content/upload"
                className="rounded-xl bg-white px-4 py-2.5 text-sm font-semibold text-indigo-700 shadow-sm transition-all duration-200 hover:bg-indigo-50"
              >
                Upload
              </Link>
            </motion.div>
            <motion.div whileTap={{ scale: 0.95 }}>
              <Link
                href="/content"
                className="rounded-xl border border-white/30 bg-white/10 px-4 py-2.5 text-sm font-semibold text-white backdrop-blur transition-all duration-200 hover:bg-white/20"
              >
                View all
              </Link>
            </motion.div>
          </div>
        </div>
      </div>

      <FadeIn className="mt-6 flex flex-col gap-6 sm:mt-8">
        <div className="grid grid-cols-2 gap-4 sm:gap-6 lg:grid-cols-4">
          {statCards.map((c) => (
            <motion.div key={c.label} variants={fadeUpItem}>
              <StatCard {...c} />
            </motion.div>
          ))}
        </div>

        <PlatformHealthStrip />

        {error ? (
          <motion.div
            variants={tableVariants}
            className="bg-white rounded-xl border border-rose-200 shadow-sm hover:shadow-md transition-all duration-200 p-6 text-rose-900"
          >
            <div className="font-semibold">Couldn’t load dashboard</div>
            <div className="mt-1 text-sm text-rose-800">{error}</div>
            <div className="mt-3 text-sm text-rose-800">
              Make sure you’re logged in and the backend is running at{" "}
              <code className="rounded bg-white/60 px-1 py-0.5">NEXT_PUBLIC_API_BASE_URL</code>{" "}
              (e.g. <code className="rounded bg-white/60 px-1 py-0.5">/ingest-reforge</code>).
            </div>
          </motion.div>
        ) : (
          <RecentContentTable rows={recentRows} />
        )}
      </FadeIn>
      </main>
    </PageTransition>
  );
}

