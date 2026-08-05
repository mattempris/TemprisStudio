import { useCallback, useEffect, useMemo, useState } from "react";
import { workforceApi, studioGates } from "../services/workforceApi";
import type { WorkforceStatus } from "../types/workforce";
import { StudioToggle } from "../components/wizard/StudioToggle";
import { DemoReset } from "../components/wizard/DemoReset";

/**
 * Work Design Studio — compose new job definitions out of the work architecture.
 *
 * A workbench, not a wizard: no StageSection accordion, because the whole point is that the
 * work pool, the job being designed and the levers are visible at the same time. The other
 * two studios are accordions because their steps are sequential and one-open-at-a-time keeps
 * a very tall page navigable; here, hiding one panel to see another would defeat the screen.
 *
 * Five panels in two rows. Top: the unreviewed work pool, the job being designed, and the
 * automation/augmentation levers. Bottom, where results land: the accumulated target task
 * profile, and the list of job definitions.
 *
 * The invariant the whole studio rests on, and the one to assert first when anything looks
 * wrong:
 *
 *     as_is_total == absorbed_by_agents + saved_by_augmentations
 *                    + allocated_to_jobs + unreviewed_remaining
 *
 * Currently a shell: the chrome, the gate and the panel frames. Each panel is filled by a
 * later phase, in the order a user meets them.
 */
const ANCHORS = [
  { id: "wd-pool", title: "Unreviewed work" },
  { id: "wd-design", title: "Job design" },
  { id: "wd-target", title: "Target profile" },
  { id: "wd-jobs", title: "Job definitions" },
] as const;

export function WorkDesignPage({
  clientSlug,
  projectSlug,
}: {
  clientSlug: string;
  projectSlug: string;
}) {
  // Memoised for the reason WorkforcePage spells out: an unmemoised client is a new object
  // every render, and anything depending on it becomes a render loop.
  const api = useMemo(() => workforceApi(clientSlug, projectSlug), [clientSlug, projectSlug]);
  const [status, setStatus] = useState<WorkforceStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await api.status());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  const gates = useMemo(() => (status ? studioGates(status) : {}), [status]);
  const gate = gates["work-design"];

  return (
    <div className="mx-auto flex max-w-[1600px] gap-6 px-6 py-6">
      <aside className="sticky top-[76px] hidden h-fit w-56 shrink-0 lg:block">
        <div className="mb-3 space-y-1.5">
          <StudioToggle gates={gates} />
          <DemoReset />
        </div>
        <p className="mb-3 text-[11px] font-extrabold uppercase tracking-wider text-text-muted">
          Steps
        </p>
        <nav className="space-y-0.5">
          {ANCHORS.map((a, i) => (
            <a
              key={a.id}
              href={`#${a.id}`}
              className="flex w-full items-center gap-2 rounded-[7px] px-2.5 py-1.5 text-left text-[12px] text-text-secondary transition-colors hover:bg-panel"
            >
              <span className="text-text-muted">{i + 1}.</span>
              <span className="min-w-0 truncate">{a.title}</span>
            </a>
          ))}
        </nav>
      </aside>

      <main className="min-w-0 flex-1 space-y-4">
        {/* The sidebar is hidden below lg, so the way between studios needs to exist here. */}
        <div className="lg:hidden">
          <StudioToggle gates={gates} />
        </div>

        {error && (
          <p className="rounded-[10px] border border-brand-border bg-brand-bg px-4 py-3 text-[12.5px] text-brand">
            {error}
          </p>
        )}

        {status && gate && !gate.ready && (
          <div className="rounded-[var(--radius-modal)] border border-border bg-card px-6 py-5 shadow-modal">
            <p className="text-[15px] font-bold text-text">Work Design Studio is not ready</p>
            <p className="mt-1 text-[13px] leading-relaxed text-text-secondary">
              This studio reads the task taxonomy and applies AI levers to it, so it needs at
              least one agent or augmentation to apply. Still needed:{" "}
              {gate.missing.join(", ")}.
            </p>
            <ul className="mt-3 space-y-1">
              {status.checks.map((c) => (
                <li key={c.name} className="flex items-center gap-2 text-[12.5px]">
                  <span className={c.ok ? "text-success" : "text-text-muted"}>
                    {c.ok ? "✓" : "○"}
                  </span>
                  <span className={c.ok ? "text-text-secondary" : "text-text-muted"}>
                    {c.name}
                  </span>
                </li>
              ))}
              <li className="flex items-center gap-2 text-[12.5px]">
                <span
                  className={
                    (status.agents_defined ?? 0) + (status.skills_written ?? 0) > 0
                      ? "text-success"
                      : "text-text-muted"
                  }
                >
                  {(status.agents_defined ?? 0) + (status.skills_written ?? 0) > 0 ? "✓" : "○"}
                </span>
                <span className="text-text-secondary">
                  An agent or augmentation to apply — {status.agents_defined ?? 0} agents,{" "}
                  {status.skills_written ?? 0} augmentations
                </span>
              </li>
            </ul>
          </div>
        )}

        {status && gate?.ready && (
          <>
            <Panel id="wd-pool" title="Coming next">
              The panels below are built in order over the following phases. The assessment
              re-calibration and the agent oversight tasks land first, because every number
              this studio shows depends on them.
            </Panel>
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1.3fr)_320px]">
              <Panel id="wd-pool-2" title="Unreviewed work">
                The filtered work stack, as a treemap named from the task taxonomy. Drains as
                levers absorb work and as work is allocated to job definitions.
              </Panel>
              <Panel id="wd-design" title="Job design">
                One job definition. Headcount raises its capacity, not its size.
              </Panel>
              <Panel id="wd-levers" title="Automation &amp; augmentation">
                Agents absorb work. Augmentations keep the person and make them faster.
              </Panel>
            </div>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Panel id="wd-target" title="Target task profile">
                The accumulated to-be work across every designed job.
              </Panel>
              <Panel id="wd-jobs" title="Job definitions">
                Designed jobs, each exportable as a job profile document, and all together as
                XLSX.
              </Panel>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

function Panel({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section
      id={id}
      className="scroll-mt-24 rounded-[var(--radius-modal)] border border-dashed border-border bg-card px-5 py-4 shadow-modal"
    >
      <p className="text-[13px] font-bold text-text">{title}</p>
      <p className="mt-1 text-[12px] leading-relaxed text-text-muted">{children}</p>
    </section>
  );
}
