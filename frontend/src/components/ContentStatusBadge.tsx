import React from "react";

export interface ContentStatusBadgeProps {
  status: string;
  isFallback?: boolean;
  className?: string;
}

function cx(...parts: Array<string | undefined | false>) {
  return parts.filter(Boolean).join(" ");
}

const STATUS_CONFIG: Record<string, { label: string; classes: string }> = {
  pending: { label: "Pending", classes: "bg-gray-100 text-gray-600 border-gray-200" },
  processing: { label: "Processing...", classes: "bg-blue-100 text-blue-700 border-blue-200 animate-pulse" },
  completed: { label: "✓ Published", classes: "bg-green-100 text-green-700 border-green-200" },
  completed_fallback: { label: "⚠ Fallback", classes: "bg-amber-100 text-amber-700 border-amber-200" },
  error_fallback: { label: "✗ Error", classes: "bg-red-100 text-red-700 border-red-200" },
  timeout_fallback: { label: "⏱ Timed out", classes: "bg-red-100 text-red-800 border-red-200" },
  scheduled: { label: "🕐 Scheduled", classes: "bg-violet-100 text-violet-700 border-violet-200" },
  published: { label: "✓ Published", classes: "bg-emerald-100 text-emerald-700 border-emerald-200" },
  assisted: { label: "👤 Manual Review", classes: "bg-orange-100 text-orange-700 border-orange-200" },
  failed: { label: "✗ Failed", classes: "bg-red-100 text-red-700 border-red-200" },
  publishing: { label: "Publishing…", classes: "bg-blue-100 text-blue-800 border-blue-200" },
};

const ContentStatusBadge: React.FC<ContentStatusBadgeProps> = ({ status, isFallback, className }) => {
  const s = String(status || "").toLowerCase();
  const config = STATUS_CONFIG[s] || {
    label: status || "Unknown",
    classes: "bg-gray-100 text-gray-500 border-gray-200",
  };

  const ariaLabel = isFallback ? `Status: ${config.label}. Fallback content` : `Status: ${config.label}`;

  return (
    <span
      className={cx(
        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold",
        config.classes,
        className
      )}
      aria-label={ariaLabel}
      title={ariaLabel}
    >
      {config.label}
    </span>
  );
};

ContentStatusBadge.displayName = "ContentStatusBadge";

export default ContentStatusBadge;
