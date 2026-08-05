/** Work Architecture Studio types. Steps 1 (the graph) and 3 (AI opportunity). */

/** "action" is not a fourth hierarchy — it has one level and hangs off a single task
 *  cluster, appearing only when that cluster is opened. */
export type GraphEntity = "job" | "skill" | "task" | "action" | "process" | "unmapped";
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

export interface FilterOption {
  id: number;
  name: string;
  leaves: number;
  /** Categories only: which family they sit under. */
  family?: number;
}

export type GraphFilters = Record<
  string,
  { level_titles: Record<GraphLevel, string>; family: FilterOption[]; category: FilterOption[] }
>;

export interface GraphCut {
  levels: Record<string, GraphLevel>;
  /** Which hierarchies this cut drew — skills and tasks are mutually exclusive. */
  shown: string[];
  expanded: string[];
  nodes: GraphNode[];
  edges: GraphEdge[];
  has_headcount: boolean;
  has_opportunity: boolean;
  totals: {
    nodes: number;
    edges: number;
    actions: number;
    processes: number;
    /** Steps that matched no task cluster — work no job description described. */
    unmapped_steps: number;
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
  level_titles: Record<string, Record<GraphLevel, string>>;
  /** What the later steps are gated on, so the page needs one call rather than one
   *  per step to know what is unlocked. */
  clusters_assessed: number;
  skills_written: number;
  /** Work Design needs one lever; either kind counts. */
  agents_defined: number;
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

// ---------------------------------------------------------------------------
// Step 5 — personal productivity
// ---------------------------------------------------------------------------
export interface SkillSummary {
  id: string;
  name: string;
  description: string;
  hook: string;
}

export interface ProductivityTask {
  cluster_id: number;
  cluster: string;
  domain: string;
  task_names: string[];
  proportion: number;
  augmentation: number;
  /** augmentation × share of the week — where a prompt helps this person most. */
  rank_score: number;
  skill: SkillSummary | null;
}

export interface ProductivityRole {
  profile_key: string;
  title: string;
  family: string;
  category: string;
  tasks: ProductivityTask[];
  skills: number;
  assessed_share: number;
}

export interface ProductivityReport {
  roles: ProductivityRole[];
  families: string[];
  total_skills: number;
  eligible_pairs: number;
}

export interface SkillEstimate {
  eligible: number;
  skills: number;
  calls: number;
  est_usd: number;
  basis: string;
}

export interface SkillDetail extends SkillSummary {
  profile_key: string;
  role_title: string;
  task_cluster_id: number;
  cluster_name: string;
  blob_path: string;
  rank_score: number;
  generated_at: string | null;
  markdown: string;
}

// ---------------------------------------------------------------------------
// Step 6 — agent definitions
// ---------------------------------------------------------------------------
export interface AgentSummary {
  id: string;
  name: string;
  purpose: string;
  n_capabilities: number;
  human_in_the_loop: boolean;
}

export interface AgentCandidate {
  cluster_id: number;
  cluster: string;
  category: string;
  domain: string;
  automation: number;
  augmentation: number;
  /** Automation weighted by how much time the cluster consumes — the build-first order. */
  time_released: number;
  roles: number;
  top_roles: string[];
  n_actions: number;
  agent: AgentSummary | null;
}

export interface ContextDoc {
  id: string;
  kind: string;
  filename: string;
  chars: number;
}

export interface AgentCandidateReport {
  clusters: AgentCandidate[];
  unit: string;
  total_agents: number;
  domains: string[];
  context_documents: ContextDoc[];
  estimate_all: { agents: number; calls: number; est_usd: number; basis: string };
}

export interface AgentDetail extends AgentSummary {
  task_cluster_id: number;
  cluster_name: string;
  slug: string;
  blob_path: string;
  time_released: number;
  time_released_unit: string;
  automation_pct: number;
  generated_at: string | null;
  /** The eight-section specification. Shape varies by section, so it stays loose. */
  spec: Record<string, unknown>;
  sections: string[];
}

export interface AgentImpactReport {
  agents: {
    id: string;
    name: string;
    cluster: string;
    purpose: string;
    automation: number;
    time_released: number;
    n_capabilities: number;
    human_in_the_loop: boolean;
  }[];
  unit: string;
  totals: {
    agents: number;
    time_released: number;
    supervised: number;
    unsupervised: number;
    mean_automation: number;
  };
}

// ---------------------------------------------------------------------------
// Steps 2 and 4 — processes
// ---------------------------------------------------------------------------
export interface ProcessStep {
  sequence: number;
  name: string;
  description: string;
  actor: string;
  system: string;
  automated: boolean;
  handoff: boolean;
  sign_off: boolean;
  /** Null means no task cluster is a plausible home — work the job descriptions
   *  never mentioned, which is a finding rather than a failure. */
  task_cluster_id: number | null;
  task_cluster_name: string;
  match_cosine: number;
  routed_by_llm: boolean;
  match_confidence: number | null;
  match_reasoning: string;
}

export interface ProcessAssessment {
  process_id: string;
  as_is_steps: number;
  as_is_manual_touchpoints: number;
  as_is_actors: number;
  as_is_sign_offs: number;
  as_is_handoffs: number;
  to_be_steps: number;
  to_be_manual_touchpoints: number;
  to_be_actors: number;
  to_be_sign_offs: number;
  effort_reduction_pct: number;
  elapsed_reduction_pct: number;
  as_is_narrative: string;
  to_be_narrative: string;
  what_changes: string[];
  risks: string[];
  prerequisites: string[];
  computed_at: string | null;
}

export interface ProcessRecord {
  id: string;
  filename: string;
  process_name: string;
  summary: string;
  /** Whether the source document carried a reliable sequence. A diagram gives labels
   *  but not arrows, so this is surfaced rather than assumed. */
  ordering_confidence: "high" | "medium" | "low";
  steps: ProcessStep[];
  uploaded_at: string | null;
  mapped_at: string | null;
  unmatched_steps: number;
  manual_steps: number;
  handoffs: number;
  sign_offs: number;
  assessment: ProcessAssessment | null;
}

export interface ProcessReport {
  processes: ProcessRecord[];
  supported_extensions: string[];
  assessed: number;
  has_opportunity: boolean;
}

// ---------------------------------------------------------------------------
// Step 7 — future roles
// ---------------------------------------------------------------------------
export interface FutureRoleDesign {
  profile_key: string;
  title: string;
  evolution_today: string;
  evolution_after_automation: string;
  evolution_future: string;
  future_purpose: string;
  future_responsibilities: string[];
  absorbed_tasks: string[];
  deepened_tasks: string[];
  skills_to_build: string[];
  deliberate_practice: string[];
  automation_pct: number;
  time_released_pct: number;
  computed_at: string | null;
}

export interface FutureRoleCandidate {
  profile_key: string;
  title: string;
  family: string;
  category: string;
  automation: number;
  augmentation: number;
  time_released_pct: number;
  n_tasks: number;
  absorbed: string[];
  agents: string[];
  design: FutureRoleDesign | null;
}

export interface FutureRoleReport {
  roles: FutureRoleCandidate[];
  families: string[];
  designed: number;
  has_strategic_context: boolean;
  estimate_all: { roles: number; calls: number; est_usd: number; basis: string };
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

/** The subset of the pipeline's JobHandle that Work Architecture Studio needs. */
export interface JobHandleLike {
  job_id: string;
  stage: string;
  websocket_url: string;
}
