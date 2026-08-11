"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { cn } from "@/components/ui/cn";
import { Button } from "@/components/ui/Button";

/**
 * Replacement for ``window.prompt(...)`` when the operator needs to pick a
 * date/time to (re)schedule a post. The native prompt:
 *
 *   * Returns a free-form string we then have to feed to ``new Date(...)``
 *     (silently produces ``Invalid Date`` for any typo).
 *   * Cannot be styled, focus-trapped, or made dismissable on backdrop
 *     click.
 *   * Is blocked by some popup blockers / keyboard-only flows.
 *
 * This dialog uses a real ``<input type="datetime-local">`` so the browser
 * validates and renders a calendar picker, gives a "Clear schedule"
 * affordance, and surfaces an inline error for unparseable input. The
 * ``onConfirm`` callback receives a UTC ISO string or ``null`` (clear).
 */
export function SchedulePromptDialog({
  open,
  title,
  description,
  initialValue,
  confirmText = "Save",
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  description?: string;
  /** Pre-filled ``yyyy-MM-ddTHH:mm`` value, or ``null``. */
  initialValue?: string | null;
  confirmText?: string;
  onConfirm: (iso: string | null) => void;
  onClose: () => void;
}) {
  const [value, setValue] = useState<string>(initialValue ?? "");
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Reset whenever the dialog re-opens so a stale value is not carried
  // between separate "reschedule" interactions.
  useEffect(() => {
    if (open) {
      setValue(initialValue ?? "");
      setError(null);
      const t = window.setTimeout(() => inputRef.current?.focus(), 30);
      return () => window.clearTimeout(t);
    }
  }, [open, initialValue]);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed) {
      onConfirm(null);
      onClose();
      return;
    }
    const dt = new Date(trimmed);
    if (Number.isNaN(dt.getTime())) {
      setError("Please enter a valid date and time.");
      return;
    }
    onConfirm(dt.toISOString());
    onClose();
  };

  const clearAndSave = () => {
    onConfirm(null);
    onClose();
  };

  return (
    <AnimatePresence>
      {open ? (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-end justify-center bg-black/30 p-6 md:items-center"
          onClick={onClose}
          role="dialog"
          aria-modal="true"
          aria-label={title}
        >
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 12 }}
            onClick={(e) => e.stopPropagation()}
            className={cn(
              "w-full max-w-lg rounded-2xl border border-slate-200 bg-white p-5 shadow-xl"
            )}
          >
            <div className="text-sm font-semibold text-slate-900">{title}</div>
            {description ? (
              <div className="mt-2 text-sm text-slate-600">{description}</div>
            ) : null}

            <div className="mt-4">
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                Date &amp; time
              </label>
              <input
                ref={inputRef}
                type="datetime-local"
                value={value}
                onChange={(e) => {
                  setValue(e.target.value);
                  if (error) setError(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    submit();
                  }
                }}
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-slate-400"
              />
              {error ? (
                <div className="mt-2 text-xs font-semibold text-rose-700">{error}</div>
              ) : null}
            </div>

            <div className="mt-5 flex flex-wrap justify-end gap-2">
              <Button variant="secondary" onClick={onClose}>
                Cancel
              </Button>
              <Button variant="warning" onClick={clearAndSave}>
                Clear schedule
              </Button>
              <Button variant="primary" onClick={submit}>
                {confirmText}
              </Button>
            </div>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
