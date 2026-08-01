import { useCallback, useEffect, useMemo, useState } from "react";
import { Play } from "lucide-react";
import { pipelineApi, taxonomyApi } from "../services/pipelineApi";
import { useJobStream } from "../hooks/useJobStream";
import type {
  HrisPreview,
  JEFramework,
  ProficiencyTemplate,
  Boilerplate,
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
import { ClusterKPanel } from "../components/pipeline/ClusterKPanel";
import { DedupePanel } from "../components/pipeline/DedupePanel";
import { JEResultsBrowser } from "../components/pipeline/JEResultsBrowser";
import { EntityTaxonomyStage } from "../components/pipeline/EntityTaxonomyStage";
import { MatchingPanel } from "../components/pipeline/MatchingPanel";
import { OverviewBrowser } from "../components/pipeline/OverviewBrowser";
import { ExportBar } from "../components/pipeline/ExportBar";
import { HrisMappingPanel } from "../components/pipeline/HrisMappingPanel";
import { JEFrameworkEditor } from "../components/pipeline/JEFrameworkEditor";
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
  { id: "cluster", title: "Cluster and name", description: "Build the Family › Category › Profile hierarchy and give each cluster an industry-standard name." },
  { id: "profiles", title: "Job profiles and evaluation", description: "Generate a job profile document per cluster and evaluate it against your job evaluation framework." },
  { id: "skills", title: "Skills taxonomy", description: "Infer the attributes each profile needs, cluster them into a taxonomy, and set proficiency levels." },
  { id: "tasks", title: "Task taxonomy", description: "Infer what each profile spends time on, cluster it, and analyse where the workforce's time goes." },
  { id: "matching", title: "3rd-party taxonomy match", description: "Place each job profile in the external market taxonomy and assign a career level." },
] as const;

const SKILL_LABELS = {
  tiers: ["Skill families", "Skill categories", "Skill clusters"] as [string, string, string],
  hints: ["Broadest grouping", "Groups of clusters", "Groups of skills"] as [string, string, string],
  itemNoun: "inferred skills",
  leafNoun: "cluster",
};

const TASK_LABELS = {
  tiers: ["Task domains", "Task categories", "Task clusters"] as [string, string, string],
  hints: ["Broadest grouping", "Groups of clusters", "Groups of tasks"] as [string, string, string],
  itemNoun: "inferred tasks",
  leafNoun: "cluster",
};

export function PipelinePage({ clientSlug, projectSlug }: { clientSlug: string; projectSlug: string }) {
  const api = useMemo(() => pipelineApi(clientSlug, projectSlug), [clientSlug, projectSlug]);
  const [summary, setSummary] = useState<StageSummary | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<ProfileRow[]>([]);
  const [caps, setCaps] = useState({ html: true, docx: true, pdf: false });
  const [busy, setBusy] = useState(false);
  const [skills, setSkills] = useState<SkillsSummary | null>(null);
  const [tasks, setTasks] = useState<TasksSummary | null>(null);
  const [matching, setMatching] = useState<MatchingSummary | null>(null);
  const [allIndustries, setAllIndustries] = useState<string[]>([]);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [hrisPreview, setHrisPreview] = useState<HrisPreview | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
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
      return s;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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
  }, [refresh, api]);

  // Re-attach to a job already running server-side (e.g. after a page reload) —
  // the backend keeps the job alive and replays its history.
  useEffect(() => {
    if (summary?.active_job_id && !job.running && job.jobId !== summary.active_job_id) {
      attach(summary.active_job_id, summary.active_job_stage ?? "");
    }
  }, [summary?.active_job_id, summary?.active_job_stage, job.running, job.jobId, attach]);

  // Declared before the effects that read it — a `const` referenced above its
  // own declaration is a temporal dead zone error at runtime, which the type
  // checker does not catch.
  const downstream = useMemo<Downstream>(
    () => ({ skills, tasks, matching }),
    [skills, tasks, matching],
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

  async function runJob(start: () => Promise<{ job_id: string; stage: string }>) {
    setError(null);
    reset();
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
    return <p className="px-6 py-12 text-[13px] text-text-muted">Loading project…</p>;
  }

  return (
    <div className="mx-auto flex max-w-6xl gap-8 px-6 py-8">
      {/* sticky step indicator */}
      <nav className="sticky top-24 hidden h-fit w-52 shrink-0 lg:block">
        <p className="mb-3 text-[10px] font-extrabold uppercase tracking-wider text-text-muted">Process</p>
        <ol className="space-y-1">
          {STAGES.map((s, i) => {
            const st = state(s.id);
            return (
              <li key={s.id}>
                <a
                  href={`#${s.id}`}
                  onClick={() => st !== "locked" && setExpanded(s.id)}
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
              expanded={expanded === s.id}
              onToggle={() => setExpanded(expanded === s.id ? null : s.id)}
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
      </div>
    </div>
  );

  function renderStage(id: string) {
    const showProgress = job.stage !== null || job.error;

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
            {summary!.stripped_records > 0 && summary!.dedupe_groups === 0 && (
              <div className="space-y-3">
                <Button
                  variant="primary"
                  onClick={() => runJob(api.startDedupeBuild)}
                  disabled={busy || job.running}
                >
                  <span className="flex items-center gap-1.5">
                    <Play size={12} /> Embed and find candidate duplicates
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
        return (
          <div className="space-y-4">
            {!summary!.clustered && (
              <div className="space-y-3">
                <Button
                  variant="primary"
                  onClick={() => runJob(api.startClusterBuild)}
                  disabled={busy || job.running}
                >
                  <span className="flex items-center gap-1.5">
                    <Play size={12} /> Build the cluster tree
                  </span>
                </Button>
                {showProgress && <ProgressBar job={job} />}
              </div>
            )}
            <ClusterKPanel
              itemCount={summary!.normalized_profiles}
              initial={{
                families: summary!.k_families ?? Math.max(2, Math.min(8, Math.floor(summary!.normalized_profiles / 12) || 2)),
                categories: summary!.k_categories ?? Math.max(3, Math.min(24, Math.floor(summary!.normalized_profiles / 5) || 3)),
                profiles: summary!.k_profiles ?? Math.max(4, Math.min(64, Math.floor(summary!.normalized_profiles / 2) || 4)),
              }}
              preview={api.clusterPreview}
              onConfirm={(k, gate) =>
                runJob(() =>
                  api.confirmCluster({
                    k_families: k.families,
                    k_categories: k.categories,
                    k_profiles: k.profiles,
                    gate,
                  }),
                )
              }
              confirming={busy || job.running}
            />
            {showProgress && job.stage === "cluster" && <ProgressBar job={job} />}
          </div>
        );

      case "profiles":
        return (
          <div className="space-y-4">
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

            <Collapsible
              title="Job evaluation framework"
              subtitle="Domains, sub-factor weights, scoring rubric, and the level names each score maps to."
            >
              <LazyJEFramework
                load={() => api.getJeFramework()}
                loadDefaults={() => api.getJeFramework(true)}
                save={api.putJeFramework}
                hasResults={summary!.je_results > 0}
              />
            </Collapsible>

            <div className="space-y-3">
              <Button
                variant="primary"
                onClick={() => runJob(() => api.startProfileGeneration(true, workers))}
                disabled={busy || job.running}
              >
                <span className="flex items-center gap-1.5">
                  <Play size={12} /> Generate job profiles and evaluate
                </span>
              </Button>
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
            labels={SKILL_LABELS}
            tierLabels={SKILL_LABELS.tiers}
            inferredCount={skills?.inferred_skills ?? 0}
            profilesCovered={skills?.profiles_covered ?? 0}
            jobProfileCount={summary!.job_profiles}
            clustered={skills?.clustered ?? false}
            named={skills?.named ?? false}
            k={{
              families: skills?.k_families ?? null,
              categories: skills?.k_categories ?? null,
              clusters: skills?.k_clusters ?? null,
            }}
            audit={skills?.audit ?? {}}
            onInfer={() => api.skills.infer(undefined, workers)}
            onBuildTree={api.skills.buildTree}
            preview={api.skills.preview}
            onConfirm={api.skills.confirm}
            loadTaxonomy={async () => {
              const t = await api.skills.taxonomy();
              return { roots: t.families, hasHeadcount: t.has_headcount };
            }}
            runJob={runJob}
            busy={busy || job.running}
            progress={showProgress ? <ProgressBar job={job} /> : null}
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
            labels={TASK_LABELS}
            tierLabels={TASK_LABELS.tiers}
            inferredCount={tasks?.inferred_tasks ?? 0}
            profilesCovered={tasks?.profiles_covered ?? 0}
            jobProfileCount={summary!.job_profiles}
            clustered={tasks?.clustered ?? false}
            named={tasks?.named ?? false}
            k={{
              families: tasks?.k_domains ?? null,
              categories: tasks?.k_categories ?? null,
              clusters: tasks?.k_tasks ?? null,
            }}
            audit={tasks?.audit ?? {}}
            onInfer={() => api.tasks.infer(undefined, workers)}
            onBuildTree={api.tasks.buildTree}
            preview={api.tasks.preview}
            onConfirm={api.tasks.confirm}
            loadTaxonomy={async () => {
              const t = await api.tasks.taxonomy();
              return { roots: t.domains, hasHeadcount: t.has_headcount };
            }}
            runJob={runJob}
            busy={busy || job.running}
            progress={showProgress ? <ProgressBar job={job} /> : null}
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
  hasResults,
}: {
  load: () => Promise<JEFramework>;
  loadDefaults: () => Promise<JEFramework>;
  save: (f: JEFramework) => Promise<unknown>;
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
      if (s.normalized_profiles < 3) return "locked";
      return s.named ? "complete" : "active";
    case "profiles":
      if (!s.named) return "locked";
      return s.job_profiles > 0 ? "complete" : "active";
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
      return `${s.k_families} families › ${s.k_categories} categories › ${s.k_profiles} profiles`;
    case "profiles":
      return `${s.job_profiles} profiles, ${s.je_results} evaluated`;
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
    case "profiles":
      return "Name the clusters first.";
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
