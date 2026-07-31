export interface StageSummary {
  raw_records: number;
  stripped_records: number;
  dedupe_threshold: number | null;
  dedupe_groups: number;
  normalized_profiles: number;
  clustered: boolean;
  k_families: number | null;
  k_categories: number | null;
  k_profiles: number | null;
  named: boolean;
  job_profiles: number;
  je_results: number;
  current_stage: string;
  active_job_id: string | null;
  active_job_stage: string | null;
}

export interface JobHandle {
  job_id: string;
  stage: string;
  websocket_url: string;
}

export type ProgressEvent =
  | { type: "stage_start"; stage: string; total: number; message: string }
  | { type: "progress"; stage: string; current: number; total: number; percent: number; message: string }
  | { type: "stage_complete"; stage: string; summary: Record<string, unknown> }
  | { type: "heartbeat"; stage: string | null; elapsed_seconds: number }
  | { type: "complete"; job_id: string; summary: Record<string, unknown> }
  | { type: "error"; stage: string | null; message: string; recoverable: boolean };

export interface DedupeGroupPreview {
  group_id: string;
  member_ids: string[];
  member_titles: string[];
  representative_id: string;
  avg_similarity: number;
  member_similarities: Record<string, number>;
}

export interface DedupePreview {
  threshold: number;
  total_items: number;
  group_count: number;
  duplicate_group_count: number;
  items_merged_away: number;
  groups: DedupeGroupPreview[];
}

export interface ClusterPreview {
  k_families: number;
  k_categories: number;
  k_profiles: number;
  family_sizes: number[];
  category_sizes: number[];
  profile_sizes: number[];
  singleton_profiles: number;
  largest_profile_size: number;
}

export interface ProfileRow {
  profile_key: string;
  title: string;
  breadcrumb: string[];
  stale: boolean;
  has_je: boolean;
  aggregate_score?: number;
  level_name?: string;
  spread_low?: number;
  spread_high?: number;
  je_stale?: boolean;
}

export interface JEDetail {
  profile_key: string;
  aggregate_score: number;
  level_name: string;
  stale: boolean;
  framework_version_hash: string;
  persona_scores: Record<string, number>;
  domain_rollups: Record<string, Record<string, number>>;
  personas: Record<string, Record<string, Record<string, number | string>>>;
  framework: {
    domains: { name: string; weight: number; subdomains: { name: string; weight: number; rubric: string[] }[] }[];
    level_bands: { name: string; min_score: number; max_score: number }[];
  };
}

export interface HierarchyNode {
  families: {
    id: number;
    name: string;
    categories: {
      id: number;
      name: string;
      profiles: {
        id: number;
        name: string;
        items: {
          item_id: string;
          source_titles: string[];
          headcount: number | null;
          stability_score: number | null;
          routed_by_llm: boolean;
          route_confidence: number | null;
          moved_by_llm: boolean;
          secondary_profile_name: string | null;
        }[];
      }[];
    }[];
  }[];
}
