import type {
  ClusterOpportunityReport,
  GraphCut,
  GraphLevel,
  JobHandleLike,
  NodeDetail,
  OpportunityStatus,
  RoleOpportunityReport,
  WorkforceStatus,
} from "../types/workforce";

/** Workforce Studio client. Mirrors pipelineApi's shape and its `/api` prefixing. */

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = body.detail ?? body;
    throw new Error(typeof detail === "string" ? detail : (detail?.message ?? res.statusText));
  }
  return res.json();
}

export function workforceApi(clientSlug: string, projectSlug: string) {
  const base = `/projects/${clientSlug}/${projectSlug}/workforce`;
  return {
    status: () => request<WorkforceStatus>(`${base}/status`),
    buildGraph: () => request<JobHandleLike>(`${base}/graph/build`, { method: "POST" }),
    /** One cut of the graph. The roll-up is server-side, so a zoom or an expand is a
     *  request rather than a re-layout of the whole dataset in the browser. */
    graph: (opts: { jobs: GraphLevel; skills: GraphLevel; tasks: GraphLevel; expand: string[] }) =>
      request<GraphCut>(
        `${base}/graph?jobs=${opts.jobs}&skills=${opts.skills}&tasks=${opts.tasks}` +
          (opts.expand.length ? `&expand=${encodeURIComponent(opts.expand.join(","))}` : ""),
      ),
    node: (nodeId: string) => request<NodeDetail>(`${base}/node/${encodeURIComponent(nodeId)}`),

    /** Step 3. `status` is free and carries the cost preview; `assess` spends. */
    opportunityStatus: () => request<OpportunityStatus>(`${base}/opportunity/status`),
    assess: (body: { cluster_ids?: number[]; limit?: number; redo?: boolean }) =>
      request<JobHandleLike>(`${base}/opportunity/assess`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    opportunityClusters: () => request<ClusterOpportunityReport>(`${base}/opportunity/clusters`),
    opportunityRoles: () => request<RoleOpportunityReport>(`${base}/opportunity/roles`),
  };
}
