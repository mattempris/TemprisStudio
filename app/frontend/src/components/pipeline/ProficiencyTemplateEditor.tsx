import { useState } from "react";
import { AlertTriangle, Plus, RotateCcw, Save, Trash2 } from "lucide-react";
import type { ProficiencyTemplate } from "../../types/pipeline";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";

/**
 * instructions.txt step 9: "User can generate Proficiency definition across the
 * taxonomy based on a proficiency template that they can edit (default: Entry,
 * Intermediate, Advanced, Expert with proficiency criteria you generate once
 * during build)".
 *
 * These criteria are the rubric the per-cluster generation is written against, so
 * editing them before generating changes every skill cluster's wording. Edited
 * after, they no longer match what was generated — hence the warning.
 */

interface Props {
  template: ProficiencyTemplate;
  onSave: (template: ProficiencyTemplate) => Promise<void>;
  onReset: () => Promise<ProficiencyTemplate>;
  saving: boolean;
  /** True once cluster definitions have been generated from this template. */
  hasGenerated: boolean;
}

export function ProficiencyTemplateEditor({
  template,
  onSave,
  onReset,
  saving,
  hasGenerated,
}: Props) {
  const [draft, setDraft] = useState<ProficiencyTemplate>(template);
  const [dirty, setDirty] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);

  function edit(mutate: (t: ProficiencyTemplate) => void) {
    setDraft((prev) => {
      const next = structuredClone(prev);
      mutate(next);
      return next;
    });
    setDirty(true);
    setProblems([]);
  }

  const localProblems: string[] = [];
  if (draft.levels.length < 2) localProblems.push("A scale needs at least two levels.");
  const ordinals = draft.levels.map((l) => l.ordinal);
  if (new Set(ordinals).size !== ordinals.length)
    localProblems.push("Two levels share an ordinal — ordinals must be unique and ordered.");
  draft.levels.forEach((l) => {
    if (!l.name.trim()) localProblems.push("A level has no name.");
    if (!l.criteria.trim()) localProblems.push(`"${l.name || "(unnamed)"}" has no criteria.`);
  });
  const all = [...localProblems, ...problems];

  const sorted = [...draft.levels].sort((a, b) => a.ordinal - b.ordinal);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[11.5px] leading-snug text-text-secondary">
          The rubric that per-cluster proficiency wording is generated against. Edit it before
          generating.
        </p>
        {dirty && <Badge color="warning">unsaved</Badge>}
      </div>

      {all.length > 0 && (
        <div className="rounded-[10px] border border-warning-border bg-warning-bg px-3.5 py-2.5">
          <p className="mb-1 flex items-center gap-1.5 text-[12px] font-bold text-warning">
            <AlertTriangle size={13} /> Fix before saving
          </p>
          <ul className="space-y-0.5">
            {all.map((p, i) => (
              <li key={i} className="text-[11.5px] text-text">
                {p}
              </li>
            ))}
          </ul>
        </div>
      )}

      {hasGenerated && dirty && (
        <div className="rounded-[10px] border border-brand-border bg-brand-bg px-3.5 py-2.5 text-[11.5px] text-text">
          Cluster definitions were already generated from the previous template. Regenerate after
          saving, or the stored wording will not match this scale.
        </div>
      )}

      <ul className="space-y-2">
        {sorted.map((level) => {
          const i = draft.levels.indexOf(level);
          return (
            <li key={i} className="rounded-[10px] border border-border bg-card px-3.5 py-2.5">
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  min={1}
                  value={level.ordinal}
                  onChange={(e) =>
                    edit((t) => void (t.levels[i].ordinal = Number(e.target.value) || 1))
                  }
                  title="Order — lowest is least proficient"
                  className="w-12 rounded-[6px] border border-border bg-card px-1.5 py-1 text-right text-[11.5px] tabular-nums text-text outline-none focus:border-accent"
                />
                <input
                  value={level.name}
                  onChange={(e) => edit((t) => void (t.levels[i].name = e.target.value))}
                  placeholder="Level name"
                  className="min-w-0 flex-1 rounded-[6px] border border-border bg-card px-2 py-1 text-[12.5px] font-semibold text-text outline-none focus:border-accent"
                />
                <button
                  onClick={() => edit((t) => void t.levels.splice(i, 1))}
                  title="Remove level"
                  className="shrink-0 text-text-muted hover:text-brand"
                >
                  <Trash2 size={12} />
                </button>
              </div>
              <textarea
                value={level.criteria}
                rows={3}
                placeholder="What someone at this level can do, and how much guidance they need."
                onChange={(e) => edit((t) => void (t.levels[i].criteria = e.target.value))}
                className="mt-1.5 w-full resize-y rounded-[6px] border border-border bg-card px-2 py-1.5 text-[11.5px] leading-snug text-text outline-none focus:border-accent"
              />
              <input
                value={level.typical_autonomy ?? ""}
                placeholder="Typical autonomy (optional) — e.g. 'works under close supervision'"
                onChange={(e) =>
                  edit((t) => void (t.levels[i].typical_autonomy = e.target.value || null))
                }
                className="mt-1.5 w-full rounded-[6px] border border-border bg-card px-2 py-1 text-[11.5px] text-text-secondary outline-none focus:border-accent"
              />
            </li>
          );
        })}
      </ul>

      <button
        onClick={() =>
          edit((t) =>
            void t.levels.push({
              name: "New level",
              ordinal: t.levels.length ? Math.max(...t.levels.map((l) => l.ordinal)) + 1 : 1,
              criteria: "",
              typical_autonomy: null,
            }),
          )
        }
        className="flex items-center gap-1 text-[11px] font-bold text-accent hover:underline"
      >
        <Plus size={11} /> Add level
      </button>

      <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
        <Button
          variant="primary"
          disabled={saving || !dirty || localProblems.length > 0}
          onClick={async () => {
            try {
              await onSave(draft);
              setDirty(false);
              setProblems([]);
            } catch (e) {
              const detail = (e as { detail?: { problems?: string[] } }).detail;
              setProblems(detail?.problems ?? [(e as Error).message]);
            }
          }}
        >
          <span className="flex items-center gap-1.5">
            <Save size={12} /> Save template
          </span>
        </Button>
        <Button
          disabled={saving}
          onClick={async () => {
            setDraft(await onReset());
            setDirty(true);
            setProblems([]);
          }}
        >
          <span className="flex items-center gap-1.5">
            <RotateCcw size={12} /> Load defaults
          </span>
        </Button>
      </div>
    </div>
  );
}
