"use client";

import { AnimatePresence, motion } from "framer-motion";
import { cn } from "@/components/ui/cn";
import { Button } from "@/components/ui/Button";

export function Dialog({
  open,
  title,
  description,
  confirmText = "Confirm",
  confirmVariant = "danger",
  onConfirm,
  onClose
}: {
  open: boolean;
  title: string;
  description?: string;
  confirmText?: string;
  confirmVariant?: "primary" | "secondary" | "danger" | "warning";
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
          role="dialog"
          aria-modal="true"
          aria-label={title}
        >
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 12 }}
            onClick={(e) => e.stopPropagation()}
            className={cn("w-full max-w-lg rounded-2xl border border-slate-200 bg-white p-5 shadow-xl")}
          >
            <div className="text-sm font-semibold text-slate-900">{title}</div>
            {description ? (
              <div className="mt-2 text-sm text-slate-600">{description}</div>
            ) : null}
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="secondary" onClick={onClose}>
                Cancel
              </Button>
              <Button
                variant={confirmVariant}
                onClick={() => {
                  onConfirm();
                  onClose();
                }}
              >
                {confirmText}
              </Button>
            </div>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

