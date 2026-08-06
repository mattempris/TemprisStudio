import { useCallback, useEffect, useMemo, useState } from "react";
import { Play } from "lucide-react";
import { pipelineApi, taxonomyApi } from "../services/pipelineApi";
import { useJobStream } from "../hooks/useJobStream";
import { useSectionScroll } from "../hooks/useSectionScroll";
import type {
  HrisPreview,
  JEFramework,
  ProficiencyTemplate,
  Boilerplate,
  EmbeddingModelsInfo,
  ClusterEntity,
  TierName,
  TierStatus,
  ProfileSection,
  ProfileTemplate,
  MatchingSummary,
  Overview,
  ProfileRow,
  SkillsSummary,
  StageSummary,
  TasksSummary,
} from "../types/pipeline";
import { StageSection, type StageState } from "../components/wizard/StageSection";
import { ProgressBar } from "../components/wizard/ProgressBar";
import { JobPulse } from "../components/wizard/JobPulse";
import { ProceedToWorkforce, StudioToggle, type StudioGate } from "../components/wizard/StudioToggle";
import type { Studio } from "../stores/projectStore";
import { DemoReset } from "../components/wizard/DemoReset";
import { workforceApi, studioGates } from "../services/workforceApi";
import { DedupePanel } from "../components/pipeline/DedupePanel";
import { JEResultsBrowser } from "../components/pipeline/JEResultsBrowser";
import { ProfileSelect } from "../components/pipeline/ProfileSelect";
import { EntityTaxonomyStage } from "../components/pipeline/EntityTaxonomyStage";
import { EmbeddingOptions } from "../components/pipeline/EmbeddingOptions";
import { TierClusterStage } from "../components/pipeline/TierClusterStage";
import { MatchingPanel } from "../components/pipeline/MatchingPanel";
import { OverviewBrowser } from "../components/pipeline/OverviewBrowser";
import { ExportBar } from "../components/pipeline/ExportBar";
import { HrisMappingPanel } from "../components/pipeline/HrisMappingPanel";
import { JEFrameworkEditor, type LevelSuggestion } from "../components/pipeline/JEFrameworkEditor";
import { ProfileTemplateEditor } from "../components/pipeline/ProfileTemplateEditor";
import { BoilerplateEditor } from "../components/pipeline/BoilerplateEditor";
import { ProficiencyTemplateEditor } from "../components/pipeline/ProficiencyTemplateEditor";
import { Collapsible } from "../components/ui/Collapsible";
import { Button } from "../components/ui/Button";
import { Dropzone } from "../components/ui/Dropzone";
import { cn } from "../lib/cn";

const STAGES = [
  { id: "ingest", title: "Input assets", description: "Upload job description files or an HRIS spreadsheet." },
  { id: "strip", title: "Strip irrelevant content", description: "Remove company boilerplate, benefits and recruitment logistics — keeping only what already exists." },
  { id: "dedupe", title: "Deduplicate", description: "Group jobs that describe the same role, using a similarity threshold you control." },
  { id: "normalize", title: "Normalise descriptions", description: "Produce a structured summary per distinct job: purpose, key tasks, reporting line, budget." },
  { id: "cluster", title: "Job profiles", description: "Group the normalised jobs into job profiles, review what the model re-checked, and name them." },
  { id: "categories", title: "Job categories", description: "Group the confirmed job profiles into categories, then name them from what they contain." },
  { id: "families", title: "Job families", description: "Group the categories into job families — the top of the hierarchy." },
  { id: "profiles", title: "Job profile documents", description: "Write a job profile document for each confirmed profile, from the template and boilerplate you define here." },
  { id: "evaluation", title: "Job evaluation", description: "Score each profile document against your job evaluation framework and map it to a level." },
  { id: "skills", title: "Skills taxonomy", description: "Infer the attributes each profile needs and cluster them into a taxonomy. Proficiency levels are optional." },
  { id: "tasks", title: "Task taxonomy", description: "Infer what each profile spends time on, cluster it, and analyse where the workforce's time goes." },
  { id: "matching", title: "3rd-party taxonomy match", description: "Place each job profile in the external market taxonomy and assign a career level." },
] as const;

// Coarsest first — the order the taxonomy browser renders its headings in, which
// is the opposite of the order the tiers are confirmed in.
const SKILL_LABELS = {
  tiers: ["Skill families", "Skill categories", "Skill clusters"] as [string, string, string],
};

const TASK_LABELS = {
  tiers: ["Task domains", "Task categories", "Task clusters"] as [string, string, string],
};

/** Nine statuses: three hierarchies x three tiers. */
type EntityTiers = Record<ClusterEntity, Partial<Record<TierName, TierStatus>>>;
const EMPTY_TIERS: EntityTiers = { job: {}, skill: {}, task: {} };

export function PipelinePage({ clientSlug, projectSlug }: { clientSlug: string; projectSlug: string }) {
  const api = useMemo(() => pipelineApi(clientSlug, projectSlug), [clientSlug, projectSlug]);
  const [summary, setSummary] = useState<StageSummary | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  // Toggling the accordion changes the document height, which used to leave the browser
  // clamping scrollTop to the page bottom. See useSectionScroll.
  const { hold, reveal } = useSectionScroll(expanded);
  const [error, setError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<ProfileRow[]>([]);
  // Which profiles the next evaluation run covers. Empty means all of them, so the
  // default behaviour is unchanged and selecting nothing is not a way to run nothing.
  const [jeSelection, setJeSelection] = useState<Set<string>>(new Set());
  const [jePicker, setJePicker] = useState(false);
  const [caps, setCaps] = useState({ html: true, docx: true, pdf: false });
  const [busy, setBusy] = useState(false);
  const [skills, setSkills] = useState<SkillsSummary | null>(null);
  const [tasks, setTasks] = useState<TasksSummary | null>(null);
  const [matching, setMatching] = useState<MatchingSummary | null>(null);
  const [allIndustries, setAllIndustries] = useState<string[]>([]);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [hrisPreview, setHrisPreview] = useState<HrisPreview | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Which wizard stage the current/last job belongs to, so its progress and
  // summary render only there. Without this every stage showed the same job —
  // step 3 displaying step 2's completed bar.
  const [jobOwner, setJobOwner] = useState<string | null>(null);
  // Distinguishes "the API is not up yet" from a real error, so a slow backend
  // start reads as waiting rather than as a broken app.
  const [backendDown, setBackendDown] = useState(false);
  const [embedModels, setEmbedModels] = useState<EmbeddingModelsInfo | null>(null);
  // Per-tier clustering status. Kept alongside the stage summary because the three
  // hierarchy steps gate on each other rather than on one "clustered" flag.
  // Per entity, per tier. Three hierarchies now use the same per-tier flow, so
  // the wizard needs all nine statuses rather than just the job hierarchy's three.
  const [tiers, setTiers] = useState<EntityTiers>(EMPTY_TIERS);
  // Whether the later studios can be entered, and what is missing if not. Comes from
  // the workforce side rather than being re-derived here, so one definition of "the
  // architecture is complete" serves the toggle, the button and the studio itself.
  // Every gated studio's state, from one /workforce/status call. Derived by studioGates so
  // this page and WorkforcePage cannot drift on what "unlocked" means.
  const [gates, setGates] = useState<Partial<Record<Studio, StudioGate>>>({});
  const wfGate = gates.workforce ?? { ready: false, missing: [] };
  // Per-run embedding choices. null model means "use the server default".
  const [jobModel, setJobModel] = useState<string | null>(null);
  const [forceCpu, setForceCpu] = useState(false);
  // Fan-out width for the per-item LLM stages. One control rather than per stage:
  // it is a property of the API account's rate limits, not of a stage.
  const [workers, setWorkers] = useState<number>(() => {
    const saved = Number(localStorage.getItem("jastudio-workers"));
    return saved >= 1 && saved <= 64 ? saved : 8;
  });
  const setWorkersPersisted = useCallback((n: number) => {
    const clamped = Math.max(1, Math.min(64, n));
    setWorkers(clamped);
    localStorage.setItem("jastudio-workers", String(clamped));
  }, []);

  const refresh = useCallback(async () => {
    try {
      const s = await api.summary();
      setSummary(s);
      if (s.job_profiles > 0) {
        const p = await api.listProfiles();
        setProfiles(p.profiles);
        // Downstream summaries only mean anything once profiles exist, and they
        // are independent — fetch together rather than serially behind each stage.
        const [sk, tk, mt, ov] = await Promise.allSettled([
          api.skills.summary(),
          api.tasks.summary(),
          api.matching.summary(),
          api.overview(),
        ]);
        if (sk.status === "fulfilled") setSkills(sk.value);
        if (tk.status === "fulfilled") setTasks(tk.value);
        if (mt.status === "fulfilled") setMatching(mt.value);
        if (ov.status === "fulfilled") setOverview(ov.value);
      }
      // Tier status drives steps 5-7 and the two taxonomy steps; a tier only
      // becomes runnable once the one below it is confirmed, so this has to
      // refresh after every job.
      void api
        .allTierStatus()
        .then((t) => setTiers({ job: t.job ?? {}, skill: t.skill ?? {}, task: t.task ?? {} }))
        .catch(() => {});
      // Keeps the "loaded" badges honest after a run put a model in memory.
      void api.embeddingModels().then(setEmbedModels).catch(() => {});
      void workforceApi(clientSlug, projectSlug)
        .status()
        .then((w) => setGates(studioGates(w)))
        .catch(() => {});
      setBackendDown(false);
      return s;
    } catch (e) {
      // The backend takes a few seconds longer to start than Vite does, so the
      // first loads of a fresh session can land before it is listening. That is
      // a wait, not a failure — show it as one and let the retry loop clear it,
      // rather than pinning a raw connection error the user has to reload past.
      if (e instanceof TypeError || (e as { status?: number }).status === 502) {
        setBackendDown(true);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
      return null;
    }
  }, [api]);

  const { state: job, attach, reset } = useJobStream(() => void refresh());

  useEffect(() => {
    void refresh();
    void api.exportCapabilities().then(setCaps).catch(() => {});
    // The industry list is static catalogue metadata; a 503 here just means the
    // 3rd-party taxonomy isn't present, which the matching stage handles.
    void taxonomyApi.industries().then((r) => setAllIndustries(r.industries)).catch(() => {});
    void api.embeddingModels().then(setEmbedModels).catch(() => {});
  }, [refresh, api]);

  // Poll until the backend answers. Only while it is down, so a healthy session
  // does no extra work.
  useEffect(() => {
    if (!backendDown) return;
    const t = setInterval(() => void refresh(), 2000);
    return () => clearInterval(t);
  }, [backendDown, refresh]);

  // Re-attach to a job already running server-side (e.g. after a page reload) —
  // the backend keeps the job alive and replays its history.
  useEffect(() => {
    if (summary?.active_job_id && !job.running && job.jobId !== summary.active_job_id) {
      attach(summary.active_job_id, summary.active_job_stage ?? "");
      // Backend stage labels match the wizard step ids, so a job picked up after
      // a reload lands under the step that owns it rather than nowhere.
      if (summary.active_job_stage) setJobOwner(summary.active_job_stage);
    }
  }, [summary?.active_job_id, summary?.active_job_stage, job.running, job.jobId, attach]);

  // Declared before the effects that read it — a `const` referenced above its
  // own declaration is a temporal dead zone error at runtime, which the type
  // checker does not catch.
  const downstream = useMemo<Downstream>(
    () => ({ tiers, skills, tasks, matching }),
    [tiers, skills, tasks, matching],
  );

  // Open the first stage that still needs attention.
  useEffect(() => {
    if (!summary || expanded !== null) return;
    setExpanded(firstIncompleteStage(summary, downstream));
  }, [summary, expanded, downstream]);

  const state = useCallback(
    (id: string): StageState => (summary ? stageState(id, summary, downstream) : "locked"),
    [summary, downstream],
  );

  async function runJob(
    start: () => Promise<{ job_id: string; stage: string }>,
    ownerStage?: string,
  ) {
    setError(null);
    reset();
    setJobOwner(ownerStage ?? expanded);
    setBusy(true);
    try {
      const handle = await start();
      attach(handle.job_id, handle.stage);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    setBusy(true);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!summary) {
    return (
      <div className="mx-auto max-w-2xl px-6 py-16 text-center">
        <p className="text-[13px] font-semibold text-text">
          {backendDown ? "Waiting for the backend…" : "Loading project…"}
        </p>
        {backendDown && (
          <p className="mx-auto mt-2 max-w-md text-[12px] leading-snug text-text-secondary">
            The API is not answering on port 9400 yet. It takes a few seconds
            longer to start than this page does, so this usually clears on its
            own. Retrying every 2 seconds.
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-6xl gap-8 px-6 py-8">
      {/* sticky step indicator */}
      <nav className="sticky top-24 hidden h-fit w-52 shrink-0 lg:block">
        <div className="mb-3 space-y-1.5">
          <StudioToggle gates={gates} />
          {/* Renders only on a seeded demo project — absent everywhere else. */}
          <DemoReset />
        </div>
        <p className="mb-3 text-[10px] font-extrabold uppercase tracking-wider text-text-muted">Steps</p>
        <ol className="space-y-1">
          {STAGES.map((s, i) => {
            const st = state(s.id);
            return (
              <li key={s.id}>
                <a
                  href={`#${s.id}`}
                  onClick={(e) => {
                    // The default hash jump uses the pre-render layout, so it lands
                    // somewhere that stops existing the moment the accordion moves.
                    e.preventDefault();
                    if (st !== "locked") setExpanded(s.id);
                    reveal(s.id);
                  }}
                  className={cn(
                    "flex items-center gap-2 rounded-[10px] px-2.5 py-1.5 text-[11.5px] font-semibold transition-colors",
                    st === "locked" ? "text-text-muted opacity-60" : "text-text hover:bg-panel",
                    expanded === s.id && "bg-accent-bg text-accent",
                  )}
                >
                  <span
                    className={cn(
                      "h-2 w-2 shrink-0 rounded-full",
                      st === "complete" && "bg-success",
                      st === "active" && "bg-accent",
                      st === "locked" && "bg-border",
                    )}
                  />
                  <span className="truncate">
                    {i + 1}. {s.title}
                  </span>
                </a>
              </li>
            );
          })}
        </ol>
        <div className="mt-4 border-t border-border px-2.5 pt-3">
          <label className="flex items-baseline justify-between text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
            Parallel requests
            <span className="text-[13px] font-bold tabular-nums text-accent">{workers}</span>
          </label>
          <input
            type="range"
            min={1}
            max={32}
            value={workers}
            onChange={(e) => setWorkersPersisted(Number(e.target.value))}
            className="mt-1 w-full accent-[var(--color-accent)]"
          />
          <p className="mt-1 text-[10.5px] leading-snug text-text-muted">
            How many LLM calls run at once in the per-item stages. Each call takes
            roughly the same time regardless, so this sets how many run
            concurrently. Raising it helps until the API account's rate limit is
            reached, after which requests are retried and throughput stops
            improving.
          </p>
        </div>

        {overview && overview.families.length > 0 && (
          <a
            href="#architecture"
            className="mt-3 flex items-center gap-2 rounded-[10px] border-t border-border px-2.5 pt-3 text-[11.5px] font-bold text-brand hover:underline"
          >
            Job architecture
          </a>
        )}
      </nav>

      <div className="min-w-0 flex-1 space-y-4">
        {error && (
          <div className="rounded-[10px] border border-brand-border bg-brand-bg px-4 py-3 text-[12.5px] text-text">
            {error}
          </div>
        )}

        {backendDown && (
          <div className="rounded-[10px] border border-warning-border bg-warning-bg px-4 py-3 text-[12.5px] text-text">
            Lost contact with the backend on port 9400. Retrying every 2 seconds —
            anything already on screen may be out of date.
          </div>
        )}

        {notice && (
          <div className="flex items-start justify-between gap-3 rounded-[10px] border border-success-border bg-success-bg px-4 py-3 text-[12.5px] text-text">
            <span>{notice}</span>
            <button
              onClick={() => setNotice(null)}
              className="shrink-0 text-[11px] font-bold text-text-muted hover:text-text"
            >
              Dismiss
            </button>
          </div>
        )}

        {STAGES.map((s, i) => {
          const st = state(s.id);
          return (
            <StageSection
              key={s.id}
              index={i + 1}
              id={s.id}
              title={s.title}
              description={s.description}
              state={st}
              summary={stageSummaryLine(s.id, summary, downstream)}
              lockedReason={lockedReason(s.id)}
              config={
                s.id === "profiles"
                  ? profileConfig()
                  : s.id === "evaluation"
                    ? evaluationConfig()
                    : undefined
              }
              expanded={expanded === s.id}
              onToggle={() => {
                hold(s.id);
                setExpanded(expanded === s.id ? null : s.id);
              }}
            >
              {renderStage(s.id)}
            </StageSection>
          );
        })}

        {/* The deliverable, not a step — styled as a structural container rather
            than another numbered wizard card so it doesn't read as more work. */}
        {overview && overview.families.length > 0 && (
          <section
            id="architecture"
            className="rounded-[var(--radius-modal)] border border-border bg-card p-6 shadow-modal"
          >
            <div className="mb-4 border-b-2 border-brand pb-3">
              <h2 className="text-[16px] font-bold text-text">Job architecture</h2>
              <p className="mt-0.5 text-[12.5px] text-text-secondary">
                The complete hierarchy: every job family, category and profile with its
                evaluation, skills, time allocation and external taxonomy match.
              </p>
            </div>
            <OverviewBrowser
              data={overview}
              onOpenProfile={(key) => window.open(api.exportUrl(key, "html"), "_blank")}
            />
            <div className="mt-4">
              <ExportBar
                manifest={api.exportManifest}
                workbookUrl={api.workbookUrl()}
                csvUrl={api.datasetCsvUrl}
              />
            </div>
          </section>
        )}

        {/* The instructions put this at the foot of the screen: the architecture is
            finished, so the next thing to do is take it into Work Architecture Studio. */}
        <ProceedToWorkforce ready={wfGate.ready} missing={wfGate.missing} />
      </div>
    </div>
  );

  /** Step 8's settings — the profile template, its boilerplate and the evaluation
   *  framework. Rendered through StageSection's config slot so they are reachable
   *  before the hierarchy is finished, since they are inputs to it rather than
   *  results of it. */
  function profileConfig() {
    return (
      <div className="space-y-3">
            {/* Step 7 opens with "User defines job profile template, Job
                Evaluation Framework and level names / JE score mapping", so both
                are editable here — before the run that consumes them. */}
            <Collapsible
              title="Document boilerplate"
              subtitle="Company description, equality statement and accent colour applied to every profile."
            >
              <LazyBoilerplate
                load={api.getBoilerplate}
                save={api.putBoilerplate}
                onSaved={() => void refresh()}
              />
            </Collapsible>

            <Collapsible
              title="Job profile template"
              subtitle="Which sections each profile has, their headings and order, and the guidance used to write them."
            >
              <LazyProfileTemplate
                load={() => api.getProfileTemplate()}
                loadDefaults={() => api.getProfileTemplate(true)}
                save={api.putProfileTemplate}
                profileCount={summary!.job_profiles}
                onSaved={(n) => {
                  if (n > 0) setNotice(`Template saved. ${n} existing profile${n === 1 ? "" : "s"} marked stale — regenerate to apply it.`);
                  void refresh();
                }}
              />
            </Collapsible>

      </div>
    );
  }

  /** The evaluation step's settings. The framework belongs here rather than with
   *  the documents: editing it invalidates evaluations, not profiles. */
  function evaluationConfig() {
    return (
      <Collapsible
        title="Job evaluation framework"
        subtitle="Domains, sub-factor weights, scoring rubric, and the level names each score maps to."
      >
        <LazyJEFramework
          load={() => api.getJeFramework()}
          loadDefaults={() => api.getJeFramework(true)}
          save={api.putJeFramework}
          suggestLevels={api.suggestLevelTitles}
          hasResults={summary!.je_results > 0}
        />
      </Collapsible>
    );
  }

  function renderStage(id: string) {
    // Only the stage that started the job shows its progress.
    const showProgress = jobOwner === id && (job.stage !== null || job.error);

    switch (id) {
      case "ingest":
        // Two independent input routes per instructions.txt ("AND/OR"), so both
        // are offered side by side and either can be used, or both.
        if (hrisPreview) {
          return (
            <HrisMappingPanel
              preview={hrisPreview}
              busy={busy}
              onCancel={() => setHrisPreview(null)}
              onConfirm={(mapping, limit) =>
                void act(async () => {
                  const res = await api.confirmHris({
                    file_id: hrisPreview.file_id,
                    job_title_col: mapping.job_title_col!,
                    job_description_col: mapping.job_description_col,
                    job_level_col: mapping.job_level_col,
                    headcount_col: mapping.headcount_col,
                    business_level_1_col: mapping.business_level_1_col,
                    business_level_2_col: mapping.business_level_2_col,
                    business_level_3_col: mapping.business_level_3_col,
                    limit,
                  });
                  setHrisPreview(null);
                  setNotice(
                    `Imported ${res.records_added.toLocaleString()} of ${res.rows_in_sheet.toLocaleString()} rows` +
                      (res.skipped_no_title ? `, skipped ${res.skipped_no_title} with no title` : "") +
                      (res.limited ? " (limited)" : "") +
                      ".",
                  );
                })
              }
            />
          );
        }
        return (
          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <Dropzone
                label="Job description files"
                hint="PDF, DOC, DOCX, TXT or HTML — one role per file"
                accept=".pdf,.doc,.docx,.txt,.html,.htm"
                multiple
                onFiles={(files) => void act(() => api.uploadFiles(files))}
              />
              <Dropzone
                label="HRIS or job list spreadsheet"
                hint="XLSX, XLS or CSV — many roles in one sheet"
                accept=".xlsx,.xls,.csv"
                onFiles={(files) =>
                  void act(async () => setHrisPreview(await api.uploadHris(files[0])))
                }
              />
            </div>
            <p className="text-[12.5px] text-text-secondary">
              {summary!.raw_records} job{summary!.raw_records === 1 ? "" : "s"} loaded.
              {summary!.raw_records > 0 && " Add more from either source, or continue."}
            </p>
          </div>
        );

      case "strip":
        return (
          <div className="space-y-3">
            <Button variant="primary" onClick={() => runJob(() => api.startStrip(workers))} disabled={busy || job.running}>
              <span className="flex items-center gap-1.5">
                <Play size={12} /> Strip {summary!.raw_records} job descriptions
              </span>
            </Button>
            {showProgress && <ProgressBar job={job} />}
          </div>
        );

      case "dedupe":
        return (
          <div className="space-y-4">
            {summary!.stripped_records > 0 && (
              <div className="space-y-3">
            {embedModels && (
              <EmbeddingOptions
                info={embedModels}
                entity="job"
                model={jobModel}
                onModel={setJobModel}
                forceCpu={forceCpu}
                onForceCpu={setForceCpu}
                hasExistingTree={summary!.dedupe_groups > 0}
                disabled={busy || job.running}
              />
            )}
                <Button
                  variant="primary"
                  onClick={() =>
                    runJob(() =>
                      api.startDedupeBuild({
                        embedding_model: jobModel,
                        device: forceCpu ? "cpu" : null,
                      }),
                    )
                  }
                  disabled={busy || job.running}
                >
                  <span className="flex items-center gap-1.5">
                    <Play size={12} />
                    {summary!.dedupe_groups > 0 ? "Re-embed" : "Embed and find candidate duplicates"}
                    {jobModel ? ` with ${jobModel}` : ""}
                  </span>
                </Button>
                {showProgress && <ProgressBar job={job} />}
              </div>
            )}
            <DedupePanel
              preview={api.dedupePreview}
              onConfirm={(t) => void act(() => api.confirmDedupe(t))}
              confirming={busy}
              initialThreshold={summary!.dedupe_threshold ?? 0.9}
              ready={summary!.dedupe_embeddings_ready}
            />
          </div>
        );

      case "normalize":
        return (
          <div className="space-y-3">
            <Button variant="primary" onClick={() => runJob(() => api.startNormalize(workers))} disabled={busy || job.running}>
              <span className="flex items-center gap-1.5">
                <Play size={12} /> Normalise {summary!.dedupe_groups} distinct jobs
              </span>
            </Button>
            {showProgress && <ProgressBar job={job} />}
          </div>
        );

      case "cluster":
      case "categories":
      case "families": {
        const tierOf = { cluster: "profile", categories: "category", families: "family" } as const;
        const tier = tierOf[id as keyof typeof tierOf];
        const st = tiers.job[tier];
        if (!st) {
          return <p className="text-[12.5px] text-text-muted">Loading tier status…</p>;
        }
        const api_t = api.tier("job", tier);
        return (
          <div className="space-y-4">
            {/* The profile tier embeds the normalised jobs, so the model choice
                belongs here. The coarser tiers cluster centroids from the tier
                below and never embed, so there is nothing to choose. */}
            {tier === "profile" && !st.built && embedModels && (
              <EmbeddingOptions
                info={embedModels}
                entity="job"
                model={jobModel}
                onModel={setJobModel}
                forceCpu={forceCpu}
                onForceCpu={setForceCpu}
                hasExistingTree={st.confirmed}
                disabled={busy || job.running}
              />
            )}
            <TierClusterStage
              status={st}
              preview={api_t.preview}
              gatePreview={api_t.gate}
              // One call: the endpoint embeds when the tier needs it. Chaining two
              // jobs here could not work — the registry allows one per project, so
              // the second always 409'd while the first ran on unattached.
              onBuild={() =>
                api_t.build(
                  tier === "profile"
                    ? { embedding_model: jobModel, device: forceCpu ? "cpu" : null }
                    : undefined,
                )
              }
              onAnalyse={api_t.analyse}
              onConfirm={(k, gate) => api_t.confirm(k, gate, workers)}
              loadClusters={api_t.clusters}
              loadClusterMembers={api_t.clusterMembers}
              onRename={api_t.rename}
              onReassign={api_t.reassign}
              runJob={runJob}
              lineagePreview={api.lineagePreview}
              lineageStep={`job:${tier}`}
              busy={busy || job.running}
              progress={showProgress ? <ProgressBar job={job} /> : null}
              activity={showProgress ? <JobPulse job={job} /> : null}
            />
          </div>
        );
      }

      case "profiles":
        return (
          <div className="space-y-4">
            <div className="space-y-3">
              <Button
                variant="primary"
                onClick={() => runJob(() => api.startProfileGeneration(workers))}
                disabled={busy || job.running}
              >
                <span className="flex items-center gap-1.5">
                  <Play size={12} />
                  {summary!.job_profiles > 0
                    ? "Regenerate " + summary!.job_profiles + " job profile documents"
                    : "Generate job profile documents"}
                </span>
              </Button>
              <p className="text-[11.5px] leading-snug text-text-secondary">
                One document per confirmed job profile, written from the template above.
                Evaluated levels are added by the step that follows.
              </p>
              {showProgress && <ProgressBar job={job} />}
            </div>

            {profiles.length > 0 && (
              <JEResultsBrowser
                profiles={profiles}
                onOpenProfile={(key) => window.open(api.exportUrl(key, "html"), "_blank")}
                loadJe={api.getProfileJe}
                exportUrl={api.exportUrl}
                pdfAvailable={caps.pdf}
              />
            )}
          </div>
        );

      case "evaluation":
        return (
          <div className="space-y-4">
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="primary"
                  onClick={() =>
                    runJob(() =>
                      api.startJobEvaluation(
                        workers,
                        jeSelection.size > 0 ? [...jeSelection] : undefined,
                      ),
                    )
                  }
                  disabled={busy || job.running || summary!.job_profiles === 0}
                >
                  <span className="flex items-center gap-1.5">
                    <Play size={12} />
                    {jeSelection.size > 0
                      ? `Evaluate ${jeSelection.size} selected`
                      : summary!.je_results > 0
                        ? "Re-evaluate all " + summary!.job_profiles + " profiles"
                        : "Evaluate all " + summary!.job_profiles + " profiles"}
                  </span>
                </Button>
                <Button
                  onClick={() => setJePicker((v) => !v)}
                  disabled={profiles.length === 0}
                >
                  {jePicker ? "Hide selection" : "Choose profiles"}
                </Button>
                {jeSelection.size > 0 && (
                  <button
                    onClick={() => setJeSelection(new Set())}
                    className="text-[11.5px] font-semibold text-accent hover:underline"
                  >
                    Clear {jeSelection.size}
                  </button>
                )}
              </div>
              <p className="text-[11.5px] leading-snug text-text-secondary">
                Three scoring perspectives per profile, aggregated to one level and one
                spread. Re-running against an edited framework leaves the documents alone.
                {jeSelection.size > 0
                  ? " A narrowed run keeps every other profile's existing evaluation."
                  : ""}
              </p>
              {jePicker && profiles.length > 0 && (
                <ProfileSelect
                  profiles={profiles}
                  selected={jeSelection}
                  onChange={setJeSelection}
                />
              )}
              {showProgress && <ProgressBar job={job} />}
            </div>

            {profiles.length > 0 && (
              <JEResultsBrowser
                profiles={profiles}
                onOpenProfile={(key) => window.open(api.exportUrl(key, "html"), "_blank")}
                loadJe={api.getProfileJe}
                exportUrl={api.exportUrl}
                pdfAvailable={caps.pdf}
              />
            )}
          </div>
        );

      case "skills":
        return (
          <EntityTaxonomyStage
            kind="skill"
            lineagePreview={api.lineagePreview}
            tierLabels={SKILL_LABELS.tiers}
            inferredCount={skills?.inferred_skills ?? 0}
            profilesCovered={skills?.profiles_covered ?? 0}
            jobProfileCount={summary!.job_profiles}
            named={skills?.named ?? false}
            tiers={tiers.skill}
            audit={skills?.audit ?? {}}
            onInfer={() => api.skills.infer(undefined, workers)}
            tierApi={(t) => api.tier("skill", t)}
            loadTaxonomy={async () => {
              const t = await api.skills.taxonomy();
              return { roots: t.families, hasHeadcount: t.has_headcount };
            }}
            runJob={runJob}
            busy={busy || job.running}
            progress={showProgress ? <ProgressBar job={job} /> : null}
            activity={showProgress ? <JobPulse job={job} /> : null}
            proficiency={{
              done: (skills?.proficiency_definitions ?? 0) > 0,
              mappedClusters: skills?.proficiency_definitions ?? 0,
              levelsAssigned: skills?.levels_assigned ?? 0,
              requirements: skills?.profile_requirements ?? 0,
              onGenerate: () => api.skills.generateProficiency(workers),
              editor: (
                <Collapsible
                  title="Proficiency template"
                  subtitle="The level scale and criteria that per-cluster wording is generated against."
                >
                  <LazyProficiencyTemplate
                    load={() => api.skills.getTemplate()}
                    loadDefaults={() => api.skills.getTemplate(true)}
                    save={api.skills.putTemplate}
                    hasGenerated={(skills?.proficiency_definitions ?? 0) > 0}
                  />
                </Collapsible>
              ),
            }}
          />
        );

      case "tasks":
        return (
          <EntityTaxonomyStage
            kind="task"
            lineagePreview={api.lineagePreview}
            tierLabels={TASK_LABELS.tiers}
            inferredCount={tasks?.inferred_tasks ?? 0}
            profilesCovered={tasks?.profiles_covered ?? 0}
            jobProfileCount={summary!.job_profiles}
            named={tasks?.named ?? false}
            tiers={tiers.task}
            audit={tasks?.audit ?? {}}
            onInfer={() => api.tasks.infer(undefined, workers)}
            tierApi={(t) => api.tier("task", t)}
            loadTaxonomy={async () => {
              const t = await api.tasks.taxonomy();
              return { roots: t.domains, hasHeadcount: t.has_headcount };
            }}
            runJob={runJob}
            busy={busy || job.running}
            progress={showProgress ? <ProgressBar job={job} /> : null}
            activity={showProgress ? <JobPulse job={job} /> : null}
          />
        );

      case "matching":
        return (
          <div className="space-y-3">
            <MatchingPanel
              browse={api.matching.browse}
              matches={api.matching.matches}
              search={api.matching.search}
              override={api.matching.override}
              industries={matching?.industries ?? []}
              allIndustries={allIndustries}
              onRun={(inds) =>
                runJob(() => api.matching.run({ industries: inds.length ? inds : null }, workers))
              }
              running={busy || job.running}
              hasResults={(matching?.matched_profiles ?? 0) > 0}
            />
            {showProgress && <ProgressBar job={job} />}
          </div>
        );

      default:
        return null;
    }
  }
}

interface Downstream {
  tiers: EntityTiers;
  skills: SkillsSummary | null;
  tasks: TasksSummary | null;
  matching: MatchingSummary | null;
}

/**
 * The two config editors fetch on first open rather than with the page.
 * Both are behind a Collapsible, so most sessions never need the request.
 */
function LazyBoilerplate({
  load,
  save,
  onSaved,
}: {
  load: () => Promise<Boilerplate>;
  save: (v: Boilerplate) => Promise<{ profiles_rerendered: number }>;
  onSaved: () => void;
}) {
  const [value, setValue] = useState<Boilerplate | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    load()
      .then(setValue)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error) return <p className="text-[12px] text-brand">{error}</p>;
  if (!value) return <p className="text-[12px] text-text-muted">Loading…</p>;
  return (
    <BoilerplateEditor
      value={value}
      saving={saving}
      onSave={async (v) => {
        setSaving(true);
        try {
          const res = await save(v);
          setValue(v);
          onSaved();
          return res;
        } finally {
          setSaving(false);
        }
      }}
    />
  );
}

function LazyProfileTemplate({
  load,
  loadDefaults,
  save,
  profileCount,
  onSaved,
}: {
  load: () => Promise<ProfileTemplate>;
  loadDefaults: () => Promise<ProfileTemplate>;
  save: (s: ProfileSection[]) => Promise<{ profiles_marked_stale: number }>;
  profileCount: number;
  onSaved: (staleCount: number) => void;
}) {
  const [template, setTemplate] = useState<ProfileTemplate | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    load()
      .then(setTemplate)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error) return <p className="text-[12px] text-brand">{error}</p>;
  if (!template) return <p className="text-[12px] text-text-muted">Loading template…</p>;
  return (
    <ProfileTemplateEditor
      template={template}
      saving={saving}
      profileCount={profileCount}
      onReset={loadDefaults}
      onSave={async (sections) => {
        setSaving(true);
        try {
          const res = await save(sections);
          setTemplate({ ...template, sections });
          onSaved(res.profiles_marked_stale);
          return res;
        } finally {
          setSaving(false);
        }
      }}
    />
  );
}

function LazyJEFramework({
  load,
  loadDefaults,
  save,
  suggestLevels,
  hasResults,
}: {
  load: () => Promise<JEFramework>;
  loadDefaults: () => Promise<JEFramework>;
  save: (f: JEFramework) => Promise<unknown>;
  suggestLevels: (f: JEFramework) => Promise<LevelSuggestion>;
  hasResults: boolean;
}) {
  const [framework, setFramework] = useState<JEFramework | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    load()
      .then(setFramework)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    // `load` is a fresh closure each render; depending on it would refetch forever.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error) return <p className="text-[12px] text-brand">{error}</p>;
  if (!framework) return <p className="text-[12px] text-text-muted">Loading framework…</p>;
  return (
    <JEFrameworkEditor
      framework={framework}
      saving={saving}
      hasResults={hasResults}
      onReset={loadDefaults}
      onSuggestLevels={suggestLevels}
      onSave={async (f) => {
        setSaving(true);
        try {
          await save(f);
          setFramework(f);
        } finally {
          setSaving(false);
        }
      }}
    />
  );
}

function LazyProficiencyTemplate({
  load,
  loadDefaults,
  save,
  hasGenerated,
}: {
  load: () => Promise<ProficiencyTemplate>;
  loadDefaults: () => Promise<ProficiencyTemplate>;
  save: (t: ProficiencyTemplate) => Promise<unknown>;
  hasGenerated: boolean;
}) {
  const [template, setTemplate] = useState<ProficiencyTemplate | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    load()
      .then(setTemplate)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error) return <p className="text-[12px] text-brand">{error}</p>;
  if (!template) return <p className="text-[12px] text-text-muted">Loading template…</p>;
  return (
    <ProficiencyTemplateEditor
      template={template}
      saving={saving}
      hasGenerated={hasGenerated}
      onReset={loadDefaults}
      onSave={async (t) => {
        setSaving(true);
        try {
          await save(t);
          setTemplate(t);
        } finally {
          setSaving(false);
        }
      }}
    />
  );
}

function stageState(id: string, s: StageSummary, d: Downstream): StageState {
  switch (id) {
    // Steps 8-11 all hang off having job profiles; each is complete once its own
    // final artifact exists (a named taxonomy, or at least one recorded match).
    case "skills":
      if (s.job_profiles === 0) return "locked";
      return d.skills?.named ? "complete" : "active";
    case "tasks":
      if (s.job_profiles === 0) return "locked";
      return d.tasks?.named ? "complete" : "active";
    case "matching":
      if (s.job_profiles === 0) return "locked";
      return (d.matching?.matched_profiles ?? 0) > 0 ? "complete" : "active";
  }
  switch (id) {
    case "ingest":
      return s.raw_records > 0 ? "complete" : "active";
    case "strip":
      if (s.raw_records === 0) return "locked";
      return s.stripped_records > 0 ? "complete" : "active";
    case "dedupe":
      if (s.stripped_records === 0) return "locked";
      return s.dedupe_groups > 0 ? "complete" : "active";
    case "normalize":
      if (s.dedupe_groups === 0) return "locked";
      return s.normalized_profiles > 0 ? "complete" : "active";
    case "cluster":
    case "categories":
    case "families": {
      const tier = { cluster: "profile", categories: "category", families: "family" }[id] as
        | "profile" | "category" | "family";
      const st = d.tiers.job[tier];
      if (!st?.ready_to_run) return "locked";
      return st.confirmed ? "complete" : "active";
    }
    case "profiles":
      // Needs the full hierarchy: a profile document carries its category and
      // family in the breadcrumb.
      if (!d.tiers.job.family?.confirmed) return "locked";
      return s.job_profiles > 0 ? "complete" : "active";
    case "evaluation":
      if (s.job_profiles === 0) return "locked";
      return s.je_results > 0 ? "complete" : "active";
    default:
      return "locked";
  }
}

function stageSummaryLine(id: string, s: StageSummary, d: Downstream): string | undefined {
  switch (id) {
    case "skills":
      if (!d.skills?.named) return undefined;
      return `${d.skills.inferred_skills} skills › ${d.skills.k_clusters} clusters${
        d.skills.levels_assigned ? ", proficiency mapped" : ""
      }`;
    case "tasks":
      if (!d.tasks?.named) return undefined;
      return `${d.tasks.inferred_tasks} tasks › ${d.tasks.k_tasks} clusters`;
    case "matching": {
      const m = d.matching;
      if (!m || m.matched_profiles === 0) return undefined;
      const review = m.summary.needs_review ?? 0;
      return `${m.summary.matched ?? 0} of ${m.matched_profiles} matched${
        review ? `, ${review} to review` : ""
      }`;
    }
  }
  switch (id) {
    case "ingest":
      return `${s.raw_records} job${s.raw_records === 1 ? "" : "s"} loaded`;
    case "strip":
      return `${s.stripped_records} stripped`;
    case "dedupe":
      return `${s.dedupe_groups} distinct jobs at threshold ${s.dedupe_threshold?.toFixed(2)}`;
    case "normalize":
      return `${s.normalized_profiles} normalised`;
    case "cluster":
    case "categories":
    case "families": {
      const tier = { cluster: "profile", categories: "category", families: "family" }[id] as
        | "profile" | "category" | "family";
      const st = d.tiers.job[tier];
      if (!st?.confirmed) return undefined;
      const noun = { profile: "profiles", category: "categories", family: "families" }[tier];
      return `${st.k} ${noun}${st.n_moved ? `, ${st.n_moved} moved by the model` : ""}`;
    }
    case "profiles":
      return `${s.job_profiles} profile document${s.job_profiles === 1 ? "" : "s"}`;
    case "evaluation":
      if (s.je_results === 0) return undefined;
      return `${s.je_results} of ${s.job_profiles} evaluated`;
    default:
      return undefined;
  }
}

function lockedReason(id: string): string {
  switch (id) {
    case "strip":
      return "Upload job descriptions first.";
    case "dedupe":
      return "Run the strip stage first.";
    case "normalize":
      return "Confirm deduplication first.";
    case "cluster":
      return "Normalise at least 3 jobs first.";
    case "categories":
      return "Confirm the job profiles first.";
    case "families":
      return "Confirm the job categories first.";
    case "profiles":
      return "Confirm all three hierarchy levels first.";
    case "evaluation":
      return "Generate the job profile documents first.";
    case "skills":
    case "tasks":
    case "matching":
      return "Generate job profiles first.";
    default:
      return "";
  }
}

function firstIncompleteStage(s: StageSummary, d: Downstream): string {
  for (const stage of STAGES) {
    if (stageState(stage.id, s, d) === "active") return stage.id;
  }
  return STAGES[STAGES.length - 1].id;
}
