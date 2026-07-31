import { useCallback, useEffect, useMemo, useState } from "react";
import { Play, Upload } from "lucide-react";
import { pipelineApi } from "../services/pipelineApi";
import { useJobStream } from "../hooks/useJobStream";
import type { ProfileRow, StageSummary } from "../types/pipeline";
import { StageSection, type StageState } from "../components/wizard/StageSection";
import { ProgressBar } from "../components/wizard/ProgressBar";
import { ClusterKPanel } from "../components/pipeline/ClusterKPanel";
import { DedupePanel } from "../components/pipeline/DedupePanel";
import { JEResultsBrowser } from "../components/pipeline/JEResultsBrowser";
import { Button } from "../components/ui/Button";
import { cn } from "../lib/cn";

const STAGES = [
  { id: "ingest", title: "Input assets", description: "Upload job description files or an HRIS spreadsheet." },
  { id: "strip", title: "Strip irrelevant content", description: "Remove company boilerplate, benefits and recruitment logistics — keeping only what already exists." },
  { id: "dedupe", title: "Deduplicate", description: "Group jobs that describe the same role, using a similarity threshold you control." },
  { id: "normalize", title: "Normalise descriptions", description: "Produce a structured summary per distinct job: purpose, key tasks, reporting line, budget." },
  { id: "cluster", title: "Cluster and name", description: "Build the Family › Category › Profile hierarchy and give each cluster an industry-standard name." },
  { id: "profiles", title: "Job profiles and evaluation", description: "Generate a job profile document per cluster and evaluate it against your job evaluation framework." },
] as const;

export function PipelinePage({ clientSlug, projectSlug }: { clientSlug: string; projectSlug: string }) {
  const api = useMemo(() => pipelineApi(clientSlug, projectSlug), [clientSlug, projectSlug]);
  const [summary, setSummary] = useState<StageSummary | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<ProfileRow[]>([]);
  const [caps, setCaps] = useState({ html: true, docx: true, pdf: false });
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const s = await api.summary();
      setSummary(s);
      if (s.job_profiles > 0) {
        const p = await api.listProfiles();
        setProfiles(p.profiles);
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
  }, [refresh, api]);

  // Re-attach to a job already running server-side (e.g. after a page reload) —
  // the backend keeps the job alive and replays its history.
  useEffect(() => {
    if (summary?.active_job_id && !job.running && job.jobId !== summary.active_job_id) {
      attach(summary.active_job_id, summary.active_job_stage ?? "");
    }
  }, [summary?.active_job_id, summary?.active_job_stage, job.running, job.jobId, attach]);

  // Open the first stage that still needs attention.
  useEffect(() => {
    if (!summary || expanded !== null) return;
    setExpanded(firstIncompleteStage(summary));
  }, [summary, expanded]);

  const state = useCallback(
    (id: string): StageState => (summary ? stageState(id, summary) : "locked"),
    [summary],
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
      </nav>

      <div className="min-w-0 flex-1 space-y-4">
        {error && (
          <div className="rounded-[10px] border border-brand-border bg-brand-bg px-4 py-3 text-[12.5px] text-text">
            {error}
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
              summary={stageSummaryLine(s.id, summary)}
              lockedReason={lockedReason(s.id)}
              expanded={expanded === s.id}
              onToggle={() => setExpanded(expanded === s.id ? null : s.id)}
            >
              {renderStage(s.id)}
            </StageSection>
          );
        })}
      </div>
    </div>
  );

  function renderStage(id: string) {
    const showProgress = job.stage !== null || job.error;

    switch (id) {
      case "ingest":
        return (
          <div>
            <label className="flex cursor-pointer flex-col items-center gap-2 rounded-[10px] border-2 border-dashed border-border bg-panel px-6 py-8 text-center hover:border-accent">
              <Upload size={20} className="text-text-muted" />
              <span className="text-[13px] font-semibold text-text">
                Drop job description files here, or click to browse
              </span>
              <span className="text-[11.5px] text-text-muted">PDF, DOC, DOCX, TXT or HTML</span>
              <input
                type="file"
                multiple
                accept=".pdf,.doc,.docx,.txt,.html,.htm"
                className="hidden"
                onChange={(e) => {
                  const files = Array.from(e.target.files ?? []);
                  if (files.length) void act(() => api.uploadFiles(files));
                }}
              />
            </label>
            <p className="mt-3 text-[12.5px] text-text-secondary">
              {summary!.raw_records} job{summary!.raw_records === 1 ? "" : "s"} loaded.
            </p>
          </div>
        );

      case "strip":
        return (
          <div className="space-y-3">
            <Button variant="primary" onClick={() => runJob(api.startStrip)} disabled={busy || job.running}>
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
            <Button variant="primary" onClick={() => runJob(api.startNormalize)} disabled={busy || job.running}>
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
            <div className="space-y-3">
              <Button
                variant="primary"
                onClick={() => runJob(() => api.startProfileGeneration(true))}
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

      default:
        return null;
    }
  }
}

function stageState(id: string, s: StageSummary): StageState {
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

function stageSummaryLine(id: string, s: StageSummary): string | undefined {
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
    default:
      return "";
  }
}

function firstIncompleteStage(s: StageSummary): string {
  for (const stage of STAGES) {
    if (stageState(stage.id, s) === "active") return stage.id;
  }
  return STAGES[STAGES.length - 1].id;
}
