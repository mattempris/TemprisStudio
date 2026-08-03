import { useCallback, useEffect, useMemo, useState } from "react";
import { Play } from "lucide-react";
import { ArchitectureGraph } from "../components/workforce/ArchitectureGraph";
import { ProgressBar } from "../components/wizard/ProgressBar";
import { JobPulse } from "../components/wizard/JobPulse";
import { StageSection } from "../components/wizard/StageSection";
import { Button } from "../components/ui/Button";
import { Modal } from "../components/ui/Modal";
import { useJobStream } from "../hooks/useJobStream";
import { workforceApi } from "../services/workforceApi";
import type { GraphCut, GraphLevel, GraphNode, NodeDetail, WorkforceStatus } from "../types/workforce";

/**
 * Workforce Studio.
 *
 * Same shape as the JAStudio wizard — sticky step list, scrolling accordion of
 * stages — reusing its components rather than reimplementing them. Step 1 is built;
 * steps 2-7 are declared and locked, so the shape of the whole thing is visible from
 * the start rather than appearing a step at a time.
 */

const STEPS = [
  {
    id: "architecture",
    title: "Workforce architecture",
    description:
      "Every job profile, skill cluster and task cluster, and how they connect. Later steps add processes, agents and actions to the same graph.",
  },
  { id: "processes", title: "Process upload", description: "Upload process documents and map their steps onto the task structure. Optional." },
  { id: "opportunity", title: "AI opportunity assessment", description: "Break each task cluster into actions and score each one for automation and augmentation." },
  { id: "process-opportunity", title: "Process opportunity assessment", description: "Current and future state per uploaded process." },
  { id: "productivity", title: "Personal productivity", description: "Per role, the tasks a prompt helps most — with a downloadable Claude skill for each." },
  { id: "agents", title: "Agent definitions", description: "Per task cluster, a full agent specification, ranked by the time it would release." },
  { id: "future-roles", title: "Future role design", description: "How a role is redesigned once agents absorb the automatable work." },
] as const;

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
  const [cut, setCut] = useState<GraphCut | null>(null);
  const [level, setLevel] = useState<GraphLevel>("family");
  const [expanded, setExpanded] = useState<string[]>([]);
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string>("architecture");

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.status());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [api]);

  const { state: job, attach } = useJobStream(() => void refresh());

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // One fetch per (resolution, expansion) — the roll-up is server-side, so changing
  // either is a request rather than a re-layout of everything in the browser.
  useEffect(() => {
    if (!status?.graph_built) return;
    let live = true;
    api
      .graph({ jobs: level, skills: level, tasks: level, expand: expanded })
      .then((c) => live && setCut(c))
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [api, status?.graph_built, level, expanded]);

  const build = async () => {
    setError(null);
    try {
      const h = await api.buildGraph();
      attach(h.job_id, h.stage);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

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
        <p className="mb-3 text-[11px] font-extrabold uppercase tracking-wider text-text-muted">
          Workforce
        </p>
        <nav className="space-y-0.5">
          {STEPS.map((s, i) => (
            <button
              key={s.id}
              onClick={() => setOpen(s.id)}
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
        {error && (
          <p className="rounded-[10px] border border-brand-border bg-brand-bg px-4 py-3 text-[12.5px] text-brand">
            {error}
          </p>
        )}

        {STEPS.map((s, i) => {
          const isArchitecture = s.id === "architecture";
          const locked = !isArchitecture;
          return (
            <StageSection
              key={s.id}
              id={s.id}
              index={i + 1}
              title={s.title}
              description={s.description}
              state={
                locked
                  ? "locked"
                  : status?.graph_built
                    ? "complete"
                    : "active"
              }
              lockedReason={locked ? "Built in a later phase." : undefined}
              summary={
                isArchitecture && cut
                  ? `${cut.totals.leaves.job} job profiles · ${cut.totals.leaves.skill} skill clusters · ${cut.totals.leaves.task} task clusters`
                  : undefined
              }
              expanded={open === s.id}
              onToggle={() => setOpen(open === s.id ? "" : s.id)}
            >
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
                      <Button variant={status.graph_built ? "default" : "primary"} onClick={build} disabled={job.running}>
                        <span className="flex items-center gap-1.5">
                          <Play size={12} />
                          {status.graph_built ? "Rebuild the architecture" : "Build the work architecture"}
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
                        <span className="text-[11.5px] text-text-secondary">
                          {cut.totals.nodes} nodes · {cut.totals.edges} links
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
                        <Legend />
                      </div>

                      <ArchitectureGraph
                        cut={cut}
                        onOpen={onOpen}
                        onExpand={onExpand}
                        paletteKey={paletteKey}
                      />
                      <p className="text-[11px] text-text-muted">
                        Click a group to open it, shift-click for detail. Drag to rearrange, scroll
                        to zoom. Node size is the metric named in its tooltip; link thickness is the
                        strength of the relationship.
                      </p>
                    </>
                  )}
                </div>
              )}
            </StageSection>
          );
        })}
      </main>

      {detail && <NodeModal detail={detail} onClose={() => setDetail(null)} />}
    </div>
  );
}

function Legend() {
  const items = [
    ["job", "Jobs", "bg-accent"],
    ["skill", "Skills", "bg-teal"],
    ["task", "Tasks", "bg-purple"],
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
          Later steps add AI opportunity, generated skills, agents and the future role design to
          this panel.
        </p>
      </div>
    </Modal>
  );
}
