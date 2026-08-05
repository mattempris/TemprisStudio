import type {
  AgentCandidateReport,
  AgentDetail,
  AgentImpactReport,
  ClusterOpportunityReport,
  FutureRoleReport,
  GraphCut,
  GraphFilters,
  GraphLevel,
  JobHandleLike,
  NodeDetail,
  OpportunityStatus,
  ProcessReport,
  ProductivityReport,
  RoleOpportunityReport,
  SkillDetail,
  SkillEstimate,
  WorkforceStatus,
} from "../types/workforce";
import type { Studio } from "../stores/projectStore";
import type { StudioGate } from "../components/wizard/StudioToggle";

/**
 * What each gated studio needs, from one `/workforce/status` payload.
 *
 * Derived here rather than in each page because PipelinePage and WorkforcePage both render
 * the switcher, and two copies of this would eventually disagree about what "unlocked"
 * means — which is the thing the single `wfGate` fetch was already trying to avoid.
 */
export function studioGates(s: WorkforceStatus): Partial<Record<Studio, StudioGate>> {
  const levers = (s.agents_defined ?? 0) + (s.skills_written ?? 0);
  return {
    workforce: { ready: s.ready, missing: s.missing },
    "work-design": {
      // Needs the architecture AND something to apply to it. An empty studio would open
      // onto a filter over work with no lever to pull.
      ready: s.ready && levers > 0,
      missing: s.ready ? ["an agent or an augmentation"] : s.missing,
    },
  };
}

/** Work Architecture Studio client. Mirrors pipelineApi's shape and its `/api` prefixing. */

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
    graph: (opts: {
      jobs: GraphLevel;
      skills: GraphLevel;
      tasks: GraphLevel;
      expand: string[];
      /** Which hierarchies to draw. Skills and tasks are never both. */
      show?: string[];
      /** Cluster ids per hierarchy at `filterLevel`. */
      jobFilter?: number[];
      skillFilter?: number[];
      taskFilter?: number[];
      filterLevel?: GraphLevel;
    }) => {
      const q = new URLSearchParams({
        jobs: opts.jobs,
        skills: opts.skills,
        tasks: opts.tasks,
      });
      if (opts.expand?.length) q.set("expand", opts.expand.join(","));
      if (opts.show?.length) q.set("show", opts.show.join(","));
      if (opts.jobFilter?.length) q.set("job_filter", opts.jobFilter.join(","));
      if (opts.skillFilter?.length) q.set("skill_filter", opts.skillFilter.join(","));
      if (opts.taskFilter?.length) q.set("task_filter", opts.taskFilter.join(","));
      if (opts.filterLevel) q.set("filter_level", opts.filterLevel);
      return request<GraphCut>(`${base}/graph?${q.toString()}`);
    },
    graphFilters: () => request<GraphFilters>(`${base}/graph/filters`),
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

    /** Steps 2 and 4. */
    processes: () => request<ProcessReport>(`${base}/processes`),
    uploadProcess: async (file: File) => {
      const body = new FormData();
      body.append("file", file);
      const res = await fetch(`/api${base}/processes/upload`, { method: "POST", body });
      if (!res.ok) {
        const j = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(typeof j.detail === "string" ? j.detail : res.statusText);
      }
      return res.json() as Promise<JobHandleLike>;
    },
    mapProcess: (id: string) =>
      request<JobHandleLike>(`${base}/processes/${id}/map`, { method: "POST" }),
    assessProcess: (id: string) =>
      request<JobHandleLike>(`${base}/processes/${id}/assess`, { method: "POST" }),
    deleteProcess: (id: string) =>
      request<{ processes: number }>(`${base}/processes/${id}`, { method: "DELETE" }),

    /** Step 7. */
    futureRoles: () => request<FutureRoleReport>(`${base}/future-roles`),
    designFutureRoles: (body: { profile_keys?: string[]; limit?: number; redo?: boolean }) =>
      request<JobHandleLike>(`${base}/future-roles/design`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    uploadStrategicContext: async (file: File) => {
      const body = new FormData();
      body.append("file", file);
      const res = await fetch(`/api${base}/agents/catalogue?kind=strategic_context`, {
        method: "POST",
        body,
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(typeof j.detail === "string" ? j.detail : res.statusText);
      }
      return res.json() as Promise<{ filename: string; chars: number; truncated: boolean }>;
    },
  };
}
