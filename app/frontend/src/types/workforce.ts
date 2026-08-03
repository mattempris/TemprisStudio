/** Workforce Studio types. Steps 1 (the graph) and 3 (AI opportunity). */

/** "action" is not a fourth hierarchy — it has one level and hangs off a single task
 *  cluster, appearing only when that cluster is opened. */
export type GraphEntity = "job" | "skill" | "task" | "action";
/** Coarse to fine. What each level is *called* differs per entity and comes from
 *  the server in `level_title`, since a task's finest level is a cluster and a
 *  job's is a profile. */
export type GraphLevel = "family" | "category" | "profile";

export interface GraphNode {
  id: string;
  entity: GraphEntity;
  level: GraphLevel;
  cluster_id: number;
  name: string;
  level_title: string;
  /** Node size: headcount / skills / FTE, or their no-headcount equivalents. */
  metric: number;
  metric_title: string;
  members: number;
  /** How many leaf clusters this node rolls up. 1 means it is a leaf. */
  leaves: number;
  expandable: boolean;
  expanded: boolean;
  /** Step 3. `null` means unassessed, which is not the same as zero opportunity. */
  automation: number | null;
  augmentation: number | null;
  /** Share of this node's weight that sits in an assessed cluster. */
  opportunity_coverage: number;
  /** Action nodes only. */
  pct_of_task?: number;
  definition?: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  weight: number;
}

export interface GraphCut {
  levels: Record<string, GraphLevel>;
  expanded: string[];
  nodes: GraphNode[];
  edges: GraphEdge[];
  has_headcount: boolean;
  has_opportunity: boolean;
  totals: {
    nodes: number;
    edges: number;
    actions: number;
    leaves: Record<string, number>;
  };
}

export interface WorkforceStatus {
  ready: boolean;
  missing: string[];
  checks: { name: string; ok: boolean }[];
  graph_built: boolean;
  levels: GraphLevel[];
  entities: GraphEntity[];
  level_titles: Record<GraphEntity, Record<GraphLevel, string>>;
}

export interface NodeDetail {
  id: string;
  entity: GraphEntity;
  level: GraphLevel;
  level_title: string;
  name: string;
  metric: number;
  metric_title: string;
  members: number;
  leaves: number;
  children_title: string | null;
  children: { id: string; name: string; metric: number; members: number }[];
  /** Strongest relationships into the other hierarchies, keyed by entity. */
  related: Record<string, { name: string; weight: number }[]>;
  /** Step 3. Null until the assessment has run over something beneath this node. */
  opportunity: { automation: number; augmentation: number; coverage: number } | null;
  actions: ActionRow[];
  /** Action nodes only: the task cluster they belong to. */
  parent?: { id: string; name: string };
  definition?: string;
}

export interface ActionRow {
  name: string;
  definition: string;
  cluster: string;
  pct_of_task: number;
  automation: number;
  augmentation: number;
  weight: number;
  /** Set on the action whose modal this is, when viewing one action's siblings. */
  current?: boolean;
}

// ---------------------------------------------------------------------------
// Step 3 — AI opportunity assessment
// ---------------------------------------------------------------------------
export interface CostEstimate {
  clusters: number;
  calls: number;
  est_input_tokens: number;
  est_output_tokens: number;
  est_usd: number;
  basis: string;
}

export interface OpportunityStatus {
  task_clusters: number;
  assessed: number;
  remaining: number;
  actions: number;
  audit: OpportunityAudit;
  hours_per_fte_week: number;
  has_headcount: boolean;
  estimate_remaining: CostEstimate;
  estimate_all: CostEstimate;
}

export interface OpportunityAudit {
  clusters_assessed?: number;
  clusters_failed?: number;
  clamped?: number;
  retried?: number;
  max_pct_drift?: number;
  mean_automation?: number;
  mean_augmentation?: number;
  automation_p10?: number;
  automation_p90?: number;
  discriminating?: boolean;
  total_assessed?: number;
}

export interface ClusterOpportunity {
  cluster_id: number;
  name: string;
  category: string;
  domain: string;
  automation: number;
  augmentation: number;
  n_actions: number;
  clamped: boolean;
  roles: number;
  proportion_sum: number;
  fte: number | null;
  absorbable: number;
  actions: {
    name: string;
    definition: string;
    pct_of_task: number;
    automation: number;
    augmentation: number;
  }[];
}

export interface ClusterOpportunityReport {
  clusters: ClusterOpportunity[];
  has_headcount: boolean;
  unit: string;
  total_clusters: number;
  audit: OpportunityAudit;
}

export interface RoleTask {
  name: string;
  description: string;
  proportion: number;
  cluster_id: number;
  cluster: string;
  automation: number | null;
  augmentation: number | null;
  augmentation_weighted: number | null;
}

export interface RoleOpportunity {
  profile_key: string;
  title: string;
  headcount: number | null;
  automation: number;
  augmentation: number;
  coverage: number;
  n_tasks: number;
  fte_released: number | null;
  hours_per_week: number | null;
  tasks: RoleTask[];
}

export interface RoleOpportunityReport {
  roles: RoleOpportunity[];
  has_headcount: boolean;
  hours_per_fte_week: number;
  totals: {
    roles: number;
    roles_assessed: number;
    headcount: number | null;
    mean_automation: number;
    mean_augmentation: number;
    mean_coverage: number;
    fte_released: number | null;
    hours_per_week: number | null;
  };
}

/** The subset of the pipeline's JobHandle that Workforce Studio needs. */
export interface JobHandleLike {
  job_id: string;
  stage: string;
  websocket_url: string;
}
