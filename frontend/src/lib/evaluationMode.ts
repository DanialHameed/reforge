/**
 * Frontend evaluation / demo safety flag.
 *
 * Must mirror backend ``EVALUATION_MODE`` for UX (disabled buttons). The
 * API still enforces 403 on destructive routes when only the server flag is
 * set—set **both** env vars for a consistent demo (see TROUBLESHOOTING.md).
 */
export function isEvaluationMode(): boolean {
  const v = (process.env.NEXT_PUBLIC_EVALUATION_MODE || "").trim().toLowerCase();
  return v === "true" || v === "1" || v === "yes";
}
