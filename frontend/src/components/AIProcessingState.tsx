"use client";

import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Loader2 } from "lucide-react";

const DEFAULT_STATES = [
  "Analyzing...",
  "Generating variants...",
  "Optimizing..."
] as const;

type AIProcessingStateProps = {
  className?: string;
  title?: string;
  states?: readonly string[];
};

function cx(...parts: Array<string | undefined | false>) {
  return parts.filter(Boolean).join(" ");
}

export function AIProcessingState({
  className,
  title = "AI is working",
  states = DEFAULT_STATES
}: AIProcessingStateProps) {
  const steps = useMemo(() => (states.length ? states : DEFAULT_STATES), [states]);
  const [idx, setIdx] = useState(0);

  useEffect(() => {
    let id: number | null = null;
    const intervalMs = () => (typeof document !== "undefined" && document.hidden ? 8000 : 2000);
    const arm = () => {
      if (id != null) window.clearInterval(id);
      if (typeof document !== "undefined" && document.hidden) {
        id = null;
        return;
      }
      id = window.setInterval(() => {
        setIdx((v) => (v + 1) % steps.length);
      }, intervalMs());
    };
    arm();
    const onVis = () => {
      arm();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      document.removeEventListener("visibilitychange", onVis);
      if (id != null) window.clearInterval(id);
    };
  }, [steps.length]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className={cx("ai-gradient-border bg-white/70 p-4", className)}
    >
      <motion.div
        animate={{
          boxShadow: [
            "0 0 0px rgba(59,130,246,0.0)",
            "0 0 24px rgba(59,130,246,0.18)",
            "0 0 0px rgba(59,130,246,0.0)"
          ]
        }}
        transition={{ duration: 1.8, ease: "easeInOut", repeat: Infinity }}
        className="rounded-2xl bg-white p-4"
      >
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm font-semibold tracking-tight text-slate-900">
              {title}
            </div>
            <AnimatePresence mode="wait">
              <motion.div
                key={idx}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.25 }}
                className="mt-1 text-sm text-slate-600"
              >
                {steps[idx]}
              </motion.div>
            </AnimatePresence>
          </div>

          <div className="flex items-center gap-3">
            <motion.div
              aria-hidden="true"
              animate={{ rotate: 360 }}
              transition={{ duration: 1.1, ease: "linear", repeat: Infinity }}
              className="text-slate-700"
            >
              <Loader2 className="h-5 w-5" />
            </motion.div>
            <motion.div
              aria-hidden="true"
              animate={{ rotate: 360 }}
              transition={{ duration: 2.4, ease: "linear", repeat: Infinity }}
              className="h-8 w-8 rounded-full bg-gradient-to-tr from-indigo-500/30 via-purple-500/30 to-fuchsia-500/30 blur-[0.2px]"
            />
          </div>
        </div>

        <div className="mt-4 space-y-3">
          <SkeletonLine wClass="w-11/12" delay={0} />
          <SkeletonLine wClass="w-10/12" delay={0.08} />
          <SkeletonLine wClass="w-9/12" delay={0.16} />
          <div className="pt-1">
            <SkeletonBlock />
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
}

function SkeletonLine({
  wClass,
  delay
}: {
  wClass: string;
  delay: number;
}) {
  return (
    <motion.div
      initial={{ width: "0%" }}
      animate={{ width: "100%" }}
      transition={{ duration: 0.6, ease: [0.2, 0.8, 0.2, 1], delay }}
      className={cx("overflow-hidden", wClass)}
    >
      <div className="ai-shimmer h-3 rounded-full bg-slate-100" />
    </motion.div>
  );
}

function SkeletonBlock() {
  return (
    <div className="ai-shimmer h-20 rounded-xl bg-slate-100" />
  );
}

