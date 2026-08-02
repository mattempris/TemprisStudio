export interface StageSummary {
  raw_records: number;
  stripped_records: number;
  dedupe_threshold: number | null;
  dedupe_groups: number;
  dedupe_embeddings_ready: boolean;
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

// ── HRIS / spreadsheet ingestion ─────────────────────────────────────────────

export type HrisMappingField =
  | "job_title_col"
  | "job_description_col"
  | "job_level_col"
  | "headcount_col";

export interface HrisSuggestedMapping {
  job_title_col: string | null;
  job_description_col: string | null;
  job_level_col: string | null;
  headcount_col: string | null;
  /** Keyed by the same `*_col` names as the fields above. */
  confidence: Partial<Record<HrisMappingField, number>>;
  reasoning: Partial<Record<HrisMappingField, string>>;
}

export interface HrisPreview {
  file_id: string;
  row_count: number;
  columns: string[];
  preview: Record<string, unknown>[];
  suggested_mapping: HrisSuggestedMapping;
  /** Set client-side from the chosen file; the API does not echo it back. */
  filename?: string;
}

export interface HrisConfirmResult {
  records_added: number;
  total_records: number;
  rows_in_sheet: number;
  skipped_no_title: number;
  limited: boolean;
}

export interface EmbeddingModelInfo {
  name: string;
  dim: number;
  note: string;
  installed: boolean;
  loaded: boolean;
}

export interface EmbeddingSlot {
  current: string;
  selectable: boolean;
  models: EmbeddingModelInfo[];
}

/** Keyed by entity: job / skill / task. Only the job slot is selectable today. */
export type EmbeddingModelsInfo = Partial<Record<"job" | "skill" | "task", EmbeddingSlot>>;

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

// ── Per-tier clustering (steps 5/6/7) ────────────────────────────────────────

export type TierName = "profile" | "category" | "family";

export interface TierStatus {
  tier: TierName;
  ready_to_run: boolean;
  below: TierName | null;
  item_count: number | null;
  item_noun: string;
  built: boolean;
  analysed_k: number | null;
  /** False when the tier is large enough that stability needs its own pass. */
  stability_inline: boolean;
  confirmed: boolean;
  k: number | null;
  gate: number | null;
  n_routed: number;
  n_moved: number;
  max_k: number | null;
}

export interface StabilityBucket {
  from: number;
  to: number;
  count: number;
}

export interface TierPreview {
  tier: TierName;
  k: number;
  item_count: number;
  sizes: number[];
  singletons: number;
  largest: number;
  stability_included: boolean;
  gate?: number;
  n_routed?: number;
  pct_routed?: number;
  mean_stability?: number | null;
  min_stability?: number | null;
  distribution?: StabilityBucket[];
}

export interface TierClusterMember {
  item_id: string;
  label: string;
  stability_score: number | null;
  routed_by_llm: boolean;
  route_confidence: number | null;
  moved: boolean;
  moved_from: string | null;
}

export interface TierClusters {
  tier: TierName;
  k: number;
  gate: number;
  n_routed: number;
  n_moved: number;
  clusters: { id: number; name: string; size: number; members: TierClusterMember[] }[];
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

// ── Skills (step 8/9) and tasks (step 10) ────────────────────────────────────
// The two taxonomies are the same three-tier shape with a different leaf, so the
// browser component is shared and these types differ only in the leaf array.

export interface SkillsSummary {
  inferred_skills: number;
  profiles_covered: number;
  clustered: boolean;
  k_families: number | null;
  k_categories: number | null;
  k_clusters: number | null;
  named: boolean;
  /** Skill clusters that have generated proficiency-level criteria. */
  proficiency_definitions: number;
  /** (job profile, skill cluster) requirement rows produced by the auto-map. */
  profile_requirements: number;
  levels_assigned: number;
  audit: Record<string, number>;
}

export interface TasksSummary {
  inferred_tasks: number;
  profiles_covered: number;
  clustered: boolean;
  k_domains: number | null;
  k_categories: number | null;
  k_tasks: number | null;
  named: boolean;
  audit: Record<string, number>;
}

export interface TaxonomyLeaf {
  id: string;
  name: string;
  description: string;
  source_profile_key: string;
  stability_score: number | null;
  routed_by_llm: boolean;
  kind?: string;
  proportion?: number;
  headcount?: number | null;
  fte_equivalent?: number | null;
}

/** One node at any of the three tiers. Aggregates differ per entity: skills
 *  carry job counts and headcount, tasks carry time proportions and FTE. */
export interface TaxonomyNode {
  id: number;
  name: string;
  children?: TaxonomyNode[];
  leaves?: TaxonomyLeaf[];
  skill_count?: number;
  task_count?: number;
  jobs_requiring_count?: number;
  jobs_contributing?: number;
  /** Skills: people in the jobs requiring this branch. Unioned, not summed. */
  headcount_requiring?: number | null;
  proportion_sum?: number;
  fte_equivalent?: number | null;
  proficiency_definitions?: Record<string, string>;
}

/** Each entity names its tiers differently on the wire: jobs use
 *  family/category/profile, skills family/category/cluster, tasks
 *  domain/category/task. All three variants are optional here and folded into
 *  one shape by toClusterPreview. */
export interface EntityClusterPreview {
  family_sizes?: number[];
  domain_sizes?: number[];
  category_sizes?: number[];
  profile_sizes?: number[];
  cluster_sizes?: number[];
  task_sizes?: number[];
  singleton_profiles?: number;
  singleton_clusters?: number;
  singleton_tasks?: number;
}

// ── 3rd-party taxonomy matching (step 11) ────────────────────────────────────

export interface MatchingSummary {
  matched_profiles: number;
  total_profiles: number;
  industries: string[];
  computed_at: string | null;
  summary: Record<string, number | null>;
}

export interface MatchCandidate {
  code: string;
  title: string;
  family_title: string;
  sub_family_title: string;
  cosine: number;
}

export interface TaxonomyMatch {
  profile_key: string;
  profile_title: string;
  matched: boolean;
  spec_code: string | null;
  spec_title: string | null;
  family_title: string | null;
  sub_family_title: string | null;
  cosine: number | null;
  confidence: number;
  rationale: string;
  runner_up_code: string | null;
  runner_up_title: string | null;
  level_code: string | null;
  level_title: string | null;
  level_stream: string | null;
  level_confidence: number;
  level_rationale: string;
  shortlist: MatchCandidate[];
  needs_review: boolean;
  review_reasons: string[];
  overridden_by_user: boolean;
}

export interface MatchBrowseRow {
  profile_key: string;
  profile_title: string;
  spec_code: string | null;
  spec_title: string | null;
  level_code: string | null;
  level_title: string | null;
  level_stream: string | null;
  cosine: number | null;
  confidence: number;
  needs_review: boolean;
  review_reasons: string[];
  overridden_by_user: boolean;
  headcount: number | null;
}

export interface MatchBrowseNode {
  name: string;
  profile_count: number;
  headcount: number | null;
  needs_review: number;
  sub_families?: MatchBrowseNode[];
  specializations?: {
    code: string;
    title: string;
    profiles: MatchBrowseRow[];
    profile_count: number;
    headcount: number | null;
    needs_review: number;
  }[];
}

export interface MatchBrowse {
  families: MatchBrowseNode[];
  unmatched: MatchBrowseRow[];
  has_headcount: boolean;
  summary: Record<string, number | null>;
}

export interface TaxonomySearchHit {
  code: string;
  title: string;
  family_title: string;
  sub_family_title: string;
  levels: { code: string; title: string }[];
}

// ── Combined final hierarchy ─────────────────────────────────────────────────

export interface OverviewProfile {
  profile_key: string;
  title: string;
  headcount: number | null;
  source_titles: string[];
  source_job_count: number;
  evaluation: { aggregate_score: number; level_name: string; stale: boolean } | null;
  skills: { cluster_id: number; cluster_name: string; assigned_level: string | null }[];
  skill_count: number;
  tasks: {
    name: string;
    description: string;
    proportion: number;
    cluster_id: number | null;
    cluster_name: string | null;
  }[];
  task_count: number;
  taxonomy_match: {
    spec_code: string | null;
    spec_title: string | null;
    family_title: string | null;
    level_code: string | null;
    level_title: string | null;
    confidence: number;
    needs_review: boolean;
    overridden_by_user: boolean;
  } | null;
}

export interface OverviewGroup {
  id: number;
  name: string;
  profile_count: number;
  headcount: number | null;
  source_job_count: number;
  mean_je_score: number | null;
  matched_count: number;
}

export interface Overview {
  families: (OverviewGroup & {
    categories: (OverviewGroup & { profiles: OverviewProfile[] })[];
  })[];
  totals: OverviewGroup & {
    families: number;
    categories: number;
    skills: number;
    tasks: number;
  };
  has_headcount: boolean;
  available: {
    evaluation: boolean;
    skills: boolean;
    tasks: boolean;
    taxonomy_match: boolean;
  };
}

// ── User-editable configuration (step 7 and step 9) ──────────────────────────

export interface JESubdomain {
  name: string;
  weight: number;
  /** Exactly 5 descriptors, one per score point 1-5. */
  rubric: string[];
}

export interface JEDomain {
  name: string;
  weight: number;
  subdomains: JESubdomain[];
}

export interface JELevelBand {
  name: string;
  min_score: number;
  max_score: number;
}

export interface JEFramework {
  domains: JEDomain[];
  level_bands: JELevelBand[];
}

export interface Boilerplate {
  client_company_description: string | null;
  diversity_statement: string | null;
  accent_color: string;
}

export interface ProfileSection {
  key: string;
  heading: string;
  include: boolean;
  guidance: string;
}

export interface ProfileSectionSpec {
  key: string;
  default_heading: string;
  shape: string;
  description: string;
  default_guidance: string;
  removable: boolean;
}

/** Config plus the catalogue, so the editor can describe each section and know
 *  which ones are required without duplicating the backend's list. */
export interface ProfileTemplate {
  sections: ProfileSection[];
  catalogue: ProfileSectionSpec[];
}

export interface ProficiencyLevel {
  name: string;
  ordinal: number;
  criteria: string;
  typical_autonomy?: string | null;
}

export interface ProficiencyTemplate {
  levels: ProficiencyLevel[];
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
