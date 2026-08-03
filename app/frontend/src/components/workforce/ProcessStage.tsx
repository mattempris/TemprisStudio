import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronRight, GitBranch, Sparkles, Trash2, Upload } from "lucide-react";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { ProgressBar } from "../wizard/ProgressBar";
import { JobPulse } from "../wizard/JobPulse";
import { useJobStream } from "../../hooks/useJobStream";
import type { workforceApi } from "../../services/workforceApi";
import type { ProcessRecord, ProcessReport } from "../../types/workforce";

/**
 * Steps 2 and 4 — process upload, mapping, and the as-is/to-be assessment.
 *
 * Both live here because step 4 is per-process and splitting them across two screens
 * would mean navigating away from the thing being assessed.
 *
 * The limit worth being loud about: a process *diagram* yields its step labels and
 * whatever ordering the file carried, not its arrows. Matching connector geometry back
 * to boxes by coordinate is a different and far less reliable job, so the sequence is
 * inferred from the labels and the prose — and where the document gave nothing reliable
 * to go on, the row says so rather than presenting a guess as a flowchart.
 */

type Api = ReturnType<typeof workforceApi>;

const CONFIDENCE_COLOUR = { high: "success", medium: "warning", low: "brand" } as const;

export function ProcessStage({
  api,
  onError,
  /** Whether step 3 has run — the assessment reads its scores. */
  hasOpportunity,
}: {
  api: Api;
  onError: (m: string) => void;
  hasOpportunity: boolean;
}) {
  const [report, setReport] = useState<ProcessReport | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setReport(await api.processes());
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  }, [api, onError]);

  const { state: job, attach } = useJobStream(() => void load());

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (fn: () => Promise<{ job_id: string; stage: string }>) => {
    try {
      const h = await fn();
      attach(h.job_id, h.stage);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  if (!report) return <p className="text-[12px] text-text-muted">Loading…</p>;

  return (
    <div className="space-y-3">
      <div className="rounded-[10px] border border-border bg-panel px-4 py-3">
        <div className="flex flex-wrap items-center gap-2.5">
          <input
            ref={fileInput}
            type="file"
            accept={report.supported_extensions.join(",")}
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void run(() => api.uploadProcess(f));
              e.target.value = "";
            }}
          />
          <Button variant="primary" onClick={() => fileInput.current?.click()} disabled={job.running}>
            <span className="flex items-center gap-1.5">
              <Upload size={12} /> Upload a process document
            </span>
          </Button>
          <JobPulse job={job} />
        </div>
        <p className="mt-2 text-[11.5px] leading-snug text-text-secondary">
          {report.supported_extensions.join(", ")}. A diagram gives up its step labels and
          whatever order the file carried — <strong className="text-text">not its
          arrows</strong>. Where the sequence had to be inferred the process says so, and
          you should not rely on adjacency in that case.
        </p>
      </div>

      {(job.running || job.summary || job.error) && <ProgressBar job={job} />}

      {report.processes.length === 0 ? (
        <p className="rounded-[10px] border border-border bg-panel px-4 py-6 text-center text-[12px] text-text-muted">
          No processes yet. This step is optional — everything else works without it.
        </p>
      ) : (
        <div className="space-y-2.5">
          {report.processes.map((p) => (
            <ProcessCard
              key={p.id}
              process={p}
              api={api}
              busy={job.running}
              hasOpportunity={hasOpportunity}
              expanded={open === p.id}
              onToggle={() => setOpen(open === p.id ? null : p.id)}
              onMap={() => void run(() => api.mapProcess(p.id))}
              onAssess={() => void run(() => api.assessProcess(p.id))}
              onDelete={() =>
                void api
                  .deleteProcess(p.id)
                  .then(load)
                  .catch((e) => onError(e instanceof Error ? e.message : String(e)))
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ProcessCard({
  process: p,
  busy,
  hasOpportunity,
  expanded,
  onToggle,
  onMap,
  onAssess,
  onDelete,
}: {
  process: ProcessRecord;
  api: Api;
  busy: boolean;
  hasOpportunity: boolean;
  expanded: boolean;
  onToggle: () => void;
  onMap: () => void;
  onAssess: () => void;
  onDelete: () => void;
}) {
  const a = p.assessment;
  return (
    <div className="overflow-hidden rounded-[10px] border border-border">
      <div className="flex items-start gap-3 bg-panel px-3.5 py-2.5">
        <button onClick={onToggle} className="mt-0.5 shrink-0 text-text-muted">
          <ChevronRight size={13} className={expanded ? "rotate-90" : ""} />
        </button>
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-bold text-text">{p.process_name}</p>
          <p className="mt-0.5 text-[11.5px] leading-snug text-text-secondary">{p.summary}</p>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-text-secondary">
            <span>{p.steps.length} steps</span>
            <span>{p.manual_steps} manual</span>
            <span>{p.handoffs} handoffs</span>
            <span>{p.sign_offs} sign-offs</span>
            <Badge color={CONFIDENCE_COLOUR[p.ordering_confidence]}>
              {p.ordering_confidence} ordering confidence
            </Badge>
            {p.mapped_at && p.unmatched_steps > 0 && (
              <Badge color="purple">{p.unmatched_steps} unmatched</Badge>
            )}
            <span className="text-text-muted">{p.filename}</span>
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1.5">
          {!p.mapped_at ? (
            <Button onClick={onMap} disabled={busy}>
              <span className="flex items-center gap-1.5">
                <GitBranch size={12} /> Map onto tasks
              </span>
            </Button>
          ) : !a ? (
            <Button
              variant="primary"
              onClick={onAssess}
              disabled={busy || !hasOpportunity}
              title={hasOpportunity ? "" : "Run the AI opportunity assessment first"}
            >
              <span className="flex items-center gap-1.5">
                <Sparkles size={12} /> Assess as-is / to-be
              </span>
            </Button>
          ) : (
            <Button onClick={onAssess} disabled={busy}>
              Re-assess
            </Button>
          )}
          <button
            onClick={onDelete}
            aria-label="Remove process"
            className="text-text-muted transition-colors hover:text-brand"
          >
            <Trash2 size={12} />
          </button>
        </div>
      </div>

      {a && (
        <div className="border-t border-border bg-card px-3.5 py-3">
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            <Delta label="Steps" from={a.as_is_steps} to={a.to_be_steps} />
            <Delta
              label="Manual touchpoints"
              from={a.as_is_manual_touchpoints}
              to={a.to_be_manual_touchpoints}
            />
            <Delta label="Actors" from={a.as_is_actors} to={a.to_be_actors} />
            <Delta label="Sign-offs" from={a.as_is_sign_offs} to={a.to_be_sign_offs} />
            <span className="flex items-baseline gap-1.5">
              <span className="text-[15px] font-bold tabular-nums text-accent">
                −{a.effort_reduction_pct}%
              </span>
              <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-muted">
                handler effort
              </span>
            </span>
            <span className="flex items-baseline gap-1.5">
              <span className="text-[15px] font-bold tabular-nums text-accent">
                −{a.elapsed_reduction_pct}%
              </span>
              <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-muted">
                elapsed time
              </span>
            </span>
          </div>
          <p className="mt-2.5 text-[11.5px] leading-snug text-text">
            <strong>Today.</strong> {a.as_is_narrative}
          </p>
          <p className="mt-1.5 text-[11.5px] leading-snug text-text">
            <strong>After.</strong> {a.to_be_narrative}
          </p>
          <div className="mt-2 grid gap-3 sm:grid-cols-3">
            <Bullets title="What changes" items={a.what_changes} />
            <Bullets title="Risks" items={a.risks} />
            <Bullets title="Prerequisites first" items={a.prerequisites} />
          </div>
        </div>
      )}

      {expanded && (
        <div className="border-t border-border bg-panel px-3.5 pb-2.5 pt-1.5">
          <div className="flex items-center gap-3 py-1 text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
            <span className="w-6" />
            <span className="min-w-0 flex-1">Step</span>
            <span className="w-40">Task cluster</span>
            <span className="w-14 text-right">Match</span>
          </div>
          {p.steps.map((s) => (
            <div key={s.sequence} className="flex items-start gap-3 border-t border-border py-1.5">
              <span className="w-6 shrink-0 text-right text-[11px] tabular-nums text-text-muted">
                {s.sequence}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[11.5px] font-semibold text-text">
                  {s.name}
                  {s.automated && <Badge color="teal" className="ml-1.5 align-middle">automated</Badge>}
                  {s.handoff && <Badge color="orange" className="ml-1.5 align-middle">handoff</Badge>}
                  {s.sign_off && <Badge color="warning" className="ml-1.5 align-middle">sign-off</Badge>}
                </span>
                <span className="block text-[10.5px] leading-snug text-text-secondary">
                  {s.description}
                </span>
                <span className="block text-[10.5px] text-text-muted">
                  {s.actor}
                  {s.system && s.system !== "none" && ` · ${s.system}`}
                </span>
              </span>
              <span className="w-40 shrink-0">
                {s.task_cluster_id !== null ? (
                  <>
                    <span className="block truncate text-[11px] text-text">
                      {s.task_cluster_name}
                    </span>
                    {s.routed_by_llm && (
                      <span className="text-[10px] text-text-muted">confirmed by model</span>
                    )}
                  </>
                ) : (
                  <span className="block text-[10.5px] leading-snug text-purple">
                    No task cluster — work the job descriptions never mentioned
                  </span>
                )}
              </span>
              <span className="w-14 shrink-0 text-right text-[11px] tabular-nums text-text-muted">
                {p.mapped_at ? s.match_cosine.toFixed(2) : "—"}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** As-is → to-be, with the direction of travel visible without arithmetic. */
function Delta({ label, from, to }: { label: string; from: number; to: number }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-[15px] font-bold tabular-nums text-text-muted">{from}</span>
      <span className="text-[11px] text-text-muted">→</span>
      <span className="text-[15px] font-bold tabular-nums text-accent">{to}</span>
      <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-muted">
        {label}
      </span>
    </span>
  );
}

function Bullets({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="mb-1 text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
        {title}
      </p>
      <ul className="space-y-0.5">
        {items.map((x, i) => (
          <li key={i} className="text-[11px] leading-snug text-text-secondary">
            · {x}
          </li>
        ))}
      </ul>
    </div>
  );
}
