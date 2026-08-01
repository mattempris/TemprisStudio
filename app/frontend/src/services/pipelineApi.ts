import type {
  ClusterPreview,
  DedupePreview,
  EntityClusterPreview,
  HierarchyNode,
  HrisConfirmResult,
  HrisPreview,
  JEDetail,
  JobHandle,
  MatchBrowse,
  MatchingSummary,
  Overview,
  ProfileRow,
  SkillsSummary,
  StageSummary,
  TasksSummary,
  TaxonomyMatch,
  TaxonomyNode,
  TaxonomySearchHit,
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

/**
 * Skills and tasks name their tiers differently from jobs (clusters / tasks
 * rather than profiles), but the k-selection panel is the same. Fold their
 * preview responses into the shared ClusterPreview shape here so the component
 * doesn't have to know which entity it is driving.
 */
function toClusterPreview(
  raw: EntityClusterPreview,
  k: { families: number; categories: number; profiles: number },
): ClusterPreview {
  const family = raw.family_sizes ?? raw.domain_sizes ?? [];
  const leaf = raw.profile_sizes ?? raw.cluster_sizes ?? raw.task_sizes ?? [];
  return {
    k_families: k.families,
    k_categories: k.categories,
    k_profiles: k.profiles,
    family_sizes: family,
    category_sizes: raw.category_sizes ?? [],
    profile_sizes: leaf,
    singleton_profiles:
      raw.singleton_profiles ?? raw.singleton_clusters ?? raw.singleton_tasks ?? 0,
    largest_profile_size: leaf.length ? Math.max(...leaf) : 0,
  };
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

    /** Upload a spreadsheet and get the AI-suggested column mapping to confirm.
     *  `header_row` exists because real HRIS exports often carry report titles
     *  or timestamps above the actual header. */
    uploadHris: (file: File, headerRow = 0) => {
      const form = new FormData();
      form.append("file", file);
      return request<HrisPreview>(
        `${base}/ingest/hris/preview?header_row=${headerRow}`,
        { method: "POST", body: form },
      ).then((r) => ({ ...r, filename: file.name }));
    },

    confirmHris: (body: {
      file_id: string;
      job_title_col: string;
      job_description_col?: string | null;
      job_level_col?: string | null;
      headcount_col?: string | null;
      header_row?: number;
      limit?: number | null;
    }) =>
      request<HrisConfirmResult>(`${base}/ingest/hris/confirm`, {
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

    /** The combined final hierarchy — every artifact joined onto its profile. */
    overview: () => request<Overview>(`${base}/overview`),

    exportManifest: () =>
      request<{ datasets: { key: string; name: string; rows: number; columns: number }[] }>(
        `${base}/exports/manifest`,
      ),
    workbookUrl: () => `/api${base}/exports/workbook.xlsx`,
    datasetCsvUrl: (dataset: string) => `/api${base}/exports/${dataset}.csv`,

    // ── Skills (steps 8-9) ───────────────────────────────────────────────────
    skills: {
      summary: () => request<SkillsSummary>(`${base}/skills/summary`),
      infer: (profileKeys?: string[]) =>
        request<JobHandle>(`${base}/skills/infer`, {
          method: "POST",
          body: JSON.stringify({ profile_keys: profileKeys ?? null }),
        }),
      buildTree: () => request<JobHandle>(`${base}/skills/cluster/build`, { method: "POST" }),
      preview: (k: { families: number; categories: number; profiles: number }) =>
        request<EntityClusterPreview>(
          `${base}/skills/cluster/preview-cut?k_families=${k.families}&k_categories=${k.categories}&k_clusters=${k.profiles}`,
        ).then((r) => toClusterPreview(r, k)),
      confirm: (k: { families: number; categories: number; profiles: number }, gate: number) =>
        request<JobHandle>(`${base}/skills/cluster/confirm`, {
          method: "POST",
          body: JSON.stringify({
            k_families: k.families,
            k_categories: k.categories,
            k_clusters: k.profiles,
            gate,
          }),
        }),
      taxonomy: () => request<{ families: TaxonomyNode[]; has_headcount: boolean }>(`${base}/skills/taxonomy`),
      generateProficiency: () =>
        request<JobHandle>(`${base}/skills/proficiency/generate`, { method: "POST" }),
    },

    // ── Tasks (step 10) ──────────────────────────────────────────────────────
    tasks: {
      summary: () => request<TasksSummary>(`${base}/tasks/summary`),
      infer: (profileKeys?: string[]) =>
        request<JobHandle>(`${base}/tasks/infer`, {
          method: "POST",
          body: JSON.stringify({ profile_keys: profileKeys ?? null }),
        }),
      buildTree: () => request<JobHandle>(`${base}/tasks/cluster/build`, { method: "POST" }),
      preview: (k: { families: number; categories: number; profiles: number }) =>
        request<EntityClusterPreview>(
          `${base}/tasks/cluster/preview-cut?k_domains=${k.families}&k_categories=${k.categories}&k_tasks=${k.profiles}`,
        ).then((r) => toClusterPreview(r, k)),
      confirm: (k: { families: number; categories: number; profiles: number }, gate: number) =>
        request<JobHandle>(`${base}/tasks/cluster/confirm`, {
          method: "POST",
          body: JSON.stringify({
            k_domains: k.families,
            k_categories: k.categories,
            k_tasks: k.profiles,
            gate,
          }),
        }),
      taxonomy: () =>
        request<{ domains: TaxonomyNode[]; has_headcount: boolean; total_proportion: number }>(
          `${base}/tasks/taxonomy`,
        ),
    },

    // ── 3rd-party taxonomy matching (step 11) ────────────────────────────────
    matching: {
      summary: () => request<MatchingSummary>(`${base}/matching/summary`),
      run: (body: { industries?: string[] | null; shortlist_size?: number; assign_level?: boolean }) =>
        request<JobHandle>(`${base}/matching/run`, { method: "POST", body: JSON.stringify(body) }),
      matches: (reviewOnly = false) =>
        request<{ matches: TaxonomyMatch[]; summary: Record<string, number | null> }>(
          `${base}/matching/matches?review_only=${reviewOnly}`,
        ),
      browse: () => request<MatchBrowse>(`${base}/matching/browse`),
      search: (q: string) =>
        request<{ total: number; results: TaxonomySearchHit[] }>(
          `${base}/matching/search?q=${encodeURIComponent(q)}`,
        ),
      override: (profileKey: string, specCode: string, levelCode?: string | null) =>
        request<TaxonomyMatch>(`${base}/matching/matches/${profileKey}/override`, {
          method: "POST",
          body: JSON.stringify({ spec_code: specCode, level_code: levelCode ?? null }),
        }),
    },
  };
}

/** Taxonomy metadata, not scoped to a project. */
export const taxonomyApi = {
  industries: () => request<{ industries: string[] }>(`/matching/industries`),
  info: (industries?: string[]) =>
    request<{
      specializations: number;
      title_variants: number;
      families: { name: string; specializations: number }[];
    }>(`/matching/taxonomy-info${industries?.length ? `?industries=${encodeURIComponent(industries.join(","))}` : ""}`),
};

export { ApiError };
