/** Work Design Studio types. Mirrors app/services/workforce/work_design.py. */

export interface WorkDesignStatus {
  ready: boolean;
  missing: string[];
  checks: { name: string; ok: boolean; detail: string }[];
  task_clusters: number;
  clusters_assessed: number;
  coverage_pct: number;
  agents: number;
  augmentations: number;
  designed_jobs: number;
  /** The graph blob's shape version. Below `graph_version_required` it carries no levers. */
  graph_version: number;
  graph_version_required: number;
  has_headcount: boolean;
  has_business_framework: boolean;
  hours_per_fte_week: number;
  augmentation_uplift: number;
}

export interface FacetOption {
  id: number;
  name: string;
  leaves: number;
  family?: number;
}

export interface WorkDesignFacetOptions {
  level_titles: Record<string, Record<string, string>>;
  has_business_framework: boolean;
  has_headcount: boolean;
  job: { family: FacetOption[]; category: FacetOption[] };
  task: { family: FacetOption[]; category: FacetOption[] };
  business_framework?: {
    level_1: { value: string; headcount: number }[];
    level_2: { value: string; parent: string; headcount: number }[];
    level_3: { value: string; parent: string; grandparent: string; headcount: number }[];
  };
}

/** The facet selection. Empty arrays mean everything, per the app's convention. */
export interface WorkDesignFacets {
  job_family_ids: number[];
  job_category_ids: number[];
  task_family_ids: number[];
  task_category_ids: number[];
  business_level_1: string[];
  business_level_2: string[];
  business_level_3: string[];
}

export const EMPTY_FACETS: WorkDesignFacets = {
  job_family_ids: [],
  job_category_ids: [],
  task_family_ids: [],
  task_category_ids: [],
  business_level_1: [],
  business_level_2: [],
  business_level_3: [],
};

export interface PoolRole {
  job_cluster: number;
  profile_key: string;
  title: string;
  hours_per_week: number;
}

export interface PoolCluster {
  cluster_id: number;
  name: string;
  category_id: number;
  category: string;
  domain_id: number;
  domain: string;
  /** What the pool draws — remaining after levers and allocation. */
  hours_per_week: number;
  fte: number | null;
  share_pct: number;
  /** One typical holder's time on this work. The rate a drop uses. */
  hours_per_holder_week: number;
  /** `false` means unknown opportunity, and both scores are null. Never treat as zero. */
  assessed: boolean;
  automation: number | null;
  augmentation: number | null;
  n_roles: number;
  roles: PoolRole[];
  // Present once levers have been applied.
  as_is_hours_per_week?: number;
  removed_by_automation_hours_per_week?: number;
  freed_by_augmentation_hours_per_week?: number;
  to_be_hours_per_week?: number;
  retained_automatable_hours_per_week?: number;
  residual_augmentation_pct?: number;
  absorbed_by?: string[];
  augmented_by?: string[];
  augmentation_coverage_pct?: number;
  allocated_hours_per_week?: number;
  remaining_hours_per_week?: number;
  fully_allocated?: boolean;
}

export interface PoolTotals {
  as_is_hours_per_week: number;
  removed_by_automation_hours_per_week: number;
  freed_by_augmentation_hours_per_week: number;
  oversight_hours_per_week: number;
  to_be_hours_per_week: number;
  net_change_hours_per_week: number;
  net_change_pct: number;
  net_fte: number | null;
  allocated_hours_per_week: number;
  remaining_hours_per_week: number;
  /** Should read ~0. The four terms adding back to the starting total, stated not implied. */
  conservation_check: number;
}

export interface AddedLine {
  id: string;
  name: string;
  description: string;
  origin: "agent_oversight";
  agent_id: string;
  task_cluster_id: number;
  cluster_name: string;
  hours_per_week: number;
  basis: string;
}

export interface PoolResult {
  unit: string;
  has_headcount: boolean;
  hours_per_fte_week: number;
  basis?: string;
  threshold?: number;
  uplift?: number;
  sample?: {
    job_profiles: number;
    headcount: number;
    capacity_hours_per_week: number;
    shown_hours_per_week: number;
    sample_hours_per_week: number;
    shown_pct_of_week: number;
    task_clusters: number;
    assessed_clusters: number;
    unassessed_hours_per_week: number;
    partial_profiles: number;
  };
  clusters: PoolCluster[];
  allocated_clusters?: PoolCluster[];
  added?: AddedLine[];
  agents?: {
    id: string;
    name: string;
    cluster_id: number;
    cluster: string;
    removed_hours_per_week: number;
    oversight_hours_per_week: number;
    oversight_source: string;
    net_hours_per_week: number;
  }[];
  augmentations?: { id: string; name: string; role_title: string; cluster_id: number }[];
  totals: PoolTotals;
  skipped_agents?: { id: string; name: string; reason: string }[];
  skipped_augmentations?: { id: string; name: string; reason: string }[];
  warnings: string[];
}

export interface LeverAgent {
  id: string;
  name: string;
  cluster_id: number;
  cluster: string;
  automation: number;
  human_in_the_loop: boolean;
  oversight_fraction: number;
  /** "specification" when the model judged it for this agent, "fallback" when assumed. */
  oversight_source: "specification" | "fallback";
  oversight_tasks: { name: string; definition: string; pct_of_absorbed_time: number }[];
}

export interface LeverAugmentation {
  id: string;
  name: string;
  role_title: string;
  profile_key: string;
  cluster_id: number;
  cluster: string;
  rank_score: number;
}

export interface Levers {
  uplift: number;
  threshold: number;
  agents: LeverAgent[];
  augmentations: LeverAugmentation[];
}

export interface DesignedTaskLine {
  id: string;
  task_cluster_id: number | null;
  cluster_name: string;
  name: string;
  description: string;
  origin: "as_is" | "agent_oversight" | "manual";
  hours_per_week: number;
  agent_id: string | null;
  source_profile_key: string | null;
  contributing_tasks: string[];
  lever_ids: string[];
  automation_pct: number | null;
  augmentation_pct: number | null;
}

export interface Capacity {
  headcount: number;
  hours_per_fte_week: number;
  capacity_hours_per_week: number;
  assigned_hours_per_week: number;
  fill_pct: number | null;
  over_capacity: boolean;
  over_by_hours_per_week: number;
  over_by_fte: number | null;
  spare_hours_per_week: number;
  /** The studio's primary output: what this work actually needs. */
  required_headcount: number | null;
  message: string;
}

export interface DesignedJob {
  id: string;
  title: string;
  headcount: number;
  facets: WorkDesignFacets;
  selected_agent_ids: string[];
  selected_skill_ids: string[];
  imported_from_profile_key: string | null;
  tasks: DesignedTaskLine[];
  notes: string;
  profile_doc: { title: string; stale: boolean; generated_at: string } | null;
  created_at: string;
  updated_at: string;
  stale: boolean;
  stale_reason: string;
  capacity: Capacity;
}

export interface TargetProfile {
  lines: {
    key: string;
    task_cluster_id: number | null;
    name: string;
    origin: string;
    hours_per_week: number;
    fte: number | null;
    jobs: string[];
  }[];
  totals: {
    hours_per_week: number;
    fte: number | null;
    jobs: number;
    headcount: number;
    oversight_hours_per_week: number;
  };
}
