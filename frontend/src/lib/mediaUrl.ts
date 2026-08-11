/**
 * Detect URLs that are video (or Cloudinary video delivery) — do not pass these to `next/image`.
 */
export function isProbablyVideoUrl(url: string | null | undefined): boolean {
  if (!url) return false;
  const lower = url.split("?")[0].toLowerCase();
  if (/\/video\/(upload|deliver)\//i.test(url)) return true;
  return /\.(mp4|m4v|webm|mov|mkv|avi|mpeg|mpg)(\?.*)?$/i.test(lower);
}
