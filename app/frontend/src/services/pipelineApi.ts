import type {
  Boilerplate,
  ClusterPreview,
  DedupePreview,
  EntityClusterPreview,
  HierarchyNode,
  HrisConfirmResult,
  HrisPreview,
  JEDetail,
  JEFramework,
  JobHandle,
  ProficiencyTemplate,
  ProfileSection,
  ProfileTemplate,
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

/** Build a query string from defined values only, so an omitted `workers` falls
 *  through to the server's configured default rather than sending "undefined". */
function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const pairs = Object.entries(params).filter(([, v]) => v !== undefined && v !== null);
  return pairs.length ? `?${pairs.map(([k, v]) => `${k}=${v}`).join("&")}` : "";
}

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

    startStrip: (workers?: number) =>
      request<JobHandle>(`${base}/strip${qs({ workers })}`, { method: "POST" }),

    startDedupeBuild: () => request<JobHandle>(`${base}/dedupe/build`, { method: "POST" }),
    dedupePreview: (threshold: number) =>
      request<DedupePreview>(`${base}/dedupe/preview?threshold=${threshold}`),
    confirmDedupe: (threshold: number) =>
      request<{ groups: number; threshold: number }>(`${base}/dedupe/confirm`, {
        method: "POST",
        body: JSON.stringify({ threshold }),
      }),

    startNormalize: (workers?: number) =>
      request<JobHandle>(`${base}/normalize${qs({ workers })}`, { method: "POST" }),

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

    /** Step 7's document boilerplate: fixed text and accent colour applied to
     *  every profile. Saving re-renders existing profiles (no LLM call needed). */
    getBoilerplate: () => request<Boilerplate>(`${base}/boilerplate`),
    putBoilerplate: (v: Boilerplate) =>
      request<{ saved: boolean; profiles_rerendered: number }>(`${base}/boilerplate`, {
        method: "PUT",
        body: JSON.stringify(v),
      }),

    /** Step 7: the user-defined job profile template — which sections a profile
     *  has, their headings, order and per-section generation guidance. */
    getProfileTemplate: (defaults = false) =>
      request<ProfileTemplate>(`${base}/profile-template${defaults ? "?defaults=true" : ""}`),
    putProfileTemplate: (sections: ProfileSection[]) =>
      request<{ saved: boolean; profiles_marked_stale: number }>(`${base}/profile-template`, {
        method: "PUT",
        body: JSON.stringify({ sections }),
      }),

    /** Step 7: the JE framework and level/score mapping the user defines.
     *  GET returns the shipped default until the project saves its own. */
    getJeFramework: (defaults = false) =>
      request<JEFramework>(`${base}/je-framework${defaults ? "?defaults=true" : ""}`),
    putJeFramework: (framework: JEFramework) =>
      request<{ saved: boolean }>(`${base}/je-framework`, {
        method: "PUT",
        body: JSON.stringify(framework),
      }),

    startProfileGeneration: (runJe = true, workers?: number) =>
      request<JobHandle>(`${base}/profiles/generate${qs({ run_je: runJe, workers })}`, {
        method: "POST",
      }),
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
      infer: (profileKeys?: string[], workers?: number) =>
        request<JobHandle>(`${base}/skills/infer${qs({ workers })}`, {
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
      generateProficiency: (workers?: number) =>
        request<JobHandle>(`${base}/skills/proficiency/generate${qs({ workers })}`, {
          method: "POST",
        }),
      getTemplate: (defaults = false) =>
        request<ProficiencyTemplate>(
          `${base}/skills/proficiency/template${defaults ? "?defaults=true" : ""}`,
        ),
      putTemplate: (template: ProficiencyTemplate) =>
        request<{ saved: boolean }>(`${base}/skills/proficiency/template`, {
          method: "PUT",
          body: JSON.stringify(template),
        }),
    },

    // ── Tasks (step 10) ──────────────────────────────────────────────────────
    tasks: {
      summary: () => request<TasksSummary>(`${base}/tasks/summary`),
      infer: (profileKeys?: string[], workers?: number) =>
        request<JobHandle>(`${base}/tasks/infer${qs({ workers })}`, {
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
      run: (
        body: { industries?: string[] | null; shortlist_size?: number; assign_level?: boolean },
        workers?: number,
      ) =>
        request<JobHandle>(`${base}/matching/run${qs({ workers })}`, {
          method: "POST",
          body: JSON.stringify(body),
        }),
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
