import type {
  AgentCandidateReport,
  AgentDetail,
  AgentImpactReport,
  ClusterOpportunityReport,
  GraphCut,
  GraphLevel,
  JobHandleLike,
  NodeDetail,
  OpportunityStatus,
  ProductivityReport,
  RoleOpportunityReport,
  SkillDetail,
  SkillEstimate,
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

    /** Step 5. Downloads are plain links rather than fetches, so the browser handles
     *  the save dialog and the filename comes from Content-Disposition. */
    productivityRoles: () => request<ProductivityReport>(`${base}/productivity/roles`),
    skillEstimate: (profileKey: string) =>
      request<SkillEstimate>(
        `${base}/productivity/estimate?profile_key=${encodeURIComponent(profileKey)}`,
      ),
    generateSkills: (body: {
      profile_key: string;
      cluster_ids?: number[];
      limit?: number;
      redo?: boolean;
    }) =>
      request<JobHandleLike>(`${base}/productivity/generate`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    skill: (skillId: string) => request<SkillDetail>(`${base}/productivity/skill/${skillId}`),
    skillDownloadUrl: (skillId: string) => `/api${base}/productivity/skill/${skillId}/download`,
    roleZipUrl: (profileKey: string) =>
      `/api${base}/productivity/role/${encodeURIComponent(profileKey)}/zip`,

    /** Step 6. */
    agentCandidates: (threshold = 0) =>
      request<AgentCandidateReport>(`${base}/agents?threshold=${threshold}`),
    generateAgents: (body: {
      cluster_ids?: number[];
      threshold?: number;
      limit?: number;
      redo?: boolean;
    }) =>
      request<JobHandleLike>(`${base}/agents/generate`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    agent: (agentId: string) => request<AgentDetail>(`${base}/agents/${agentId}`),
    agentDownloadUrl: (agentId: string) => `/api${base}/agents/${agentId}/download`,
    agentImpact: () => request<AgentImpactReport>(`${base}/agents/report/impact`),
    /** Multipart, so it bypasses the JSON `request` helper. */
    uploadCatalogue: async (file: File, kind = "software_catalogue") => {
      const body = new FormData();
      body.append("file", file);
      const res = await fetch(`/api${base}/agents/catalogue?kind=${kind}`, { method: "POST", body });
      if (!res.ok) {
        const j = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(typeof j.detail === "string" ? j.detail : res.statusText);
      }
      return res.json() as Promise<{
        filename: string;
        chars: number;
        truncated: boolean;
        cacheable: boolean;
        documents: number;
      }>;
    },
    deleteCatalogue: (docId: string) =>
      request<{ documents: number }>(`${base}/agents/catalogue/${docId}`, { method: "DELETE" }),
  };
}
