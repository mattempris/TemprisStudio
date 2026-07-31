import type {
  ClusterPreview,
  DedupePreview,
  HierarchyNode,
  JEDetail,
  JobHandle,
  ProfileRow,
  StageSummary,
} from "../types/pipeline";

class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: options?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = body.detail ?? body;
    const message = typeof detail === "string" ? detail : (detail?.message ?? res.statusText);
    throw new ApiError(message, res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

/** All pipeline endpoints are scoped to a client/project pair. */
export function pipelineApi(clientSlug: string, projectSlug: string) {
  const base = `/projects/${clientSlug}/${projectSlug}`;
  return {
    summary: () => request<StageSummary>(`${base}/summary`),

    uploadFiles: (files: File[]) => {
      const form = new FormData();
      files.forEach((f) => form.append("files", f));
      return request<{ added: unknown[]; errors: unknown[]; total_records: number }>(
        `${base}/ingest/files`,
        { method: "POST", body: form },
      );
    },

    uploadHris: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return request<{
        file_id: string;
        row_count: number;
        columns: string[];
        preview: Record<string, string>[];
        suggested_mapping: {
          job_title_col: string | null;
          job_description_col: string | null;
          job_level_col: string | null;
          headcount_col: string | null;
          confidence: Record<string, number>;
          reasoning: Record<string, string>;
        };
      }>(`${base}/ingest/hris/preview`, { method: "POST", body: form });
    },

    confirmHris: (body: {
      file_id: string;
      job_title_col: string;
      job_description_col?: string | null;
      job_level_col?: string | null;
      headcount_col?: string | null;
    }) =>
      request<{ records_added: number; total_records: number }>(`${base}/ingest/hris/confirm`, {
        method: "POST",
        body: JSON.stringify(body),
      }),

    startStrip: () => request<JobHandle>(`${base}/strip`, { method: "POST" }),

    startDedupeBuild: () => request<JobHandle>(`${base}/dedupe/build`, { method: "POST" }),
    dedupePreview: (threshold: number) =>
      request<DedupePreview>(`${base}/dedupe/preview?threshold=${threshold}`),
    confirmDedupe: (threshold: number) =>
      request<{ groups: number; threshold: number }>(`${base}/dedupe/confirm`, {
        method: "POST",
        body: JSON.stringify({ threshold }),
      }),

    startNormalize: () => request<JobHandle>(`${base}/normalize`, { method: "POST" }),

    startClusterBuild: () => request<JobHandle>(`${base}/cluster/build`, { method: "POST" }),
    clusterPreview: (k: { families: number; categories: number; profiles: number }) =>
      request<ClusterPreview>(
        `${base}/cluster/preview-cut?k_families=${k.families}&k_categories=${k.categories}&k_profiles=${k.profiles}`,
      ),
    confirmCluster: (body: { k_families: number; k_categories: number; k_profiles: number; gate?: number }) =>
      request<JobHandle>(`${base}/cluster/confirm`, { method: "POST", body: JSON.stringify(body) }),
    hierarchy: () => request<HierarchyNode>(`${base}/cluster/hierarchy`),
    rename: (level: "family" | "category" | "profile", clusterId: number, name: string) =>
      request<unknown>(`${base}/cluster/rename`, {
        method: "POST",
        body: JSON.stringify({ level, cluster_id: clusterId, name }),
      }),

    startProfileGeneration: (runJe = true) =>
      request<JobHandle>(`${base}/profiles/generate?run_je=${runJe}`, { method: "POST" }),
    listProfiles: () => request<{ profiles: ProfileRow[]; count: number }>(`${base}/profiles`),
    getProfile: (key: string) =>
      request<{ profile_key: string; title: string; content: Record<string, unknown>; html: string }>(
        `${base}/profiles/${key}`,
      ),
    getProfileJe: (key: string) => request<JEDetail>(`${base}/profiles/${key}/je`),
    exportUrl: (key: string, fmt: "html" | "docx" | "pdf") =>
      `/api${base}/profiles/${key}/export/${fmt}`,
    exportCapabilities: () =>
      request<{ html: boolean; docx: boolean; pdf: boolean }>(`${base}/export/capabilities`),
  };
}

export { ApiError };
