import { useMemo, useState } from "react";
import { AlertTriangle, ChevronRight, Plus, RotateCcw, Save, Trash2 } from "lucide-react";
import type { JEFramework } from "../../types/pipeline";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { cn } from "../../lib/cn";

/**
 * instructions.txt step 7: "User defines job profile template, Job Evaluation
 * Framework and level names / JE score mapping".
 *
 * The framework is 4 domains x 5 subfactors x 5 rubric descriptors — about a
 * hundred fields — so nothing is shown expanded. Weights are the part users
 * actually change and they are visible at every tier; rubric wording sits two
 * clicks down.
 *
 * Weights are validated live against the same rules the backend enforces
 * (domains sum to 100; each domain's subfactors sum to that domain's weight),
 * because discovering a 422 after editing thirty fields is miserable. The
 * running total per tier is always on screen rather than only in an error.
 */

interface Props {
  framework: JEFramework;
  onSave: (framework: JEFramework) => Promise<void>;
  onReset: () => Promise<JEFramework>;
  saving: boolean;
  /** True once evaluations exist — editing then invalidates them. */
  hasResults: boolean;
}

export function JEFrameworkEditor({ framework, onSave, onReset, saving, hasResults }: Props) {
  const [draft, setDraft] = useState<JEFramework>(framework);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [dirty, setDirty] = useState(false);
  const [serverProblems, setServerProblems] = useState<string[]>([]);

  const toggle = (k: string) => setOpen((o) => ({ ...o, [k]: !o[k] }));

  function edit(mutate: (d: JEFramework) => void) {
    setDraft((prev) => {
      const next = structuredClone(prev);
      mutate(next);
      return next;
    });
    setDirty(true);
    setServerProblems([]);
  }

  const domainTotal = draft.domains.reduce((a, d) => a + d.weight, 0);

  // Mirrors validate_framework() on the backend.
  const problems = useMemo(() => {
    const out: string[] = [];
    if (!draft.domains.length) out.push("Framework has no domains.");
    if (Math.abs(domainTotal - 100) > 0.01)
      out.push(`Domain weights sum to ${round(domainTotal)}, expected 100.`);
    for (const d of draft.domains) {
      if (!d.subdomains.length) {
        out.push(`"${d.name}" has no sub-factors.`);
        continue;
      }
      const sub = d.subdomains.reduce((a, s) => a + s.weight, 0);
      if (Math.abs(sub - d.weight) > 0.01)
        out.push(
          `"${d.name}": sub-factor weights sum to ${round(sub)}, expected ${round(d.weight)}.`,
        );
      for (const s of d.subdomains) {
        if (s.rubric.length !== 5)
          out.push(`"${d.name} / ${s.name}" has ${s.rubric.length} rubric descriptors, expected 5.`);
      }
    }
    const bands = [...draft.level_bands].sort((a, b) => a.min_score - b.min_score);
    for (let i = 0; i < bands.length; i++) {
      if (bands[i].max_score <= bands[i].min_score)
        out.push(`Level "${bands[i].name}" has max ≤ min.`);
      // Gaps and overlaps both matter: a score falling in a gap has no level at
      // all, and an overlap makes the assigned level depend on iteration order.
      if (i > 0 && Math.abs(bands[i].min_score - bands[i - 1].max_score) > 0.01)
        out.push(
          `Gap or overlap between "${bands[i - 1].name}" (ends ${round(bands[i - 1].max_score)}) ` +
            `and "${bands[i].name}" (starts ${round(bands[i].min_score)}).`,
        );
    }
    return out;
  }, [draft, domainTotal]);

  const allProblems = [...problems, ...serverProblems];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-[10px] bg-panel px-4 py-2.5">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
          <Stat value={draft.domains.length} label="domains" />
          <Stat
            value={draft.domains.reduce((a, d) => a + d.subdomains.length, 0)}
            label="sub-factors"
          />
          <Stat value={draft.level_bands.length} label="levels" />
          <span className="flex items-baseline gap-1.5">
            <span
              className={cn(
                "text-[15px] font-bold tabular-nums",
                Math.abs(domainTotal - 100) > 0.01 ? "text-brand" : "text-success",
              )}
            >
              {round(domainTotal)}
            </span>
            <span className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">
              / 100 weight
            </span>
          </span>
        </div>
        {dirty && <Badge color="warning">unsaved</Badge>}
      </div>

      {allProblems.length > 0 && (
        <div className="rounded-[10px] border border-warning-border bg-warning-bg px-3.5 py-2.5">
          <p className="mb-1 flex items-center gap-1.5 text-[12px] font-bold text-warning">
            <AlertTriangle size={13} /> Fix before saving
          </p>
          <ul className="space-y-0.5">
            {allProblems.map((p, i) => (
              <li key={i} className="text-[11.5px] text-text">
                {p}
              </li>
            ))}
          </ul>
        </div>
      )}

      {hasResults && dirty && (
        <div className="rounded-[10px] border border-brand-border bg-brand-bg px-3.5 py-2.5 text-[11.5px] text-text">
          Evaluations already exist under the current framework. Saving marks them stale — they keep
          their scores but must be re-run to reflect these changes.
        </div>
      )}

      {/* Domains → sub-factors → rubric */}
      <ul className="space-y-1.5">
        {draft.domains.map((domain, di) => {
          const dk = `d${di}`;
          const subTotal = domain.subdomains.reduce((a, s) => a + s.weight, 0);
          return (
            <li key={dk} className="overflow-hidden rounded-[10px] border border-border bg-card">
              <div className="flex items-center gap-2 px-3 py-2">
                <button onClick={() => toggle(dk)} className="shrink-0">
                  <ChevronRight
                    size={13}
                    className={cn("text-text-muted transition-transform", open[dk] && "rotate-90")}
                  />
                </button>
                <input
                  value={domain.name}
                  onChange={(e) => edit((d) => void (d.domains[di].name = e.target.value))}
                  className="min-w-0 flex-1 rounded-[6px] border border-transparent bg-transparent px-1.5 py-1 text-[13px] font-semibold text-text outline-none hover:border-border focus:border-accent"
                />
                <WeightInput
                  value={domain.weight}
                  onChange={(v) => edit((d) => void (d.domains[di].weight = v))}
                />
                <span
                  className={cn(
                    "w-24 shrink-0 text-right text-[10.5px] tabular-nums",
                    Math.abs(subTotal - domain.weight) > 0.01 ? "text-brand" : "text-text-muted",
                  )}
                >
                  subs {round(subTotal)}/{round(domain.weight)}
                </span>
              </div>

              {open[dk] &&
                domain.subdomains.map((sub, si) => {
                  const sk = `${dk}s${si}`;
                  return (
                    <div key={sk} className="border-t border-border bg-panel/40">
                      <div className="flex items-center gap-2 py-1.5 pl-9 pr-3">
                        <button onClick={() => toggle(sk)} className="shrink-0">
                          <ChevronRight
                            size={12}
                            className={cn(
                              "text-text-muted transition-transform",
                              open[sk] && "rotate-90",
                            )}
                          />
                        </button>
                        <input
                          value={sub.name}
                          onChange={(e) =>
                            edit((d) => void (d.domains[di].subdomains[si].name = e.target.value))
                          }
                          className="min-w-0 flex-1 rounded-[6px] border border-transparent bg-transparent px-1.5 py-0.5 text-[12.5px] text-text-secondary outline-none hover:border-border focus:border-accent"
                        />
                        <WeightInput
                          value={sub.weight}
                          onChange={(v) =>
                            edit((d) => void (d.domains[di].subdomains[si].weight = v))
                          }
                        />
                        <button
                          onClick={() =>
                            edit((d) => void d.domains[di].subdomains.splice(si, 1))
                          }
                          title="Remove sub-factor"
                          className="shrink-0 text-text-muted hover:text-brand"
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>

                      {open[sk] && (
                        <div className="space-y-1 border-t border-border bg-card px-4 py-2.5 pl-12">
                          <p className="text-[9.5px] font-extrabold uppercase tracking-wider text-text-muted">
                            Rubric — what each score from 1 to 5 means
                          </p>
                          {sub.rubric.map((text, ri) => (
                            <div key={ri} className="flex items-start gap-2">
                              <span className="mt-1 w-4 shrink-0 text-right text-[11px] font-bold tabular-nums text-accent">
                                {ri + 1}
                              </span>
                              <textarea
                                value={text}
                                rows={2}
                                onChange={(e) =>
                                  edit(
                                    (d) =>
                                      void (d.domains[di].subdomains[si].rubric[ri] =
                                        e.target.value),
                                  )
                                }
                                className="min-w-0 flex-1 resize-y rounded-[6px] border border-border bg-card px-2 py-1 text-[11.5px] leading-snug text-text outline-none focus:border-accent"
                              />
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}

              {open[dk] && (
                <div className="border-t border-border bg-panel/40 py-1.5 pl-9">
                  <button
                    onClick={() =>
                      edit((d) =>
                        void d.domains[di].subdomains.push({
                          name: "New sub-factor",
                          weight: 0,
                          rubric: ["", "", "", "", ""],
                        }),
                      )
                    }
                    className="flex items-center gap-1 text-[11px] font-bold text-accent hover:underline"
                  >
                    <Plus size={11} /> Add sub-factor
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {/* Level bands */}
      <div className="rounded-[10px] border border-border bg-card px-4 py-3">
        <p className="mb-2 text-[11px] font-extrabold uppercase tracking-wider text-text-muted">
          Level names and score mapping
        </p>
        <p className="mb-2.5 text-[11.5px] leading-snug text-text-secondary">
          A profile's weighted score (0-100) falls into one of these bands. Bands must be
          contiguous — a gap leaves some scores with no level.
        </p>
        <ul className="space-y-1">
          {draft.level_bands.map((band, bi) => (
            <li key={bi} className="flex items-center gap-2">
              <input
                value={band.name}
                onChange={(e) => edit((d) => void (d.level_bands[bi].name = e.target.value))}
                className="min-w-0 flex-1 rounded-[6px] border border-border bg-card px-2 py-1 text-[12px] text-text outline-none focus:border-accent"
              />
              <NumberInput
                value={band.min_score}
                onChange={(v) => edit((d) => void (d.level_bands[bi].min_score = v))}
              />
              <span className="text-[11px] text-text-muted">to</span>
              <NumberInput
                value={band.max_score}
                onChange={(v) => edit((d) => void (d.level_bands[bi].max_score = v))}
              />
              <button
                onClick={() => edit((d) => void d.level_bands.splice(bi, 1))}
                title="Remove level"
                className="shrink-0 text-text-muted hover:text-brand"
              >
                <Trash2 size={12} />
              </button>
            </li>
          ))}
        </ul>
        <button
          onClick={() =>
            edit((d) =>
              void d.level_bands.push({
                name: "New level",
                min_score: d.level_bands.length
                  ? Math.max(...d.level_bands.map((b) => b.max_score))
                  : 0,
                max_score: 100,
              }),
            )
          }
          className="mt-2 flex items-center gap-1 text-[11px] font-bold text-accent hover:underline"
        >
          <Plus size={11} /> Add level
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          disabled={saving || !dirty || problems.length > 0}
          onClick={async () => {
            try {
              await onSave(draft);
              setDirty(false);
              setServerProblems([]);
            } catch (e) {
              const detail = (e as { detail?: { problems?: string[] } }).detail;
              setServerProblems(detail?.problems ?? [(e as Error).message]);
            }
          }}
        >
          <span className="flex items-center gap-1.5">
            <Save size={12} /> Save framework
          </span>
        </Button>
        <Button
          disabled={saving}
          onClick={async () => {
            const def = await onReset();
            setDraft(def);
            setDirty(true);
            setServerProblems([]);
          }}
        >
          <span className="flex items-center gap-1.5">
            <RotateCcw size={12} /> Load defaults
          </span>
        </Button>
        {dirty && problems.length === 0 && (
          <span className="text-[11.5px] text-text-muted">Changes are not saved yet.</span>
        )}
      </div>
    </div>
  );
}

function round(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/\.?0+$/, "");
}

function WeightInput({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  return (
    <span className="flex shrink-0 items-center gap-1">
      <NumberInput value={value} onChange={onChange} />
      <span className="text-[10px] text-text-muted">wt</span>
    </span>
  );
}

function NumberInput({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  return (
    <input
      type="number"
      step="0.5"
      min={0}
      max={100}
      value={value}
      onChange={(e) => onChange(Number(e.target.value) || 0)}
      className="w-16 rounded-[6px] border border-border bg-card px-1.5 py-1 text-right text-[11.5px] tabular-nums text-text outline-none focus:border-accent"
    />
  );
}

function Stat({ value, label }: { value: string | number; label: string }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-[15px] font-bold tabular-nums text-accent">{value}</span>
      <span className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">
        {label}
      </span>
    </span>
  );
}
