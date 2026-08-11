export const env = {
  appName: process.env.NEXT_PUBLIC_APP_NAME ?? "ReForge",
  apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || "/ingest-reforge",
};

