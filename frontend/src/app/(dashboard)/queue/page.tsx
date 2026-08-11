"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent
} from "@dnd-kit/core";
import {
  SortableContext,
  useSortable,
  horizontalListSortingStrategy
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

import { api } from "@/lib/api";
import type { ContentItem, Paginated } from "@/types/api";
import { ContentCard } from "@/components/queue/ContentCard";
import { AIProcessingState } from "@/components/AIProcessingState";
import { InstagramIcon, LinkedinIcon } from "@/components/queue/icons";
import { FadeIn, PageTransition, fadeUpItem } from "@/components/Motion";
import { Dialog } from "@/components/ui/Dialog";
import { SchedulePromptDialog } from "@/components/ui/SchedulePromptDialog";
import { useToast } from "@/components/ui/Toast";
import { isEvaluationMode } from "@/lib/evaluationMode";

type ViewMode = "cards" | "calendar";
type SortBy = "scheduled_at" | "created_at" | "title";

type AssistedItem = {
  id: string;
  platform: string | null;
  content_item_id: string;
  scheduled_at: string | null;
  caption: string | null;
  hashtags: string[] | null;
  media_url: string | null;
};

function cx(...parts: Array<string | undefined | false>) {
  return parts.filter(Boolean).join(" ");
}

function platformIcon(platform: string | null) {
  const p = (platform ?? "").toLowerCase();
  if (p === "instagram") return <InstagramIcon className="h-5 w-5" />;
  if (p === "linkedin") return <LinkedinIcon className="h-5 w-5" />;
  return <div className="h-5 w-5 rounded bg-slate-200" />;
}

function toPlatformDots(item: ContentItem) {
  const pv = item.platform_variants ?? [];
  return pv.map((v) => ({
    platform: (v.platform ?? "platform").slice(0, 2).toUpperCase(),
    status: v.status
  }));
}

function inDateRange(iso: string | null, start: string | null, end: string | null) {
  if (!start && !end) return true;
  if (!iso) return false;
  const d = new Date(iso).getTime();
  if (start) {
    const s = new Date(start).getTime();
    if (d < s) return false;
  }
  if (end) {
    const e = new Date(end).getTime();
    if (d > e) return false;
  }
  return true;
}

function uniq<T>(arr: T[]) {
  return Array.from(new Set(arr));
}

function MultiSelect({
  label,
  options,
  selected,
  onChange
}: {
  label: string;
  options: string[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <motion.button
        whileTap={{ scale: 0.95 }}
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md hover:bg-slate-50 transition-all duration-200 inline-flex items-center gap-2 px-3 py-2 text-sm font-semibold text-slate-900"
      >
        {label}
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
          {selected.length}
        </span>
      </motion.button>
      {open ? (
        <div className="absolute right-0 z-30 mt-2 w-64 rounded-2xl border border-slate-200 bg-white p-2 shadow-lg">
          <div className="max-h-60 overflow-auto">
            {options.map((o) => {
              const checked = selected.includes(o);
              return (
                <label
                  key={o}
                  className="flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2 hover:bg-slate-50"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={(e) => {
                      const next = e.target.checked
                        ? uniq([...selected, o])
                        : selected.filter((x) => x !== o);
                      onChange(next);
                    }}
                    className="h-4 w-4 rounded border-slate-300"
                  />
                  <span className="text-sm text-slate-800">{o}</span>
                </label>
              );
            })}
          </div>
          <div className="flex items-center justify-between px-2 pt-2">
            <button
              type="button"
              onClick={() => onChange([])}
              className="text-xs font-semibold text-slate-600 hover:text-slate-900"
            >
              Clear
            </button>
            <motion.button
              whileTap={{ scale: 0.95 }}
              type="button"
              onClick={() => setOpen(false)}
              className="rounded-xl bg-slate-900 px-3 py-2 text-xs font-semibold text-white hover:bg-slate-800 transition-all duration-200 shadow-sm"
            >
              Done
            </motion.button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function SortableCard({
  id,
  children
}: {
  id: string;
  children: React.ReactNode;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id });

  return (
    <motion.div
      ref={setNodeRef}
      style={{
        transform: CSS.Transform.toString(transform),
        transition
      }}
      layout
      {...attributes}
      {...listeners}
      animate={isDragging ? { rotate: 2, scale: 1.02 } : { rotate: 0, scale: 1 }}
      transition={{ type: "spring", stiffness: 500, damping: 35, mass: 0.6 }}
    >
      {children}
    </motion.div>
  );
}

function BulkBar({
  count,
  onRescheduleAll,
  onDeleteAll,
  onRetryAll,
  onClear,
  deleteDisabled
}: {
  count: number;
  onRescheduleAll: () => void;
  onDeleteAll: () => void;
  onRetryAll: () => void;
  onClear: () => void;
  deleteDisabled?: boolean;
}) {
  return (
    <div className="sticky bottom-6 z-40 mx-auto mt-6 w-full max-w-6xl px-4 sm:px-6">
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white/90 p-4 shadow-lg backdrop-blur"
      >
        <div className="text-sm font-semibold text-slate-900">
          {count} selected
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={onRescheduleAll}
            className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-900 hover:bg-slate-50"
          >
            Reschedule All
          </button>
          <button
            onClick={onRetryAll}
            className="rounded-xl border border-orange-200 bg-orange-50 px-3 py-2 text-sm font-semibold text-orange-900 hover:bg-orange-100"
          >
            Retry All
          </button>
          <button
            type="button"
            disabled={deleteDisabled}
            title={deleteDisabled ? "Deleting is disabled in evaluation mode" : undefined}
            onClick={onDeleteAll}
            className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-semibold text-rose-900 hover:bg-rose-100 disabled:pointer-events-none disabled:opacity-50"
          >
            Delete All
          </button>
          <button
            onClick={onClear}
            className="rounded-xl px-3 py-2 text-sm font-semibold text-slate-600 hover:text-slate-900"
          >
            Clear
          </button>
        </div>
      </motion.div>
    </div>
  );
}

function CalendarView({
  items
}: {
  items: ContentItem[];
}) {
  const [cursor, setCursor] = useState(() => new Date());
  const [openId, setOpenId] = useState<string | null>(null);

  const year = cursor.getFullYear();
  const month = cursor.getMonth();
  const first = new Date(year, month, 1);
  const startDay = first.getDay(); // 0=Sun
  const daysInMonth = new Date(year, month + 1, 0).getDate();

  const cells: Array<{ date: Date | null }> = [];
  for (let i = 0; i < startDay; i++) cells.push({ date: null });
  for (let d = 1; d <= daysInMonth; d++) cells.push({ date: new Date(year, month, d) });
  while (cells.length % 7 !== 0) cells.push({ date: null });

  const byDay = useMemo(() => {
    const map = new Map<string, ContentItem[]>();
    for (const it of items) {
      if (!it.scheduled_at) continue;
      const dt = new Date(it.scheduled_at);
      const key = `${dt.getFullYear()}-${dt.getMonth()}-${dt.getDate()}`;
      map.set(key, [...(map.get(key) ?? []), it]);
    }
    return map;
  }, [items]);

  function keyForDate(d: Date) {
    return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  }

  const openItem = openId ? items.find((i) => i.id === openId) ?? null : null;

  return (
    <div className="mt-6 rounded-2xl border border-slate-200 bg-white">
      <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
        <div className="text-sm font-semibold text-slate-900">
          {cursor.toLocaleString(undefined, { month: "long", year: "numeric" })}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setCursor(new Date(year, month - 1, 1))}
            className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-900 hover:bg-slate-50"
          >
            Prev
          </button>
          <button
            onClick={() => setCursor(new Date())}
            className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-900 hover:bg-slate-50"
          >
            Today
          </button>
          <button
            onClick={() => setCursor(new Date(year, month + 1, 1))}
            className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-900 hover:bg-slate-50"
          >
            Next
          </button>
        </div>
      </div>

      <div className="grid grid-cols-7 gap-px bg-slate-200">
        {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((d) => (
          <div key={d} className="bg-white px-3 py-2 text-xs font-semibold text-slate-600">
            {d}
          </div>
        ))}
        {cells.map((c, idx) => {
          const key = c.date ? keyForDate(c.date) : "";
          const dayItems = c.date ? byDay.get(key) ?? [] : [];
          return (
            <div key={idx} className="min-h-[110px] bg-white p-3">
              {c.date ? (
                <>
                  <div className="text-xs font-semibold text-slate-700">{c.date.getDate()}</div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {dayItems.slice(0, 8).map((it) => (
                      <button
                        key={it.id}
                        onClick={() => setOpenId(it.id)}
                        className={cx(
                          "h-2.5 w-2.5 rounded-full",
                          it.status === "scheduled"
                            ? "bg-violet-500"
                            : it.status === "processing"
                              ? "bg-blue-500"
                              : it.status === "failed"
                                ? "bg-rose-500"
                                : "bg-slate-300"
                        )}
                        aria-label={it.title ?? "Scheduled item"}
                      />
                    ))}
                  </div>
                </>
              ) : null}
            </div>
          );
        })}
      </div>

      <AnimatePresence>
        {openItem ? (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-end justify-center bg-black/30 p-6 md:items-center"
            onClick={() => setOpenId(null)}
          >
            <motion.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 12 }}
              onClick={(e) => e.stopPropagation()}
              className="rounded-2xl bg-white p-4 shadow-xl"
            >
              <div className="text-sm font-semibold text-slate-900">Preview</div>
              <div className="mt-2">
                <ContentCard
                  item={openItem}
                  selected={false}
                  platforms={toPlatformDots(openItem)}
                  actions={{
                    onEdit: () => {},
                    onReschedule: () => {},
                    onDelete: () => {},
                    onRetry: () => {},
                    onToggleSelect: () => {}
                  }}
                />
              </div>
              <div className="mt-3 flex justify-end">
                <Link
                  href={`/content/${openItem.id}`}
                  className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800"
                >
                  Open
                </Link>
              </div>
            </motion.div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

export default function QueuePage() {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));

  const [items, setItems] = useState<ContentItem[]>([]);
  const [assisted, setAssisted] = useState<AssistedItem[]>([]);
  const [assistedOpen, setAssistedOpen] = useState(false);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [view, setView] = useState<ViewMode>("cards");
  const [sortBy, setSortBy] = useState<SortBy>("scheduled_at");
  const [dateStart, setDateStart] = useState<string | null>(null);
  const [dateEnd, setDateEnd] = useState<string | null>(null);

  const [platformFilter, setPlatformFilter] = useState<string[]>([]);
  const [statusFilter, setStatusFilter] = useState<string[]>([]);

  const [selectedIds, setSelectedIds] = useState<Record<string, boolean>>({});
  const selected = Object.keys(selectedIds).filter((k) => selectedIds[k]);

  const [activeId, setActiveId] = useState<string | null>(null);

  // Modal-driven replacements for window.prompt / window.confirm.
  const [singleScheduleTargetId, setSingleScheduleTargetId] = useState<string | null>(null);
  const [bulkScheduleOpen, setBulkScheduleOpen] = useState(false);
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const toast = useToast();
  const evalDeleteLocked = isEvaluationMode();

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [contentRes, assistedRes] = await Promise.all([
        api.get<Paginated<ContentItem>>("/api/v1/content?limit=100"),
        api.get<{ items: AssistedItem[] }>("/api/v1/platforms/assisted/pending")
      ]);
      setItems(contentRes.items ?? []);
      setAssisted(assistedRes.items ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load queue");
    } finally {
      setLoading(false);
    }
  }, []);

  const anyQueueActivity = useMemo(() => {
    if (assisted.length > 0) return true;
    for (const it of items) {
      if (it.status === "processing") return true;
      for (const v of it.platform_variants ?? []) {
        const s = String(v.status || "").toLowerCase();
        if (s === "publishing") return true;
      }
    }
    return false;
  }, [items, assisted]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  /** Poll only when the queue has active work (processing, publishing, or assisted hand-offs). */
  useEffect(() => {
    if (!anyQueueActivity) return;
    void loadAll();
    let id: number | null = null;
    const intervalMs = () => (typeof document !== "undefined" && document.hidden ? 15_000 : 4000);
    const arm = () => {
      if (id != null) window.clearInterval(id);
      id = window.setInterval(() => void loadAll(), intervalMs());
    };
    arm();
    document.addEventListener("visibilitychange", arm);
    return () => {
      document.removeEventListener("visibilitychange", arm);
      if (id != null) window.clearInterval(id);
    };
  }, [anyQueueActivity, loadAll]);

  const allPlatforms = useMemo(() => {
    const set = new Set<string>();
    for (const it of items) {
      for (const v of it.platform_variants ?? []) set.add((v.platform ?? "platform").toLowerCase());
    }
    for (const a of assisted) if (a.platform) set.add(a.platform.toLowerCase());
    return Array.from(set).sort();
  }, [items, assisted]);

  const allStatuses = useMemo(() => {
    const set = new Set<string>();
    for (const it of items) set.add(it.status ?? "unknown");
    return Array.from(set).sort();
  }, [items]);

  const filtered = useMemo(() => {
    const list = items
      .filter((it) => inDateRange(it.scheduled_at ?? it.created_at ?? null, dateStart, dateEnd))
      .filter((it) => (statusFilter.length ? statusFilter.includes(it.status) : true))
      .filter((it) => {
        if (!platformFilter.length) return true;
        const platforms = (it.platform_variants ?? []).map((v) => (v.platform ?? "").toLowerCase());
        return platformFilter.some((p) => platforms.includes(p));
      });

    list.sort((a, b) => {
      if (sortBy === "title") return String(a.title ?? "").localeCompare(String(b.title ?? ""));
      if (sortBy === "created_at") return String(b.created_at ?? "").localeCompare(String(a.created_at ?? ""));
      // scheduled_at
      return String(b.scheduled_at ?? "").localeCompare(String(a.scheduled_at ?? ""));
    });

    return list;
  }, [items, dateStart, dateEnd, statusFilter, platformFilter, sortBy]);

  const drafts = useMemo(() => filtered.filter((i) => !i.scheduled_at), [filtered]);
  const scheduled = useMemo(() => filtered.filter((i) => Boolean(i.scheduled_at)), [filtered]);

  const anyProcessing = items.some((i) => i.status === "processing");

  async function reschedule(id: string, when: string | null) {
    await apiClientPatchContent(id, { scheduled_at: when });
    await loadAll();
  }

  async function apiClientPatchContent(id: string, payload: Record<string, unknown>) {
    const { apiClient } = await import("@/lib/api");
    await apiClient.patch(`/api/v1/content/${id}`, payload);
  }

  async function onDelete(id: string) {
    if (evalDeleteLocked) {
      toast.push({
        kind: "info",
        message: "Evaluation mode: delete is disabled for demo safety.",
      });
      return;
    }
    try {
      await apiClientDeleteContent(id);
      setSelectedIds((prev) => {
        const n = { ...prev };
        delete n[id];
        return n;
      });
      await loadAll();
      toast.push({ kind: "success", message: "Item deleted" });
    } catch (e) {
      toast.push({
        kind: "error",
        message: e instanceof Error ? e.message : "Failed to delete item",
      });
    }
  }

  async function apiClientDeleteContent(id: string) {
    const { apiClient } = await import("@/lib/api");
    await apiClient.delete(`/api/v1/content/${id}`);
  }

  async function onRetry(id: string) {
    try {
      await api.post(`/api/v1/content/${id}/process`, {});
      await loadAll();
      toast.push({ kind: "info", message: "Retry queued" });
    } catch (e) {
      toast.push({
        kind: "error",
        message: e instanceof Error ? e.message : "Failed to queue retry",
      });
    }
  }

  function onEdit(id: string) {
    window.location.assign(`/content/${id}`);
  }

  function onReschedule(id: string) {
    // Open the modal-driven scheduler. The actual reschedule happens in the
    // dialog's onConfirm callback below.
    setSingleScheduleTargetId(id);
  }

  async function performSingleReschedule(id: string, iso: string | null) {
    try {
      await reschedule(id, iso);
      toast.push({
        kind: "success",
        message: iso ? "Schedule updated" : "Schedule cleared",
      });
    } catch (e) {
      toast.push({
        kind: "error",
        message: e instanceof Error ? e.message : "Failed to update schedule",
      });
    }
  }

  async function onDragStart(e: DragStartEvent) {
    setActiveId(String(e.active.id));
  }

  async function onDragEnd(e: DragEndEvent) {
    const id = String(e.active.id);
    setActiveId(null);
    const overId = e.over?.id ? String(e.over.id) : null;
    if (!overId) return;

    // Dropping into a column sets/clears scheduled_at.
    if (overId === "column:drafts") {
      await reschedule(id, null);
    } else if (overId === "column:scheduled") {
      // Default: schedule 1 hour from now if not set.
      const it = items.find((x) => x.id === id);
      const next = it?.scheduled_at ?? new Date(Date.now() + 60 * 60 * 1000).toISOString();
      await reschedule(id, next);
    }
  }

  async function confirmAssisted(variantId: string) {
    try {
      await api.post(`/api/v1/platforms/assisted/${variantId}/confirm`, {});
      await loadAll();
      toast.push({ kind: "success", message: "Marked as posted" });
    } catch (e) {
      toast.push({
        kind: "error",
        message: e instanceof Error ? e.message : "Failed to confirm",
      });
    }
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast.push({ kind: "info", message: "Copied to clipboard" });
    } catch {
      toast.push({ kind: "error", message: "Copy failed (clipboard unavailable)" });
    }
  }

  function bulkRescheduleAll() {
    // Modal-driven (see <SchedulePromptDialog open={bulkScheduleOpen}> below).
    if (!selected.length) return;
    setBulkScheduleOpen(true);
  }

  async function performBulkReschedule(iso: string | null) {
    if (!selected.length) return;
    let failures = 0;
    for (const id of selected) {
      try {
        await apiClientPatchContent(id, { scheduled_at: iso });
      } catch {
        failures += 1;
      }
    }
    setSelectedIds({});
    await loadAll();
    if (failures === 0) {
      toast.push({
        kind: "success",
        message: iso
          ? `Scheduled ${selected.length} item${selected.length === 1 ? "" : "s"}`
          : `Cleared schedule for ${selected.length} item${selected.length === 1 ? "" : "s"}`,
      });
    } else {
      toast.push({
        kind: "error",
        message: `Failed to update ${failures} of ${selected.length} items`,
      });
    }
  }

  function bulkDeleteAll() {
    if (!selected.length) return;
    if (evalDeleteLocked) {
      toast.push({
        kind: "info",
        message: "Evaluation mode: delete is disabled for demo safety.",
      });
      return;
    }
    setBulkDeleteOpen(true);
  }

  async function performBulkDelete() {
    if (evalDeleteLocked) {
      toast.push({
        kind: "info",
        message: "Evaluation mode: bulk delete is disabled for demo safety.",
      });
      setBulkDeleteOpen(false);
      return;
    }
    if (!selected.length) return;
    let failures = 0;
    for (const id of selected) {
      try {
        await apiClientDeleteContent(id);
      } catch {
        failures += 1;
      }
    }
    setSelectedIds({});
    await loadAll();
    if (failures === 0) {
      toast.push({ kind: "success", message: `Deleted ${selected.length} item${selected.length === 1 ? "" : "s"}` });
    } else {
      toast.push({ kind: "error", message: `Failed to delete ${failures} of ${selected.length} items` });
    }
  }

  async function bulkRetryAll() {
    for (const id of selected) await api.post(`/api/v1/content/${id}/process`, {});
    setSelectedIds({});
    await loadAll();
  }

  const overlayItem = activeId ? items.find((i) => i.id === activeId) ?? null : null;

  return (
    <PageTransition>
      <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Queue</h1>
          <p className="mt-2 text-slate-600">
            Manage drafts, schedules, and assisted posts in one premium workspace.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <motion.button
            whileTap={{ scale: 0.95 }}
            onClick={() => setView("cards")}
            className={cx(
              "rounded-xl px-4 py-2 text-sm font-semibold transition-all duration-200",
              view === "cards"
                ? "bg-slate-900 text-white"
                : "bg-white border border-slate-200 shadow-sm hover:shadow-md hover:bg-slate-50 text-slate-900"
            )}
          >
            Card View
          </motion.button>
          <motion.button
            whileTap={{ scale: 0.95 }}
            onClick={() => setView("calendar")}
            className={cx(
              "rounded-xl px-4 py-2 text-sm font-semibold transition-all duration-200",
              view === "calendar"
                ? "bg-slate-900 text-white"
                : "bg-white border border-slate-200 shadow-sm hover:shadow-md hover:bg-slate-50 text-slate-900"
            )}
          >
            Calendar View
          </motion.button>
        </div>
      </div>

      {assisted.length ? (
        <div className="sticky top-[64px] z-40 mt-6">
          <div className="rounded-2xl border border-orange-200 bg-orange-50 p-4">
            <button
              onClick={() => setAssistedOpen((v) => !v)}
              className="flex w-full items-center justify-between gap-3 text-left"
            >
              <div className="text-sm font-semibold text-orange-900">
                You have {assisted.length} posts ready to publish manually
              </div>
              <div className="text-sm font-semibold text-orange-900">
                {assistedOpen ? "Hide" : "View"}
              </div>
            </button>

            <AnimatePresence>
              {assistedOpen ? (
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: 8 }}
                  className="mt-4 space-y-3"
                >
                  {assisted.map((a) => {
                    const caption = (a.caption ?? "").trim();
                    const hashtags = (a.hashtags ?? []).map((h) => (h.startsWith("#") ? h : `#${h}`)).join(" ");
                    const deepLink =
                      (a.platform ?? "").toLowerCase() === "linkedin"
                        ? "https://www.linkedin.com/post/new"
                        : "https://www.instagram.com/";
                    return (
                      <div key={a.id} className="rounded-2xl border border-orange-200 bg-white p-4">
                        <div className="flex flex-wrap items-start justify-between gap-4">
                          <div className="flex items-start gap-3">
                            <div className="text-orange-900">{platformIcon(a.platform)}</div>
                            <div className="min-w-0">
                              <div className="text-sm font-semibold text-slate-900">
                                {(a.platform ?? "platform").toUpperCase()}
                              </div>
                              <div className="mt-1 line-clamp-2 text-sm text-slate-700">
                                {caption || "No caption"}
                              </div>
                              <div className="mt-1 text-xs text-slate-500">
                                {a.scheduled_at ? new Date(a.scheduled_at).toLocaleString() : "—"}
                              </div>
                            </div>
                          </div>

                          <div className="flex flex-wrap items-center gap-2">
                            <button
                              onClick={() => void copy(caption)}
                              className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-900 hover:bg-slate-50"
                            >
                              Copy Caption
                            </button>
                            <button
                              onClick={() => void copy(hashtags)}
                              className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-900 hover:bg-slate-50"
                            >
                              Copy Hashtags
                            </button>
                            <a
                              href={deepLink}
                              target="_blank"
                              rel="noreferrer"
                              className="rounded-xl bg-slate-900 px-3 py-2 text-xs font-semibold text-white hover:bg-slate-800"
                            >
                              Open Platform
                            </a>
                            <button
                              onClick={() => void confirmAssisted(a.id)}
                              className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-900 hover:bg-emerald-100"
                            >
                              I Posted It
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </motion.div>
              ) : null}
            </AnimatePresence>
          </div>
        </div>
      ) : null}

      {anyProcessing ? (
        <div className="mt-6">
          <AIProcessingState title="Updating queue" />
        </div>
      ) : null}

      {/* Filter bar */}
      <div className="mt-6 bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md transition-all duration-200 flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <MultiSelect
            label="Platforms"
            options={allPlatforms.length ? allPlatforms : ["instagram", "linkedin"]}
            selected={platformFilter}
            onChange={setPlatformFilter}
          />
          <MultiSelect
            label="Status"
            options={allStatuses.length ? allStatuses : ["draft", "scheduled", "processing", "failed", "published"]}
            selected={statusFilter}
            onChange={setStatusFilter}
          />
          <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2">
            <div className="text-sm font-semibold text-slate-900">Date</div>
            <input
              type="date"
              value={dateStart ?? ""}
              onChange={(e) => setDateStart(e.target.value || null)}
              className="text-sm text-slate-700 outline-none"
            />
            <span className="text-slate-300">—</span>
            <input
              type="date"
              value={dateEnd ?? ""}
              onChange={(e) => setDateEnd(e.target.value || null)}
              className="text-sm text-slate-700 outline-none"
            />
          </div>
        </div>

        <div className="flex items-center gap-2">
          <div className="text-sm font-semibold text-slate-600">Sort</div>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as SortBy)}
            className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-900"
          >
            <option value="scheduled_at">Scheduled Time</option>
            <option value="created_at">Created</option>
            <option value="title">Title</option>
          </select>
          <motion.button
            whileTap={{ scale: 0.95 }}
            onClick={() => void loadAll()}
            className="bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md hover:bg-slate-50 transition-all duration-200 px-3 py-2 text-sm font-semibold text-slate-900"
          >
            Refresh
          </motion.button>
          <motion.div whileTap={{ scale: 0.95 }}>
            <Link
              href="/content/upload"
              className="rounded-xl bg-slate-900 px-3 py-2 text-sm font-semibold text-white hover:bg-slate-800 transition-all duration-200 shadow-sm"
            >
              Upload
            </Link>
          </motion.div>
        </div>
      </div>

      {error ? (
        <div className="mt-6 rounded-2xl border border-rose-200 bg-rose-50 p-6 text-rose-900">
          <div className="font-semibold">Couldn’t load queue</div>
          <div className="mt-1 text-sm text-rose-800">{error}</div>
        </div>
      ) : null}

      {view === "calendar" ? (
        <FadeIn className="mt-6">
          <motion.div variants={fadeUpItem}>
            <CalendarView items={filtered} />
          </motion.div>
        </FadeIn>
      ) : (
        <DndContext
          sensors={sensors}
          onDragStart={onDragStart}
          onDragEnd={onDragEnd}
        >
          <FadeIn className="mt-6">
            <motion.div variants={fadeUpItem} className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <DropColumn
                id="column:drafts"
                title="Drafts"
                hint="Drag into Scheduled to queue."
                items={drafts}
                selectedIds={selectedIds}
                setSelectedIds={setSelectedIds}
                onEdit={onEdit}
                onReschedule={onReschedule}
                onDelete={onDelete}
                onRetry={onRetry}
                activeId={activeId}
                deleteDisabled={evalDeleteLocked}
              />
              <DropColumn
                id="column:scheduled"
                title="Scheduled"
                hint="Drag into Drafts to unschedule."
                items={scheduled}
                selectedIds={selectedIds}
                setSelectedIds={setSelectedIds}
                onEdit={onEdit}
                onReschedule={onReschedule}
                onDelete={onDelete}
                onRetry={onRetry}
                activeId={activeId}
                deleteDisabled={evalDeleteLocked}
              />
            </motion.div>
          </FadeIn>

          <DragOverlay>
            {overlayItem ? (
              <div style={{ transform: "rotate(2deg)" }}>
                <ContentCard
                  item={overlayItem}
                  selected={Boolean(selectedIds[overlayItem.id])}
                  platforms={toPlatformDots(overlayItem)}
                  dragging
                  deleteDisabled={evalDeleteLocked}
                  actions={{
                    onEdit,
                    onReschedule,
                    onDelete,
                    onRetry,
                    onToggleSelect: (id, checked) =>
                      setSelectedIds((prev) => ({ ...prev, [id]: checked }))
                  }}
                />
              </div>
            ) : null}
          </DragOverlay>
        </DndContext>
      )}

      {selected.length ? (
        <BulkBar
          count={selected.length}
          onRescheduleAll={() => bulkRescheduleAll()}
          onDeleteAll={() => bulkDeleteAll()}
          onRetryAll={() => void bulkRetryAll()}
          onClear={() => setSelectedIds({})}
          deleteDisabled={evalDeleteLocked}
        />
      ) : null}

      <SchedulePromptDialog
        open={singleScheduleTargetId != null}
        title="Reschedule post"
        description="Pick a new date and time, or clear the schedule to move it back to drafts."
        initialValue={(() => {
          const it = items.find((x) => x.id === singleScheduleTargetId);
          return isoToLocalInput(it?.scheduled_at ?? null);
        })()}
        onConfirm={(iso) => {
          const id = singleScheduleTargetId;
          if (id) void performSingleReschedule(id, iso);
        }}
        onClose={() => setSingleScheduleTargetId(null)}
      />

      <SchedulePromptDialog
        open={bulkScheduleOpen}
        title={`Reschedule ${selected.length} item${selected.length === 1 ? "" : "s"}`}
        description="Pick a new date and time for every selected post, or clear the schedule for all of them."
        onConfirm={(iso) => void performBulkReschedule(iso)}
        onClose={() => setBulkScheduleOpen(false)}
      />

      <Dialog
        open={bulkDeleteOpen}
        title={`Delete ${selected.length} item${selected.length === 1 ? "" : "s"}?`}
        description="This cannot be undone. Drafts, scheduled posts, and any unpublished platform variants will be removed."
        confirmText="Delete"
        confirmVariant="danger"
        onConfirm={() => void performBulkDelete()}
        onClose={() => setBulkDeleteOpen(false)}
      />
      </main>
    </PageTransition>
  );
}

/**
 * Convert a UTC ISO string into the local-time format that an
 * ``<input type="datetime-local">`` accepts (``yyyy-MM-ddTHH:mm``). Returns
 * ``null`` for falsy / invalid input so the dialog defaults to empty.
 */
function isoToLocalInput(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function DropColumn({
  id,
  title,
  hint,
  items,
  selectedIds,
  setSelectedIds,
  onEdit,
  onReschedule,
  onDelete,
  onRetry,
  activeId,
  deleteDisabled
}: {
  id: string;
  title: string;
  hint: string;
  items: ContentItem[];
  selectedIds: Record<string, boolean>;
  setSelectedIds: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
  onEdit: (id: string) => void;
  onReschedule: (id: string) => void;
  onDelete: (id: string) => void;
  onRetry: (id: string) => void;
  activeId: string | null;
  deleteDisabled?: boolean;
}) {
  // Column itself acts as a drop target by being part of sortable context ids.
  const cardIds = items.map((i) => i.id);

  return (
    <div className="rounded-2xl border border-slate-200 bg-white">
      <div className="border-b border-slate-200 px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="text-sm font-semibold text-slate-900">{title}</div>
          <span className="rounded-full bg-slate-100 px-2 py-1 text-xs font-semibold text-slate-700">
            {items.length}
          </span>
        </div>
        <div className="mt-1 text-xs text-slate-500">{hint}</div>
      </div>

      <div className="p-4">
        <SortableContext items={[id, ...cardIds]} strategy={horizontalListSortingStrategy}>
          <div className="flex flex-wrap gap-4">
            {/* Invisible droppable anchor */}
            <SortableAnchor id={id} />

            {items.map((it) => (
              <SortableCard key={it.id} id={it.id}>
                <ContentCard
                  item={it}
                  selected={Boolean(selectedIds[it.id])}
                  platforms={toPlatformDots(it)}
                  dragging={activeId === it.id}
                  deleteDisabled={deleteDisabled}
                  actions={{
                    onEdit,
                    onReschedule,
                    onDelete,
                    onRetry,
                    onToggleSelect: (contentId, checked) =>
                      setSelectedIds((prev) => ({ ...prev, [contentId]: checked }))
                  }}
                />
              </SortableCard>
            ))}

            {!items.length ? (
              <div className="w-full rounded-2xl border border-dashed border-slate-200 bg-slate-50 p-6 text-sm text-slate-600">
                Drop items here.
              </div>
            ) : null}
          </div>
        </SortableContext>
      </div>
    </div>
  );
}

function SortableAnchor({ id }: { id: string }) {
  const { setNodeRef } = useSortable({ id });
  return <div ref={setNodeRef} className="h-0 w-0" aria-hidden="true" />;
}

