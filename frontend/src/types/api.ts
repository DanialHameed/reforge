export type HealthResponse = {
  name: string;
  env: string;
  version: string;
  evaluation_mode?: boolean;
};

export type User = {
  id: string;
  email: string;
  display_name: string;
  avatar_url: string | null;
  plan: string | null;
  llm_migration_feature_enabled: boolean;
};

export type AuthTokens = {
  access_token: string;
  refresh_token: string;
};

export type ContentItem = {
  id: string;
  user_id: string;
  title: string | null;
  original_file_url: string | null;
  file_type: string | null;
  file_size_mb: number | null;
  status: string;
  scheduled_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  platform_variants: PlatformVariant[];
};

export type PlatformVariant = {
  id: string;
  platform: string | null;
  caption: string | null;
  hashtags: string[] | null;
  metadata: Record<string, unknown> | null;
  media_url: string | null;
  scheduled_at: string | null;
  published_at: string | null;
  status: string;
  error_message: string | null;
  retry_count: number;
  manually_edited?: boolean;
  updated_at?: string | null;
};

export type Paginated<T> = {
  page: number;
  limit: number;
  total: number;
  items: T[];
};

export type AnalyticsSummary = {
  total_published: number;
  published_by_platform: Record<string, number>;
  published_by_day: Array<{ date: string; count: number }>;
  success_rate_by_platform: Record<string, number>;
  top_content: Array<{ id: string; title: string | null; published_count: number }>;
  // extra fields used by dashboard visuals
  published_by_day_platform?: Array<{ date: string; platform: string; count: number }>;
  content_type_breakdown?: Array<{ platform: string; content_type: string; count: number }>;
  published_heatmap?: Array<{ weekday: number; hour: number; count: number }>;
  range?: { days: number; start: string; end: string };
};

export type MetaPage = {
  page_id: string;
  page_name: string;
  has_instagram_business_account: boolean;
  instagram_business_account_id: string | null;
};

export type MetaPagesResponse = {
  pages: MetaPage[];
  selected_page_id: string | null;
};

