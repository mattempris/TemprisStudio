import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { ArchitectureGraph, type ColorMode } from "../components/workforce/ArchitectureGraph";
import { OpportunityStage } from "../components/workforce/OpportunityStage";
import { ProductivityStage } from "../components/workforce/ProductivityStage";
import { AgentsStage } from "../components/workforce/AgentsStage";
import { ProcessStage } from "../components/workforce/ProcessStage";
import { FutureRolesStage } from "../components/workforce/FutureRolesStage";
import { ProgressBar } from "../components/wizard/ProgressBar";
import { JobPulse } from "../components/wizard/JobPulse";
import { StageSection } from "../components/wizard/StageSection";
import { StudioToggle, ProceedToWorkDesign } from "../components/wizard/StudioToggle";
import { useSectionScroll } from "../hooks/useSectionScroll";
import { DemoReset } from "../components/wizard/DemoReset";
import { Button } from "../components/ui/Button";
import { CheckboxDropdown } from "../components/ui/CheckboxDropdown";
import { Modal } from "../components/ui/Modal";
import { HEAT_GRADIENT, opportunityColor, opportunitySpan } from "../lib/heat";
import { useJobStream } from "../hooks/useJobStream";
import { workforceApi, studioGates } from "../services/workforceApi";
import type {
  GraphCut,
  GraphFilters,
  GraphLevel,
  GraphNode,
  NodeDetail,
  WorkforceStatus,
} from "../types/workforce";

/**
 * Work Architecture Studio.
 *
 * Same shape as the Job Architecture Studio wizard — sticky step list, scrolling accordion of
 * stages — reusing its components rather than reimplementing them. Steps 1 and 3 are
 * built; the rest are declared and locked, so the shape of the whole thing is visible
 * from the start rather than appearing a step at a time.
 */

const STEPS = [
  {
    id: "architecture",
    title: "Work architecture",
    description:
      "Every anchor role, skill cluster and task cluster, and how they connect. Later steps add processes, agents and actions to the same graph.",
  },
  { id: "processes", title: "Process upload", description: "Upload process documents and map their steps onto the task structure. Optional." },
  { id: "opportunity", title: "AI opportunity assessment", description: "Break each task cluster into actions and score each one for automation and augmentation." },
  { id: "process-opportunity", title: "Process opportunity assessment", description: "Current and future state per uploaded process." },
  // Named for the axis each one acts on rather than for its output. Augmentation is
  // where a person keeps the work and goes faster; automation is where the work leaves
  // the person. That is the distinction the two scores in step 3 exist to draw, and
  // naming the steps after it makes the pair legible at a glance.
  { id: "productivity", title: "Augmentation", description: "Per role, the tasks where AI help gives the most time back — with a downloadable Claude skill for each." },
  { id: "agents", title: "Automation", description: "Per task cluster, a full agent specification, ranked by the time it would release." },
  { id: "future-roles", title: "Future role design", description: "How a role is redesigned once agents absorb the automatable work." },
] as const;

/**
 * Above this, a force-directed layout stops being a picture and becomes a mass.
 *
 * Measured rather than guessed: at the reference project's finest cut — 1,870 nodes
 * and 10,300 links — the simulation takes tens of seconds to settle and then shows
 * an undifferentiated blob. "Finest" is still offered because it is the user's
 * choice and the data is real, but it says so rather than pretending.
 */
const LEGIBLE_NODES = 600;

const LEVEL_LABEL: Record<GraphLevel, string> = {
  family: "Broadest",
  category: "Middle",
  profile: "Finest",
};

export function WorkforcePage({
  clientSlug,
  projectSlug,
  paletteKey,
}: {
  clientSlug: string;
  projectSlug: string;
  paletteKey: string;
}) {
  // Memoised, or every render builds a new client, which changes the identity of
  // every effect dependency below and re-fires them forever. An unmemoised API
  // object is a render loop, not an inefficiency.
  const api = useMemo(() => workforceApi(clientSlug, projectSlug), [clientSlug, projectSlug]);
  const [status, setStatus] = useState<WorkforceStatus | null>(null);
  // Real gates rather than a hardcoded `ready`: being inside this studio says nothing about
  // whether the next one is reachable, and the toggle used to claim both were.
  const gates = useMemo(() => (status ? studioGates(status) : {}), [status]);
  const [cut, setCut] = useState<GraphCut | null>(null);
  const [level, setLevel] = useState<GraphLevel>("family");
  const [expanded, setExpanded] = useState<string[]>([]);
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string>("architecture");
  // Same accordion, same problem as the Job Architecture Studio wizard — the graph and opportunity steps
  // are the tall ones here. See useSectionScroll.
  const { hold, reveal } = useSectionScroll(open);
  const [colorMode, setColorMode] = useState<ColorMode>("entity");
  // Skills and tasks answer different questions about the same jobs — what a role needs
  // to know against what it spends its week doing. Drawing both triples the edges for a
  // picture nobody reads either half of, so it is one at a time.
  const [side, setSide] = useState<"skill" | "task">("task");
  const [filters, setFilters] = useState<GraphFilters | null>(null);
  const [jobFilter, setJobFilter] = useState<number[]>([]);
  const [otherFilter, setOtherFilter] = useState<number[]>([]);
  // How far a selection lights up. 1 is "what does this touch"; 3 starts to answer
  // "what is in this neighbourhood", which on a dense cut is most of it.
  const [degrees, setDegrees] = useState(1);
  // Bumped when step 3 finishes, which rebuilds the fact table server-side. Without
  // it the graph keeps showing the pre-assessment cut until the page is reloaded.
  const [graphEpoch, setGraphEpoch] = useState(0);

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.status());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [api]);

  // Bumping the epoch on every job completion is what makes the graph actually update.
  // `refresh` only re-reads status, and `graph_built` does not change when the fact
  // table is rebuilt — so a rebuild used to leave the old cut on screen, which after an
  // opportunity run meant the colours silently lagged the data behind them.
  const { state: job, attach } = useJobStream(() => {
    void refresh();
    setGraphEpoch((n) => n + 1);
  });

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // One fetch per (resolution, expansion) — the roll-up is server-side, so changing
  // either is a request rather than a re-layout of everything in the browser.
  useEffect(() => {
    if (!status?.graph_built) return;
    let live = true;
    api
      .graph({
        jobs: level,
        skills: level,
        tasks: level,
        expand: expanded,
        show: ["job", side],
        jobFilter,
        skillFilter: side === "skill" ? otherFilter : [],
        taskFilter: side === "task" ? otherFilter : [],
        filterLevel: "family",
      })
      .then((c) => live && setCut(c))
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [api, status?.graph_built, level, expanded, graphEpoch, side, jobFilter, otherFilter]);

  // Filter options come from the fact table, so they only exist once it is built.
  useEffect(() => {
    if (!status?.graph_built) return;
    let live = true;
    api
      .graphFilters()
      .then((f) => live && setFilters(f))
      .catch(() => live && setFilters(null));
    return () => {
      live = false;
    };
  }, [api, status?.graph_built, graphEpoch]);

  const build = async () => {
    setError(null);
    try {
      const h = await api.buildGraph();
      attach(h.job_id, h.stage);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  // The ramp covers what this cut actually contains, not the theoretical 0-80 — see
  // opportunitySpan. Recomputed per cut so zooming does not silently change what a
  // colour means without the legend following.
  const span = useMemo<[number, number]>(
    () =>
      opportunitySpan(
        (cut?.nodes ?? [])
          .filter((n) => n.entity !== "action" && n.automation !== null)
          .map((n) => n.automation as number),
      ),
    [cut],
  );

  const onExpand = useCallback((n: GraphNode) => {
    setExpanded((prev) => (prev.includes(n.id) ? prev.filter((x) => x !== n.id) : [...prev, n.id]));
  }, []);

  const onOpen = useCallback(
    (n: GraphNode) => {
      void api
        .node(n.id)
        .then(setDetail)
        .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    },
    [api],
  );

  return (
    <div className="mx-auto flex max-w-[1600px] gap-6 px-6 py-6">
      <aside className="sticky top-[76px] hidden h-fit w-56 shrink-0 lg:block">
        {/* The way back. Without this the toggle exists only on the Job Architecture Studio nav, so
            entering Work Architecture Studio was a one-way door — you could reach it and
            had no route back to the architecture that feeds it. */}
        <div className="mb-3 space-y-1.5">
          <StudioToggle gates={gates} />
          {/* Reachable from both halves: a demo that needs resetting is usually one that
              just cascaded a Work Architecture step, and walking back to find it is the
              last thing anyone wants to do mid-meeting. */}
          <DemoReset />
        </div>
        <p className="mb-3 text-[11px] font-extrabold uppercase tracking-wider text-text-muted">
          Steps
        </p>
        <nav className="space-y-0.5">
          {STEPS.map((s, i) => (
            <button
              key={s.id}
              onClick={() => {
                setOpen(s.id);
                reveal(s.id);
              }}
              className={`flex w-full items-center gap-2 rounded-[7px] px-2.5 py-1.5 text-left text-[12px] transition-colors ${
                open === s.id ? "bg-accent-bg font-bold text-accent" : "text-text-secondary hover:bg-panel"
              }`}
            >
              <span className="text-text-muted">{i + 1}.</span>
              <span className="min-w-0 truncate">{s.title}</span>
            </button>
          ))}
        </nav>
      </aside>

      <main className="min-w-0 flex-1 space-y-4">
        {/* The sidebar is hidden below `lg`, so the way back needs to exist here too —
            otherwise the one-way door reappears on a narrow window. */}
        <div className="lg:hidden">
          <StudioToggle gates={gates} />
        </div>

        {error && (
          <p className="rounded-[10px] border border-brand-border bg-brand-bg px-4 py-3 text-[12.5px] text-brand">
            {error}
          </p>
        )}

        {STEPS.map((s, i) => {
          const isArchitecture = s.id === "architecture";
          const isOpportunity = s.id === "opportunity";
          const isProductivity = s.id === "productivity";
          const isAgents = s.id === "agents";
          const isProcesses = s.id === "processes";
          const isProcessOpportunity = s.id === "process-opportunity";
          const isFutureRoles = s.id === "future-roles";
          // Every step now has an implementation, so nothing is "built in a later phase" —
          // each one gates on the asset it actually reads instead.
          // Steps 5 and 6 read step 3's scores, so they unlock on the first assessed
          // cluster rather than on the whole taxonomy being done — the ranking is
          // useful, and honest about its coverage, long before it is complete.
          const assessed = (status?.clusters_assessed ?? 0) > 0;
          return (
            <StageSection
              key={s.id}
              id={s.id}
              index={i + 1}
              title={s.title}
              description={s.description}
              state={
                isProcesses
                  ? status?.ready
                    ? "active"
                    : "locked"
                  : isOpportunity
                    ? status?.ready
                      ? "active"
                      : "locked"
                    : isProductivity || isAgents || isFutureRoles || isProcessOpportunity
                      ? assessed
                        ? "active"
                        : "locked"
                      : status?.graph_built
                        ? "complete"
                        : "active"
              }
              lockedReason={
                (isOpportunity || isProcesses) && !status?.ready
                  ? "Needs the completed job architecture."
                  : isProductivity && !assessed
                    ? "Needs the AI opportunity assessment — it ranks tasks by augmentation."
                    : isAgents && !assessed
                      ? "Needs the AI opportunity assessment — it ranks clusters by automation."
                      : isFutureRoles && !assessed
                        ? "Needs the AI opportunity assessment — it decides what changes shape."
                        : isProcessOpportunity && !assessed
                          ? "Needs the AI opportunity assessment, and at least one mapped process."
                          : undefined
              }
              summary={
                isArchitecture && cut
                  ? `${cut.totals.leaves.job} anchor roles · ${cut.totals.leaves.skill} skill clusters · ${cut.totals.leaves.task} task clusters`
                  : isProductivity && status?.skills_written
                    ? `${status.skills_written} skills written`
                    : undefined
              }
              expanded={open === s.id}
              onToggle={() => {
                hold(s.id);
                setOpen(open === s.id ? "" : s.id);
              }}
            >
              {isOpportunity && status?.ready && (
                <OpportunityStage
                  api={api}
                  onError={setError}
                  onAssessed={() => {
                    setGraphEpoch((n) => n + 1);
                    // Once there is something to see, colour by it — the point of
                    // putting the scores on the graph is that they are visible
                    // without having to go looking for a control.
                    setColorMode("opportunity");
                  }}
                />
              )}

              {isProductivity && assessed && (
                <ProductivityStage api={api} onError={setError} />
              )}

              {isAgents && assessed && <AgentsStage api={api} onError={setError} />}

              {/* Steps 2 and 4 are the same screen: the assessment is per process, and
                  putting it elsewhere would mean navigating away from the thing being
                  assessed. Step 4's section points at it rather than duplicating it. */}
              {isProcesses && status?.ready && (
                <ProcessStage api={api} onError={setError} hasOpportunity={assessed} />
              )}

              {isProcessOpportunity && assessed && (
                <p className="rounded-[10px] border border-border bg-panel px-4 py-3 text-[12px] leading-snug text-text-secondary">
                  The as-is/to-be assessment runs per process and appears against each one
                  in <strong className="text-text">step 2</strong>, where the steps it is
                  reasoning about are visible. Upload a process there and press{" "}
                  <strong className="text-text">Assess as-is / to-be</strong>.
                </p>
              )}

              {isFutureRoles && assessed && <FutureRolesStage api={api} onError={setError} />}
              {isArchitecture && (
                <div className="space-y-3">
                  {!status?.ready && status && (
                    <div className="rounded-[10px] border border-warning-border bg-warning-bg px-4 py-3">
                      <p className="text-[12.5px] font-semibold text-text">
                        The job architecture is not complete yet
                      </p>
                      <ul className="mt-1 space-y-0.5">
                        {status.checks.map((c) => (
                          <li key={c.name} className="text-[11.5px] text-text-secondary">
                            {c.ok ? "✓" : "○"} {c.name}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {status?.ready && (
                    <div className="flex flex-wrap items-center gap-3">
                      {/* The fact table is derived from project state, so anything that
                          changes state — a new assessment, a re-clustering, generated
                          agents — leaves it behind until it is recomputed. Named for
                          what it is for rather than "rebuild", which reads like a
                          repair. */}
                      <Button variant={status.graph_built ? "default" : "primary"} onClick={build} disabled={job.running}>
                        <span className="flex items-center gap-1.5">
                          <RefreshCw size={12} />
                          {status.graph_built
                            ? "Update the graph from the latest data"
                            : "Build the work architecture"}
                        </span>
                      </Button>
                      <JobPulse job={job} />
                      {cut && !cut.has_headcount && (
                        <span className="text-[11.5px] text-text-muted">
                          No headcount in the source data — nodes are sized by counts, not people.
                        </span>
                      )}
                    </div>
                  )}

                  {job.stage && <ProgressBar job={job} />}

                  {cut && (
                    <>
                      <div className="flex flex-wrap items-center gap-3 rounded-[10px] border border-border bg-panel px-4 py-2.5">
                        <span className="text-[11px] font-extrabold uppercase tracking-wider text-text-muted">
                          Resolution
                        </span>
                        <span className="flex gap-1">
                          {(["family", "category", "profile"] as GraphLevel[]).map((l) => (
                            <button
                              key={l}
                              onClick={() => {
                                setLevel(l);
                                setExpanded([]);
                              }}
                              className={`rounded-[6px] border px-2 py-0.5 text-[11.5px] font-semibold transition-colors ${
                                level === l
                                  ? "border-accent bg-accent-bg text-accent"
                                  : "border-border bg-card text-text-secondary hover:border-accent"
                              }`}
                            >
                              {LEVEL_LABEL[l]}
                            </button>
                          ))}
                        </span>
                        <span className="text-[11px] font-extrabold uppercase tracking-wider text-text-muted">
                          Against
                        </span>
                        <span className="flex gap-1">
                          {(["skill", "task"] as const).map((s) => (
                            <button
                              key={s}
                              onClick={() => {
                                setSide(s);
                                setOtherFilter([]);
                                setExpanded([]);
                              }}
                              className={`rounded-[6px] border px-2 py-0.5 text-[11.5px] font-semibold transition-colors ${
                                side === s
                                  ? "border-accent bg-accent-bg text-accent"
                                  : "border-border bg-card text-text-secondary hover:border-accent"
                              }`}
                            >
                              {s === "skill" ? "Skills" : "Tasks"}
                            </button>
                          ))}
                        </span>
                        <span className="text-[11.5px] text-text-secondary">
                          {cut.totals.nodes} nodes · {cut.totals.edges} links
                        </span>
                        <span className="text-[11px] font-extrabold uppercase tracking-wider text-text-muted">
                          Hops
                        </span>
                        <span className="flex gap-1">
                          {[1, 2, 3].map((d) => (
                            <button
                              key={d}
                              onClick={() => setDegrees(d)}
                              title={`Light up nodes within ${d} link${d === 1 ? "" : "s"} of the one you click`}
                              className={`rounded-[6px] border px-2 py-0.5 text-[11.5px] font-semibold transition-colors ${
                                degrees === d
                                  ? "border-accent bg-accent-bg text-accent"
                                  : "border-border bg-card text-text-secondary hover:border-accent"
                              }`}
                            >
                              {d}
                            </button>
                          ))}
                        </span>
                        {expanded.length > 0 && (
                          <button
                            onClick={() => setExpanded([])}
                            className="text-[11.5px] font-semibold text-accent hover:underline"
                          >
                            collapse {expanded.length} expanded
                          </button>
                        )}
                        <span className="flex-1" />
                        {cut.has_opportunity && (
                          <span className="flex gap-1">
                            {(["entity", "opportunity"] as ColorMode[]).map((m) => (
                              <button
                                key={m}
                                onClick={() => setColorMode(m)}
                                className={`rounded-[6px] border px-2 py-0.5 text-[11.5px] font-semibold transition-colors ${
                                  colorMode === m
                                    ? "border-accent bg-accent-bg text-accent"
                                    : "border-border bg-card text-text-secondary hover:border-accent"
                                }`}
                              >
                                {m === "entity" ? "Colour by type" : "Colour by AI opportunity"}
                              </button>
                            ))}
                          </span>
                        )}
                        {colorMode === "opportunity" && cut.has_opportunity ? (
                          <OpportunityLegend span={span} />
                        ) : (
                          <Legend
                            hasActions={cut.totals.actions > 0}
                            hasProcesses={cut.totals.processes > 0}
                            hasUnmapped={cut.totals.unmapped_steps > 0}
                          />
                        )}
                      </div>

                      {filters && (
                        <div className="flex flex-wrap items-start gap-2 rounded-[10px] border border-border bg-panel px-3 py-2">
                          <FilterGroup
                            title={filters.job?.level_titles.family ?? "Job family"}
                            options={filters.job?.family ?? []}
                            selected={jobFilter}
                            onChange={(v) => {
                              setJobFilter(v);
                              setExpanded([]);
                            }}
                          />
                          <FilterGroup
                            title={filters[side]?.level_titles.family ?? "Family"}
                            options={filters[side]?.family ?? []}
                            selected={otherFilter}
                            onChange={(v) => {
                              setOtherFilter(v);
                              setExpanded([]);
                            }}
                          />
                        </div>
                      )}

                      {cut.totals.nodes > LEGIBLE_NODES && (
                        <p className="rounded-[10px] border border-warning-border bg-warning-bg px-4 py-2.5 text-[11.5px] leading-snug text-text-secondary">
                          <strong className="text-text">
                            {cut.totals.nodes.toLocaleString()} nodes is past what a
                            force-directed layout can show legibly
                          </strong>{" "}
                          — it will settle into a dense mass rather than a readable
                          picture, and takes a while doing it. For a project this size the
                          usable path is a coarser resolution with individual branches
                          opened, which keeps the view under a few hundred nodes.
                        </p>
                      )}

                      <ArchitectureGraph
                        cut={cut}
                        onOpen={onOpen}
                        onExpand={onExpand}
                        paletteKey={paletteKey}
                        colorMode={colorMode}
                        opportunitySpan={span}
                        degrees={degrees}
                      />
                      <p className="text-[11px] text-text-muted">
                        Hover a node for its detail; click to pin the card and light up
                        everything within the chosen number of hops. Drag to rearrange, scroll to
                        zoom, click the background to clear. Node size is the metric named in its
                        card; link thickness is the strength of the relationship.
                        {cut.has_opportunity &&
                          " Opening a task cluster at the finest resolution shows the actions inside it."}
                        {cut.totals.processes > 0 &&
                          (cut.totals.processes === 1
                            ? " One uploaded process sits on the right, linked to the task clusters its steps map to"
                            : ` ${cut.totals.processes} uploaded processes sit on the right, linked to the task clusters their steps map to`) +
                            (cut.totals.unmapped_steps > 0
                              ? `, with ${cut.totals.unmapped_steps} step${
                                  cut.totals.unmapped_steps === 1 ? "" : "s"
                                } that matched no task cluster.`
                              : ".")}
                      </p>
                    </>
                  )}
                </div>
              )}
            </StageSection>
          );
        })}

        {/* The forward door. Job Architecture has one at the foot of its wizard; without the
            same here, Work Design was only reachable from the sidebar toggle, which is hidden
            below lg and easy to miss once you have scrolled seven steps down. */}
        {gates["work-design"] && (
          <ProceedToWorkDesign
            ready={gates["work-design"].ready}
            missing={gates["work-design"].missing}
          />
        )}
      </main>

      {detail && <NodeModal detail={detail} onClose={() => setDetail(null)} />}
    </div>
  );
}

/**
 * Multi-select filter as a dropdown of checkboxes.
 *
 * A thin adapter over the shared `CheckboxDropdown` — Work Design Studio needs the same
 * control, so the behaviour lives in one place rather than being written twice.
 *
 * This was a row of toggle chips, on the reasoning that the counts have to be visible: a
 * filter that does not say how much of the graph each option covers makes choosing one
 * guesswork, and a native multi-select hides them behind a scroll and a modifier key. That
 * reasoning was right and still holds — the dropdown keeps the count on every row. What it
 * drops is only the permanent cost of laying every option out above the graph.
 *
 * Nothing selected means everything, which is the same convention as the rest of the app.
 */
function FilterGroup({
  title,
  options,
  selected,
  onChange,
}: {
  title: string;
  options: { id: number; name: string; leaves: number }[];
  selected: number[];
  onChange: (next: number[]) => void;
}) {
  return (
    <CheckboxDropdown
      className="w-[200px]"
      label={title}
      options={options.map((o) => ({ value: o.id, label: o.name, count: o.leaves }))}
      selected={selected}
      onChange={onChange}
    />
  );
}

function Legend({
  hasActions = false,
  hasProcesses = false,
  hasUnmapped = false,
}: {
  hasActions?: boolean;
  hasProcesses?: boolean;
  hasUnmapped?: boolean;
}) {
  const items = [
    ["job", "Jobs", "bg-accent"],
    ["skill", "Skills", "bg-teal"],
    ["task", "Tasks", "bg-purple"],
    ...(hasActions ? ([["action", "Actions", "bg-orange"]] as const) : []),
    ...(hasProcesses ? ([["process", "Processes", "bg-brand"]] as const) : []),
    ...(hasUnmapped ? ([["unmapped", "No matching task", "bg-warning"]] as const) : []),
  ] as const;
  return (
    <span className="flex items-center gap-3">
      {items.map(([id, label, cls]) => (
        <span key={id} className="flex items-center gap-1.5 text-[11px] text-text-secondary">
          <span className={`h-2.5 w-2.5 rounded-full ${cls}`} />
          {label}
        </span>
      ))}
    </span>
  );
}

/**
 * The graph's ramp is stretched to what this cut contains, so the endpoints must be
 * labelled with the real numbers — otherwise a hot node reads as "fully absorbable"
 * when it might mean 38%.
 */
function OpportunityLegend({ span }: { span: [number, number] }) {
  return (
    <span className="flex items-center gap-1.5 text-[10.5px] text-text-muted">
      {span[0]}%
      <span className="h-2.5 w-20 rounded-full" style={{ background: HEAT_GRADIENT }} />
      {span[1]}% automatable
      <span className="ml-1.5 flex items-center gap-1">
        <span className="h-2.5 w-2.5 rounded-full bg-text-muted" /> not assessed
      </span>
    </span>
  );
}

/** "Job category" -> "Job categories", not "Job categorys". */
function plural(word: string): string {
  return word.endsWith("y") ? `${word.slice(0, -1)}ies` : `${word}s`;
}

function NodeModal({ detail, onClose }: { detail: NodeDetail; onClose: () => void }) {
  return (
    <Modal
      title={detail.name}
      subtitle={`${detail.level_title} · ${detail.metric} ${detail.metric_title}${
        detail.leaves > 1 ? ` · ${detail.leaves} beneath` : ""
      }`}
      onClose={onClose}
    >
      <div className="space-y-4">
        {detail.definition && (
          <p className="text-[12px] leading-snug text-text-secondary">{detail.definition}</p>
        )}

        {detail.parent && (
          <p className="text-[11.5px] text-text-muted">
            An action within{" "}
            <strong className="font-semibold text-text">{detail.parent.name}</strong>.
          </p>
        )}

        {detail.opportunity && (
          <div className="rounded-[10px] border border-border bg-panel px-3.5 py-2.5">
            <p className="mb-1.5 text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
              AI opportunity
            </p>
            <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
              <span className="flex items-baseline gap-1.5">
                <span
                  className="rounded-[5px] px-1.5 py-0.5 text-[12px] font-bold tabular-nums text-white"
                  style={{ background: opportunityColor(detail.opportunity.automation) }}
                >
                  {detail.opportunity.automation}%
                </span>
                <span className="text-[11px] text-text-secondary">automatable</span>
              </span>
              <span className="flex items-baseline gap-1.5">
                <span
                  className="rounded-[5px] px-1.5 py-0.5 text-[12px] font-bold tabular-nums text-white"
                  style={{ background: opportunityColor(detail.opportunity.augmentation) }}
                >
                  {detail.opportunity.augmentation}%
                </span>
                <span className="text-[11px] text-text-secondary">augmentable</span>
              </span>
            </div>
            {detail.opportunity.coverage < 99 && (
              <p className="mt-1.5 text-[11px] text-text-muted">
                Weighted over the {detail.opportunity.coverage}% of this node that has been
                assessed. The rest is unknown, not zero.
              </p>
            )}
            <p className="mt-1.5 text-[11px] text-text-muted">
              A model estimate, calibrated by prompt and validated for range — not a measurement.
            </p>
          </div>
        )}

        {detail.actions.length > 0 && (
          <div>
            <p className="mb-1 text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
              {detail.parent ? "Actions in this cluster" : "Actions"} ({detail.actions.length})
            </p>
            <div className="space-y-1.5">
              {detail.actions.map((a, i) => (
                <div
                  key={`${a.name}-${i}`}
                  className={`rounded-[7px] border px-2.5 py-1.5 ${
                    a.current ? "border-accent bg-accent-bg" : "border-border bg-panel"
                  }`}
                >
                  <div className="flex items-baseline gap-2">
                    <span className="min-w-0 flex-1 text-[11.5px] font-semibold text-text">
                      {a.name}
                    </span>
                    <span className="shrink-0 text-[10.5px] tabular-nums text-text-muted">
                      {a.pct_of_task.toFixed(0)}% of task
                    </span>
                    <span
                      className="shrink-0 rounded-[4px] px-1 py-0.5 text-[10px] font-bold tabular-nums text-white"
                      style={{ background: opportunityColor(a.automation) }}
                    >
                      {a.automation}%
                    </span>
                  </div>
                  <p className="mt-0.5 text-[10.5px] leading-snug text-text-secondary">
                    {a.definition}
                  </p>
                  {!detail.parent && detail.level !== "profile" && (
                    <p className="mt-0.5 text-[10px] text-text-muted">{a.cluster}</p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {detail.children.length > 0 && (
          <div>
            <p className="mb-1 text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
              {plural(detail.children_title ?? "child")} ({detail.children.length})
            </p>
            <ul className="space-y-0.5">
              {detail.children.map((c) => (
                <li key={c.id} className="flex items-baseline gap-2 text-[12px]">
                  <span className="min-w-0 flex-1 truncate text-text">{c.name}</span>
                  <span className="shrink-0 tabular-nums text-text-muted">{c.metric}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {Object.entries(detail.related).map(([rel, items]) =>
          items.length ? (
            <div key={rel}>
              <p className="mb-1 text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
                Strongest {rel} links
              </p>
              <ul className="space-y-0.5">
                {items.map((x) => (
                  <li key={x.name} className="flex items-baseline gap-2 text-[12px]">
                    <span className="min-w-0 flex-1 truncate text-text">{x.name}</span>
                    <span className="shrink-0 tabular-nums text-text-muted">{x.weight}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null,
        )}

        <p className="border-t border-border pt-2 text-[11px] text-text-muted">
          Later steps add generated skills, agents and the future role design to this panel.
        </p>
      </div>
    </Modal>
  );
}
