import type {
  Boilerplate,
  ClusterEntity,
  ClusterPreview,
  DedupePreview,
  EmbeddingModelsInfo,
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
  TierClusters,
  TierName,
  TierPreview,
  TierStatus,
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

    /** Which embedding models exist per entity, and what is installed/loaded. */
    embeddingModels: () => request<EmbeddingModelsInfo>(`${base}/embedding-models`),

    startDedupeBuild: (opts?: { embedding_model?: string | null; device?: string | null }) =>
      request<JobHandle>(
        `${base}/dedupe/build${qs({ embedding_model: opts?.embedding_model, device: opts?.device })}`,
        { method: "POST" },
      ),
    dedupePreview: (threshold: number) =>
      request<DedupePreview>(`${base}/dedupe/preview?threshold=${threshold}`),
    confirmDedupe: (threshold: number) =>
      request<{ groups: number; threshold: number }>(`${base}/dedupe/confirm`, {
        method: "POST",
        body: JSON.stringify({ threshold }),
      }),

    startNormalize: (workers?: number) =>
      request<JobHandle>(`${base}/normalize${qs({ workers })}`, { method: "POST" }),

    startClusterBuild: (opts?: { embedding_model?: string | null; device?: string | null }) =>
      request<JobHandle>(
        `${base}/cluster/build${qs({ embedding_model: opts?.embedding_model, device: opts?.device })}`,
        { method: "POST" },
      ),
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

    /** Steps 5/6/7 — one tier of the hierarchy. Same shape for profiles,
     *  categories and families. */
    /** Every tier of every hierarchy in one request. The wizard needs all nine to
     *  decide what is runnable, and asking per tier re-read the whole project state
     *  nine times. */
    allTierStatus: () =>
      request<Record<ClusterEntity, Partial<Record<TierName, TierStatus>>>>(
        `${base}/cluster/tiers/status`,
      ),

    // One client for nine steps: three hierarchies (job, skill, task) x three
    // tiers, all served by the same entity-parameterised routes.
    tier: (entity: ClusterEntity, tier: TierName) => {
      const t = `${base}/cluster/${entity}/tier/${tier}`;
      return {
        status: () => request<TierStatus>(`${t}/status`),
        build: (opts?: { device?: string | null; embedding_model?: string | null }) =>
          request<JobHandle>(
            `${t}/build${qs({ device: opts?.device, embedding_model: opts?.embedding_model })}`,
            { method: "POST" },
          ),
        preview: (k: number) => request<TierPreview>(`${t}/preview?k=${k}`),
        analyse: (k: number) =>
          request<JobHandle>(`${t}/analyse`, { method: "POST", body: JSON.stringify({ k }) }),
        gate: (gate: number) => request<TierPreview>(`${t}/gate?gate=${gate}`),
        confirm: (k: number, gate: number, workers?: number) =>
          request<JobHandle>(`${t}/confirm${qs({ workers })}`, {
            method: "POST",
            body: JSON.stringify({ k, gate }),
          }),
        clusters: () => request<TierClusters>(`${t}/clusters`),
        rename: (clusterId: number, name: string) =>
          request<unknown>(`${t}/rename`, {
            method: "POST",
            body: JSON.stringify({ cluster_id: clusterId, name }),
          }),
      };
    },

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

    startProfileGeneration: (workers?: number) =>
      request<JobHandle>(`${base}/profiles/generate${qs({ workers })}`, { method: "POST" }),
    // Its own call, not a flag on generation: editing the framework and
    // re-levelling must not mean rewriting every document.
    startJobEvaluation: (workers?: number) =>
      request<JobHandle>(`${base}/evaluation/run${qs({ workers })}`, { method: "POST" }),
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

/** The per-tier client, so components can take one without re-deriving its shape. */
export type TierApi = ReturnType<ReturnType<typeof pipelineApi>["tier"]>;
