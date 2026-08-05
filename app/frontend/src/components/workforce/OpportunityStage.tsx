import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronRight, Flame, LayoutGrid, List, Play, Search } from "lucide-react";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { ProgressBar } from "../wizard/ProgressBar";
import { JobPulse } from "../wizard/JobPulse";
import { HEAT_GRADIENT, OPPORTUNITY_CEILING, opportunityColor } from "../../lib/heat";
import { TaskTreemap } from "./TaskTreemap";
import { useJobStream } from "../../hooks/useJobStream";
import type { workforceApi } from "../../services/workforceApi";
import type {
  ClusterOpportunity,
  ClusterOpportunityReport,
  OpportunityStatus,
  RoleOpportunity,
  RoleOpportunityReport,
} from "../../types/workforce";

/**
 * Step 3 — the AI opportunity assessment.
 *
 * One LLM call per task cluster, so this is the first Work Architecture Studio step that
 * spends real money on a real project: 750 clusters is 750 calls. The cost is stated
 * before the button is pressed and a small calibration run is offered first, following
 * the clustering gate's precedent — the point being that nobody discovers the spend
 * afterwards.
 *
 * Two readings of the same assessment, because two different people want it. The
 * role-level report answers "what does this mean for this job" (demo section 6); the
 * cluster report answers "where should we build first", which is what step 6 keys off.
 */

type Api = ReturnType<typeof workforceApi>;
type View = "roles" | "clusters";

const CALIBRATION_N = 10;

export function OpportunityStage({
  api,
  onError,
  onAssessed,
}: {
  api: Api;
  onError: (message: string) => void;
  /** The graph is rebuilt server-side after a run; this lets the page refetch it. */
  onAssessed: () => void;
}) {
  const [status, setStatus] = useState<OpportunityStatus | null>(null);
  const [roles, setRoles] = useState<RoleOpportunityReport | null>(null);
  const [clusters, setClusters] = useState<ClusterOpportunityReport | null>(null);
  const [view, setView] = useState<View>("roles");
  const [heat, setHeat] = useState(true);

  const load = useCallback(async () => {
    try {
      const s = await api.opportunityStatus();
      setStatus(s);
      if (s.assessed > 0) {
        // Both reports, since switching view should not cost a round trip — they are
        // reads of state with no model calls behind them.
        const [r, c] = await Promise.all([api.opportunityRoles(), api.opportunityClusters()]);
        setRoles(r);
        setClusters(c);
      }
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  }, [api, onError]);

  const { state: job, attach } = useJobStream(() => {
    void load();
    onAssessed();
  });

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (body: { limit?: number; redo?: boolean }) => {
    try {
      const h = await api.assess(body);
      attach(h.job_id, h.stage);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  // Actions are per task cluster, so one lookup serves every role that does that work.
  // The cluster report is already loaded for the other view; this reuses it rather than
  // repeating 3,800 action rows once per role in the roles payload.
  const actionsByCluster = useMemo(() => {
    const map = new Map<number, ClusterOpportunity["actions"]>();
    for (const c of clusters?.clusters ?? []) map.set(c.cluster_id, c.actions);
    return map;
  }, [clusters]);

  if (!status) return <p className="text-[12px] text-text-muted">Loading…</p>;

  const { remaining, assessed, task_clusters } = status;
  const calibration = Math.min(CALIBRATION_N, remaining);

  return (
    <div className="space-y-3">
      <div className="rounded-[10px] border border-border bg-panel px-4 py-3">
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
          <Stat label="Task clusters" value={task_clusters} />
          <Stat label="Assessed" value={assessed} />
          <Stat label="Remaining" value={remaining} />
          <Stat label="Actions" value={status.actions} />
        </div>
        {remaining > 0 && (
          <p className="mt-2 text-[11.5px] leading-snug text-text-secondary">
            Assessing the remaining {remaining.toLocaleString()} is{" "}
            <strong className="text-text">
              {remaining.toLocaleString()} model calls
            </strong>
            , one per task cluster, fanned out concurrently.
          </p>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2.5">
        {calibration > 0 && (
          <Button onClick={() => void run({ limit: calibration })} disabled={job.running}>
            <span className="flex items-center gap-1.5">
              <Play size={12} />
              Assess {calibration} to check calibration
            </span>
          </Button>
        )}
        {remaining > 0 && (
          <Button
            variant="primary"
            onClick={() => void run({})}
            disabled={job.running}
            title={`${remaining} model calls`}
          >
            <span className="flex items-center gap-1.5">
              <Play size={12} />
              Assess all {remaining.toLocaleString()} remaining
            </span>
          </Button>
        )}
        {remaining === 0 && assessed > 0 && (
          <Button onClick={() => void run({ redo: true })} disabled={job.running}>
            <span className="flex items-center gap-1.5">
              <Play size={12} />
              Re-assess all {assessed.toLocaleString()}
            </span>
          </Button>
        )}
        <JobPulse job={job} />
      </div>

      {(job.running || job.summary || job.error) && <ProgressBar job={job} />}

      {assessed > 0 && <AuditNote status={status} />}

      {assessed > 0 && (roles || clusters) && (
        <>
          <div className="flex flex-wrap items-center gap-3 rounded-[10px] border border-border bg-panel px-4 py-2.5">
            <span className="flex gap-1">
              {(["roles", "clusters"] as View[]).map((v) => (
                <button
                  key={v}
                  onClick={() => setView(v)}
                  className={`rounded-[6px] border px-2.5 py-0.5 text-[11.5px] font-semibold transition-colors ${
                    view === v
                      ? "border-accent bg-accent-bg text-accent"
                      : "border-border bg-card text-text-secondary hover:border-accent"
                  }`}
                >
                  {v === "roles" ? "By role" : "By task cluster"}
                </button>
              ))}
            </span>
            <button
              onClick={() => setHeat((h) => !h)}
              className={`flex items-center gap-1.5 rounded-[6px] border px-2.5 py-0.5 text-[11.5px] font-semibold transition-colors ${
                heat
                  ? "border-accent bg-accent-bg text-accent"
                  : "border-border bg-card text-text-secondary hover:border-accent"
              }`}
            >
              <Flame size={11} /> Heat map
            </button>
            {heat && (
              <span className="flex items-center gap-1.5 text-[10.5px] text-text-muted">
                0%
                <span
                  className="h-2.5 w-24 rounded-full"
                  style={{ background: HEAT_GRADIENT }}
                />
                {OPPORTUNITY_CEILING}%+ automatable
              </span>
            )}
          </div>

          {view === "roles" && roles && (
            <RoleReport report={roles} heat={heat} actionsByCluster={actionsByCluster} />
          )}
          {view === "clusters" && clusters && <ClusterReport report={clusters} heat={heat} />}
        </>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-[15px] font-bold tabular-nums text-accent">
        {typeof value === "number" ? value.toLocaleString() : value}
      </span>
      <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-muted">
        {label}
      </span>
    </span>
  );
}

/**
 * The one quality check worth putting on screen.
 *
 * A run can complete cleanly and still be worthless: if every cluster comes back
 * between 40 and 55, the model has hedged rather than judged, and every number
 * downstream inherits that. The spread between the 10th and 90th percentile is the
 * cheapest test of it, so it is reported rather than buried in an audit dict.
 */
function AuditNote({ status }: { status: OpportunityStatus }) {
  const a = status.audit;
  if (!a || a.mean_automation === undefined) return null;
  const spread = (a.automation_p90 ?? 0) - (a.automation_p10 ?? 0);
  const good = a.discriminating !== false;
  return (
    <div
      className={`rounded-[10px] border px-4 py-2.5 ${
        good ? "border-border bg-panel" : "border-warning-border bg-warning-bg"
      }`}
    >
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
        <Badge color={good ? "success" : "warning"}>
          {good ? "Discriminating" : "Not discriminating"}
        </Badge>
        <span className="text-[11.5px] text-text-secondary">
          Automation mean <strong className="text-text">{a.mean_automation}%</strong>, p10{" "}
          {a.automation_p10}% to p90 {a.automation_p90}% — a {spread.toFixed(0)} point spread
        </span>
        <span className="text-[11.5px] text-text-secondary">
          Augmentation mean <strong className="text-text">{a.mean_augmentation}%</strong>
        </span>
        {!!a.clamped && (
          <span className="text-[11.5px] text-warning">{a.clamped} clamped into range</span>
        )}
        {!!a.clusters_failed && (
          <span className="text-[11.5px] text-brand">{a.clusters_failed} failed</span>
        )}
      </div>
      {!good && (
        <p className="mt-1.5 text-[11.5px] leading-snug text-text-secondary">
          Scores are clustered in the middle of the range, which usually means the model
          hedged rather than judged. Treat the ranking as weak evidence and re-assess
          before putting these numbers in front of a client.
        </p>
      )}
    </div>
  );
}

/** Percentage as a coloured chip. Heat off leaves it plain, which is the honest
 *  default when someone wants to read the numbers rather than the picture. */
function Pct({ value, heat }: { value: number | null; heat: boolean }) {
  if (value === null || value === undefined)
    return <span className="text-[11px] text-text-muted">—</span>;
  const bg = heat ? opportunityColor(value) : undefined;
  return (
    <span
      className="inline-block min-w-[2.6rem] rounded-[5px] border px-1.5 py-0.5 text-center text-[11px] font-bold tabular-nums"
      style={
        heat
          ? { background: bg, color: "#fff", borderColor: bg }
          : { borderColor: "var(--color-border)", color: "var(--color-text)" }
      }
    >
      {value.toFixed(0)}%
    </span>
  );
}

// ---------------------------------------------------------------------------
// By role — instructions step 3, demo section 6
// ---------------------------------------------------------------------------
function RoleReport({
  report,
  heat,
  actionsByCluster,
}: {
  report: RoleOpportunityReport;
  heat: boolean;
  actionsByCluster: Map<number, ClusterOpportunity["actions"]>;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const t = report.totals;

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? report.roles.filter((r) => r.title.toLowerCase().includes(q)) : report.roles;
  }, [report.roles, query]);

  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1 rounded-[10px] border border-border bg-panel px-4 py-3">
        <Stat label="Roles assessed" value={`${t.roles_assessed}/${t.roles}`} />
        <Stat label="Mean automatable" value={`${t.mean_automation}%`} />
        <Stat label="Mean augmentable" value={`${t.mean_augmentation}%`} />
        {t.headcount !== null && <Stat label="Headcount" value={t.headcount} />}
        {t.fte_released !== null ? (
          <Stat label="FTE released" value={t.fte_released} />
        ) : (
          <span className="text-[11.5px] text-text-muted">
            No headcount in the source data, so released capacity is a share of each role's
            week rather than an FTE count.
          </span>
        )}
      </div>

      {t.mean_coverage < 99 && (
        <p className="text-[11.5px] text-text-secondary">
          On average {t.mean_coverage}% of each role's time sits in an assessed cluster.
          Scores are means over that part only — the rest is unknown, not zero.
        </p>
      )}

      <label className="flex items-center gap-2 rounded-[10px] border border-border bg-card px-3 py-1.5">
        <Search size={13} className="shrink-0 text-text-muted" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={`Filter ${report.roles.length.toLocaleString()} roles`}
          className="min-w-0 flex-1 bg-transparent text-[12px] text-text outline-none placeholder:text-text-muted"
        />
      </label>

      <div className="overflow-hidden rounded-[10px] border border-border">
        <div className="flex items-center gap-3 border-b border-border bg-panel px-3 py-1.5 text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
          <span className="w-4" />
          <span className="min-w-0 flex-1">Role</span>
          {report.has_headcount && <span className="w-14 text-right">Heads</span>}
          <span className="w-16 text-right">Automate</span>
          <span className="w-16 text-right">Augment</span>
          <span className="w-20 text-right">
            {report.has_headcount ? "FTE freed" : "Coverage"}
          </span>
        </div>
        {shown.slice(0, 400).map((r) => (
          <RoleRow
            key={r.profile_key}
            role={r}
            heat={heat}
            actionsByCluster={actionsByCluster}
            hasHeadcount={report.has_headcount}
            open={open === r.profile_key}
            onToggle={() => setOpen(open === r.profile_key ? null : r.profile_key)}
          />
        ))}
        {shown.length === 0 && (
          <p className="px-3 py-3 text-[12px] text-text-muted">No role matches that filter.</p>
        )}
      </div>
      {shown.length > 400 && (
        <p className="text-[11px] text-text-muted">
          Showing the first 400 of {shown.length.toLocaleString()} — filter to narrow.
        </p>
      )}
    </div>
  );
}

function RoleRow({
  role,
  heat,
  actionsByCluster,
  hasHeadcount,
  open,
  onToggle,
}: {
  role: RoleOpportunity;
  heat: boolean;
  actionsByCluster: Map<number, ClusterOpportunity["actions"]>;
  hasHeadcount: boolean;
  open: boolean;
  onToggle: () => void;
}) {
  // Which task within this role has its actions showing. One at a time: the actions are
  // four or five rows each, and several open at once turns the role back into a wall.
  const [openTask, setOpenTask] = useState<number | null>(null);
  // The treemap leads because the shape of the week is what a client reacts to. The list
  // is kept rather than replaced: it is the only view that shows the arithmetic behind a
  // task's score, and the only one usable without a pointer.
  const [shape, setShape] = useState<"map" | "list">("map");
  return (
    <div className="border-b border-border last:border-0">
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-3 px-3 py-1.5 text-left transition-colors hover:bg-panel"
      >
        <ChevronRight
          size={12}
          className={`shrink-0 text-text-muted transition-transform ${open ? "rotate-90" : ""}`}
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[12px] font-semibold text-text">{role.title}</span>
          <span className="text-[10.5px] text-text-muted">
            {role.n_tasks} tasks
            {role.coverage < 99 && ` · ${role.coverage}% assessed`}
          </span>
        </span>
        {hasHeadcount && (
          <span className="w-14 text-right text-[11.5px] tabular-nums text-text-secondary">
            {role.headcount ?? "—"}
          </span>
        )}
        <span className="w-16 text-right">
          <Pct value={role.automation} heat={heat} />
        </span>
        <span className="w-16 text-right">
          <Pct value={role.augmentation} heat={heat} />
        </span>
        <span className="w-20 text-right text-[11.5px] font-semibold tabular-nums text-text">
          {hasHeadcount
            ? role.fte_released !== null
              ? role.fte_released.toFixed(2)
              : "—"
            : `${role.coverage}%`}
        </span>
      </button>

      {open && shape === "map" && (
        <div className="bg-panel px-3 pb-2.5 pt-1.5">
          <ShapeToggle shape={shape} onChange={setShape} />
          <TaskTreemap tasks={role.tasks} actionsByCluster={actionsByCluster} heat={heat} />
        </div>
      )}

      {open && shape === "list" && (
        <div className="bg-panel px-3 pb-2.5 pt-1.5">
          <ShapeToggle shape={shape} onChange={setShape} />
          <div className="flex items-center gap-3 py-1 text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
            <span className="w-[10px] shrink-0" />
            <span className="min-w-0 flex-1">Task</span>
            <span className="w-14 text-right">% of role</span>
            <span className="w-16 text-right">Automate</span>
            <span className="w-16 text-right">Augment</span>
          </div>
          {role.tasks.map((t, i) => {
            const actions = actionsByCluster.get(t.cluster_id) ?? [];
            const isOpen = openTask === t.cluster_id;
            return (
              <div key={`${t.cluster_id}-${i}`} className="border-t border-border">
                <button
                  onClick={() => setOpenTask(isOpen ? null : t.cluster_id)}
                  disabled={actions.length === 0}
                  className="flex w-full items-center gap-3 py-1 text-left transition-colors hover:bg-card disabled:cursor-default disabled:hover:bg-transparent"
                >
                  <ChevronRight
                    size={10}
                    className={`shrink-0 transition-transform ${
                      isOpen ? "rotate-90 text-text-secondary" : "text-text-muted"
                    } ${actions.length === 0 ? "invisible" : ""}`}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[11.5px] text-text">{t.name}</span>
                    <span className="block truncate text-[10.5px] text-text-muted">
                      {t.cluster}
                      {actions.length > 0 && ` · ${actions.length} actions`}
                    </span>
                  </span>
                  <span className="w-14 text-right text-[11.5px] tabular-nums text-text-secondary">
                    {t.proportion.toFixed(1)}
                  </span>
                  <span className="w-16 text-right">
                    <Pct value={t.automation} heat={heat} />
                  </span>
                  <span className="w-16 text-right">
                    <Pct value={t.augmentation} heat={heat} />
                  </span>
                </button>

                {isOpen && (
                  <div className="mb-1 ml-[22px] rounded-[6px] border border-border bg-card px-2.5 py-1.5">
                    <div className="flex items-center gap-3 pb-1 text-[9.5px] font-extrabold uppercase tracking-wider text-text-muted">
                      <span className="min-w-0 flex-1">Action within this task</span>
                      <span className="w-14 text-right">% of task</span>
                      <span className="w-16 text-right">Automate</span>
                      <span className="w-16 text-right">Augment</span>
                    </div>
                    {actions.map((a) => (
                      <div
                        key={a.name}
                        className="flex items-start gap-3 border-t border-border py-1"
                      >
                        <span className="min-w-0 flex-1">
                          <span className="block text-[11px] font-semibold text-text">
                            {a.name}
                          </span>
                          <span className="block text-[10.5px] leading-snug text-text-secondary">
                            {a.definition}
                          </span>
                        </span>
                        <span className="w-14 shrink-0 text-right text-[11px] tabular-nums text-text-secondary">
                          {a.pct_of_task.toFixed(0)}
                        </span>
                        <span className="w-16 shrink-0 text-right">
                          <Pct value={a.automation} heat={heat} />
                        </span>
                        <span className="w-16 shrink-0 text-right">
                          <Pct value={a.augmentation} heat={heat} />
                        </span>
                      </div>
                    ))}
                    {/* The task's own score is the effort-weighted mean of these, so the
                        rows above should reconcile to the chip on the task line. */}
                    <p className="border-t border-border pt-1 text-[10px] text-text-muted">
                      The task's {t.automation?.toFixed(0)}% automatable is these actions
                      weighted by their share of the task.
                    </p>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Treemap or list, for one expanded role. */
function ShapeToggle({
  shape,
  onChange,
}: {
  shape: "map" | "list";
  onChange: (s: "map" | "list") => void;
}) {
  return (
    <div className="mb-1.5 flex rounded-[6px] border border-border bg-card p-0.5 w-fit">
      {(["map", "list"] as const).map((s) => (
        <button
          key={s}
          onClick={() => onChange(s)}
          className={`flex items-center gap-1 rounded-[4px] px-2 py-0.5 text-[10.5px] font-semibold transition-colors ${
            shape === s ? "bg-accent-bg text-accent" : "text-text-secondary hover:text-text"
          }`}
        >
          {s === "map" ? <LayoutGrid size={10} /> : <List size={10} />}
          {s === "map" ? "Task map" : "Task list"}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// By task cluster — the build-first ranking step 6 keys off
// ---------------------------------------------------------------------------
type Sort = "absorbable" | "automation" | "augmentation";

const SORT_LABEL: Record<Sort, string> = {
  absorbable: "Time absorbable",
  automation: "Automation %",
  augmentation: "Augmentation %",
};

function ClusterReport({ report, heat }: { report: ClusterOpportunityReport; heat: boolean }) {
  const [sort, setSort] = useState<Sort>("absorbable");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<number | null>(null);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const rows = q
      ? report.clusters.filter(
          (c) =>
            c.name.toLowerCase().includes(q) ||
            c.domain.toLowerCase().includes(q) ||
            c.category.toLowerCase().includes(q),
        )
      : report.clusters;
    return [...rows].sort((a, b) => b[sort] - a[sort]);
  }, [report.clusters, query, sort]);

  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
          Rank by
        </span>
        {(Object.keys(SORT_LABEL) as Sort[]).map((s) => (
          <button
            key={s}
            onClick={() => setSort(s)}
            className={`rounded-[6px] border px-2 py-0.5 text-[11.5px] font-semibold transition-colors ${
              sort === s
                ? "border-accent bg-accent-bg text-accent"
                : "border-border bg-card text-text-secondary hover:border-accent"
            }`}
          >
            {SORT_LABEL[s]}
          </button>
        ))}
        <span className="text-[11px] text-text-muted">
          {report.clusters.length.toLocaleString()} of {report.total_clusters.toLocaleString()}{" "}
          clusters assessed
        </span>
      </div>

      <p className="text-[11.5px] leading-snug text-text-secondary">
        <strong className="text-text">Time absorbable</strong> is automation weighted by how
        much {report.has_headcount ? "of the workforce's capacity" : "role time"} the cluster
        consumes, in {report.unit}. It is the honest build-first order: a rare, highly
        automatable task ranks below a common, moderately automatable one.
      </p>

      <label className="flex items-center gap-2 rounded-[10px] border border-border bg-card px-3 py-1.5">
        <Search size={13} className="shrink-0 text-text-muted" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter by cluster, category or domain"
          className="min-w-0 flex-1 bg-transparent text-[12px] text-text outline-none placeholder:text-text-muted"
        />
      </label>

      <div className="overflow-hidden rounded-[10px] border border-border">
        <div className="flex items-center gap-3 border-b border-border bg-panel px-3 py-1.5 text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
          <span className="w-4" />
          <span className="min-w-0 flex-1">Task cluster</span>
          <span className="w-12 text-right">Roles</span>
          <span className="w-16 text-right">Automate</span>
          <span className="w-16 text-right">Augment</span>
          <span className="w-20 text-right">{report.unit} freed</span>
        </div>
        {shown.slice(0, 400).map((c) => (
          <ClusterRow
            key={c.cluster_id}
            cluster={c}
            heat={heat}
            open={open === c.cluster_id}
            onToggle={() => setOpen(open === c.cluster_id ? null : c.cluster_id)}
          />
        ))}
      </div>
      {shown.length > 400 && (
        <p className="text-[11px] text-text-muted">
          Showing the first 400 of {shown.length.toLocaleString()} — filter to narrow.
        </p>
      )}
    </div>
  );
}

function ClusterRow({
  cluster,
  heat,
  open,
  onToggle,
}: {
  cluster: ClusterOpportunity;
  heat: boolean;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="border-b border-border last:border-0">
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-3 px-3 py-1.5 text-left transition-colors hover:bg-panel"
      >
        <ChevronRight
          size={12}
          className={`shrink-0 text-text-muted transition-transform ${open ? "rotate-90" : ""}`}
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[12px] font-semibold text-text">
            {cluster.name}
            {cluster.clamped && (
              <Badge color="warning" className="ml-1.5 align-middle">
                clamped
              </Badge>
            )}
          </span>
          <span className="block truncate text-[10.5px] text-text-muted">
            {cluster.domain} › {cluster.category} · {cluster.n_actions} actions
          </span>
        </span>
        <span className="w-12 text-right text-[11.5px] tabular-nums text-text-secondary">
          {cluster.roles}
        </span>
        <span className="w-16 text-right">
          <Pct value={cluster.automation} heat={heat} />
        </span>
        <span className="w-16 text-right">
          <Pct value={cluster.augmentation} heat={heat} />
        </span>
        <span className="w-20 text-right text-[11.5px] font-semibold tabular-nums text-text">
          {cluster.absorbable.toFixed(2)}
        </span>
      </button>

      {open && (
        <div className="bg-panel px-3 pb-2.5 pt-1">
          <div className="flex items-center gap-3 py-1 text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
            <span className="min-w-0 flex-1">Action</span>
            <span className="w-14 text-right">% of task</span>
            <span className="w-16 text-right">Automate</span>
            <span className="w-16 text-right">Augment</span>
          </div>
          {cluster.actions.map((a) => (
            <div key={a.name} className="flex items-start gap-3 border-t border-border py-1.5">
              <span className="min-w-0 flex-1">
                <span className="block text-[11.5px] font-semibold text-text">{a.name}</span>
                <span className="block text-[10.5px] leading-snug text-text-secondary">
                  {a.definition}
                </span>
              </span>
              <span className="w-14 shrink-0 text-right text-[11.5px] tabular-nums text-text-secondary">
                {a.pct_of_task.toFixed(0)}
              </span>
              <span className="w-16 shrink-0 text-right">
                <Pct value={a.automation} heat={heat} />
              </span>
              <span className="w-16 shrink-0 text-right">
                <Pct value={a.augmentation} heat={heat} />
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
