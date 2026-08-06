import type {
  DesignedJob,
  Levers,
  PoolResult,
  TargetProfile,
  WorkDesignFacetOptions,
  WorkDesignFacets,
  WorkDesignStatus,
} from "../types/workDesign";

/**
 * Work Design Studio's API client.
 *
 * Its own module rather than more methods on `workforceApi`: this studio has a dozen
 * endpoints of its own and a different prefix, and the existing client is already long.
 * Downloads are plain `<a href>` links, never fetches — the browser handles the save dialog
 * and the filename comes from Content-Disposition, the same convention the workforce client
 * documents for its skill and agent downloads.
 */

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = body.detail;
    throw new Error(
      typeof detail === "string" ? detail : detail?.message ?? res.statusText,
    );
  }
  return res.json() as Promise<T>;
}

export interface ApplyRequest extends WorkDesignFacets {
  agent_ids: string[];
  skill_ids: string[];
  uplift?: number;
  /** The job open in the panel. Its own allocation must not drain the pool twice. */
  editing_job_id?: string | null;
}

export interface JobPayload {
  title: string;
  headcount: number;
  notes?: string;
  facets?: WorkDesignFacets;
  selected_agent_ids: string[];
  selected_skill_ids: string[];
  tasks: {
    id?: string | null;
    task_cluster_id: number | null;
    cluster_name: string;
    name: string;
    description?: string;
    origin: string;
    hours_per_week: number;
    agent_id?: string | null;
    source_profile_key?: string | null;
    contributing_tasks?: string[];
    lever_ids?: string[];
    automation_pct?: number | null;
    augmentation_pct?: number | null;
  }[];
}

export function workDesignApi(clientSlug: string, projectSlug: string) {
  const base = `/projects/${clientSlug}/${projectSlug}/work-design`;
  return {
    status: () => request<WorkDesignStatus>(`${base}/status`),
    facets: () => request<WorkDesignFacetOptions>(`${base}/facets`),
    levers: () => request<Levers>(`${base}/levers`),

    /** The pool with levers applied and designed jobs drained out of it. */
    apply: (body: ApplyRequest) =>
      request<PoolResult>(`${base}/apply`, { method: "POST", body: JSON.stringify(body) }),

    jobs: () =>
      request<{ jobs: DesignedJob[]; hours_per_fte_week: number; augmentation_uplift: number }>(
        `${base}/jobs`,
      ),
    job: (id: string) => request<DesignedJob>(`${base}/jobs/${id}`),
    createJob: (body: JobPayload) =>
      request<DesignedJob>(`${base}/jobs`, { method: "POST", body: JSON.stringify(body) }),
    updateJob: (id: string, body: JobPayload) =>
      request<DesignedJob>(`${base}/jobs/${id}`, { method: "PUT", body: JSON.stringify(body) }),
    deleteJob: (id: string) =>
      request<{ deleted: string; title: string; hours_returned_to_pool: number; jobs: number }>(
        `${base}/jobs/${id}`,
        { method: "DELETE" },
      ),

    target: () => request<TargetProfile>(`${base}/target`),

    /** One role's task profile as job lines, scaled to the designed headcount. */
    importPreview: (profileKey: string, headcount: number) =>
      request<{
        profile_key: string;
        headcount: number;
        lines: JobPayload["tasks"];
        total_hours_per_week: number;
      }>(`${base}/import/${profileKey}?headcount=${headcount}`),

    /** A link, not a fetch — see the note at the top of this module. */
    xlsxUrl: () => `/api${base}/export.xlsx`,
  };
}
