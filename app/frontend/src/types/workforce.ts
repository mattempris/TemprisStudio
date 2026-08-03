/** Workforce Studio types. Step 1 (the work architecture graph) only, so far. */

export type GraphEntity = "job" | "skill" | "task";
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
}

export interface GraphEdge {
  source: string;
  target: string;
  weight: number;
}

export interface GraphCut {
  levels: Record<GraphEntity, GraphLevel>;
  expanded: string[];
  nodes: GraphNode[];
  edges: GraphEdge[];
  has_headcount: boolean;
  totals: { nodes: number; edges: number; leaves: Record<GraphEntity, number> };
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
}

/** The subset of the pipeline's JobHandle that Workforce Studio needs. */
export interface JobHandleLike {
  job_id: string;
  stage: string;
  websocket_url: string;
}
