import { useMemo, useState } from "react";
import { Search, UserCheck } from "lucide-react";
import { Button } from "../ui/Button";
import type { Levers, PoolResult } from "../../types/workDesign";

/**
 * Automation and augmentation — the two levers, treated the same way and behaving differently.
 *
 * An **agent** absorbs work: the actions it takes score above the absorption threshold, that
 * time leaves the human's week, and an oversight task is added back. An **augmentation** keeps
 * the person and makes them faster: the task shrinks and nothing is added. Those are different
 * claims about the same hours, which is why the ledger below reports them separately and never
 * as one "time saved".
 *
 * Augmentations are applied per role with "top X" rather than one checkbox each. A skill is
 * written for one (role, cluster) pair, so on a 565-role project there are far too many to tick
 * individually and each one only affects the hours it was measured against.
 *
 * "Update task profile" is a button, not live recompute, deliberately: the pool is the surface
 * being dragged from, and re-tiling it the instant a checkbox flips moves the cell the user was
 * reaching for.
 */
export function LeversPanel({
  levers,
  selectedAgents,
  onAgents,
  topAugmentations,
  onTopAugmentations,
  uplift,
  onUplift,
  dirty,
  onApply,
  applied,
  busy,
}: {
  levers: Levers;
  selectedAgents: Set<string>;
  onAgents: (next: Set<string>) => void;
  topAugmentations: number;
  onTopAugmentations: (n: number) => void;
  uplift: number;
  onUplift: (v: number) => void;
  dirty: boolean;
  onApply: () => void;
  applied: PoolResult | null;
  busy: boolean;
}) {
  const [tab, setTab] = useState<"agents" | "augmentations">("agents");
  const [query, setQuery] = useState("");

  const agents = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q
      ? levers.agents.filter(
          (a) => a.name.toLowerCase().includes(q) || a.cluster.toLowerCase().includes(q),
        )
      : levers.agents;
  }, [levers.agents, query]);

  // Grouped by role so "top X per role" is legible as a rule rather than a number.
  const byRole = useMemo(() => {
    const m = new Map<string, typeof levers.augmentations>();
    for (const s of levers.augmentations) {
      const list = m.get(s.role_title);
      if (list) list.push(s);
      else m.set(s.role_title, [s]);
    }
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [levers.augmentations]);

  const t = applied?.totals;

  return (
    <div className="rounded-[var(--radius-modal)] border border-border bg-card shadow-modal">
      <div className="border-b border-border px-4 py-3">
        <p className="text-[13px] font-bold text-text">Automation &amp; augmentation</p>
        <p className="mt-0.5 text-[11px] leading-snug text-text-muted">
          Agents absorb work and add oversight back. Augmentations keep the person and make them
          faster.
        </p>
      </div>

      <div className="flex border-b border-border">
        {(["agents", "augmentations"] as const).map((k) => (
          <button
            key={k}
            onClick={() => setTab(k)}
            className={`flex-1 px-3 py-2 text-[11.5px] font-semibold capitalize transition-colors ${
              tab === k
                ? "border-b-2 border-accent text-accent"
                : "text-text-secondary hover:text-text"
            }`}
          >
            {k} ({k === "agents" ? levers.agents.length : levers.augmentations.length})
          </button>
        ))}
      </div>

      {tab === "agents" ? (
        <div>
          <div className="relative border-b border-border px-3 py-2">
            <Search className="absolute left-5 top-1/2 h-3 w-3 -translate-y-1/2 text-text-muted" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter agents…"
              className="w-full rounded-[7px] border border-border bg-card py-1 pl-7 pr-2 text-[12px] text-text outline-none focus:border-accent"
            />
          </div>
          <div className="max-h-72 overflow-y-auto">
            {agents.length === 0 && (
              <p className="px-3 py-5 text-center text-[11.5px] text-text-muted">
                {levers.agents.length === 0
                  ? "No agents specified yet — build them in Work Architecture Studio."
                  : "Nothing matches that filter."}
              </p>
            )}
            {agents.map((a) => (
              <label
                key={a.id}
                className="flex cursor-pointer items-start gap-2 border-b border-border/60 px-3 py-1.5 last:border-0 hover:bg-panel"
              >
                <input
                  type="checkbox"
                  checked={selectedAgents.has(a.id)}
                  onChange={(e) => {
                    const next = new Set(selectedAgents);
                    if (e.target.checked) next.add(a.id);
                    else next.delete(a.id);
                    onAgents(next);
                  }}
                  className="mt-0.5 h-3 w-3 accent-[var(--color-accent)]"
                />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1">
                    <span className="min-w-0 truncate text-[11.5px] font-semibold text-text">
                      {a.name}
                    </span>
                    {a.human_in_the_loop && (
                      <UserCheck size={10} className="shrink-0 text-text-muted" />
                    )}
                  </span>
                  <span className="block truncate text-[10.5px] text-text-muted">{a.cluster}</span>
                  <span className="block text-[10.5px] text-text-secondary">
                    {a.automation.toFixed(0)}% automatable · oversight{" "}
                    {(a.oversight_fraction * 100).toFixed(0)}%
                    {/* The difference between a number the model judged for this agent and one
                        the project assumed for all of them is worth showing, not hiding. */}
                    {a.oversight_source === "fallback" && (
                      <span className="ml-1 text-warning">(assumed)</span>
                    )}
                  </span>
                </span>
              </label>
            ))}
          </div>
        </div>
      ) : (
        <div>
          <div className="border-b border-border px-3 py-2.5">
            <label className="flex items-center justify-between text-[11.5px] font-semibold text-text">
              Apply the top
              <input
                type="number"
                min={0}
                max={20}
                value={topAugmentations}
                onChange={(e) => onTopAugmentations(Math.max(0, Number(e.target.value) || 0))}
                className="w-16 rounded-[6px] border border-border bg-card px-2 py-0.5 text-right text-[12px] tabular-nums text-text outline-none focus:border-accent"
              />
            </label>
            <p className="mt-1 text-[10.5px] leading-snug text-text-muted">
              per role, ranked by the time each frees. A skill is written for one role and one
              task cluster, so it only ever speeds up the hours it was measured against.
            </p>
          </div>
          <div className="border-b border-border px-3 py-2.5">
            <label className="flex items-baseline justify-between text-[11px] font-semibold text-text">
              Augmentation potential realised
              <span className="tabular-nums text-accent">{(uplift * 100).toFixed(0)}%</span>
            </label>
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={uplift * 100}
              onChange={(e) => onUplift(Number(e.target.value) / 100)}
              className="mt-1 w-full accent-[var(--color-accent)]"
            />
            {/* The one number the data does not contain. Labelled as an assumption and carried
                into the export, rather than quietly baked in. */}
            <p className="mt-0.5 text-[10.5px] leading-snug text-text-muted">
              An assumption: the score says where AI help applies, not how much time is saved.
            </p>
          </div>
          <div className="max-h-52 overflow-y-auto">
            {byRole.length === 0 && (
              <p className="px-3 py-5 text-center text-[11.5px] text-text-muted">
                No augmentations generated yet — create them in Work Architecture Studio.
              </p>
            )}
            {byRole.map(([role, list]) => (
              <div key={role} className="border-b border-border/60 px-3 py-1.5 last:border-0">
                <p className="truncate text-[11.5px] font-semibold text-text">{role}</p>
                <p className="truncate text-[10.5px] text-text-muted">
                  {Math.min(topAugmentations, list.length)} of {list.length} applied ·{" "}
                  {list
                    .slice(0, Math.max(1, topAugmentations))
                    .map((s) => s.name)
                    .join(", ")}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="border-t border-border px-3 py-2.5">
        <Button variant="primary" onClick={onApply} disabled={!dirty || busy} className="w-full">
          {busy ? "Applying…" : dirty ? "Update task profile" : "Task profile is up to date"}
        </Button>
        {t && (
          <div className="mt-2 space-y-0.5 text-[11px] tabular-nums">
            {/* Reported separately, never summed. One is work that has gone, the other is the
                same work done faster. */}
            <p className="flex justify-between text-text-secondary">
              <span>Absorbed by agents</span>
              <span className="font-semibold text-text">
                −{t.removed_by_automation_hours_per_week.toFixed(1)} h
              </span>
            </p>
            <p className="flex justify-between text-text-secondary">
              <span>Saved by augmentation</span>
              <span className="font-semibold text-text">
                −{t.freed_by_augmentation_hours_per_week.toFixed(1)} h
              </span>
            </p>
            <p className="flex justify-between text-text-secondary">
              <span>Oversight added back</span>
              <span className="font-semibold text-text">
                +{t.oversight_hours_per_week.toFixed(1)} h
              </span>
            </p>
            <p className="flex justify-between border-t border-border pt-0.5 font-semibold text-text">
              <span>Net</span>
              <span>
                {t.net_change_hours_per_week.toFixed(1)} h ({t.net_change_pct.toFixed(1)}%)
              </span>
            </p>
          </div>
        )}
        {applied?.skipped_agents && applied.skipped_agents.length > 0 && (
          <p className="mt-1.5 text-[10.5px] leading-snug text-warning">
            {applied.skipped_agents.length} selected agents target work outside this filter and
            were ignored.
          </p>
        )}
      </div>
    </div>
  );
}
