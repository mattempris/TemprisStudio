import { useMemo, useState } from "react";
import { AlertTriangle, ArrowDown, ArrowUp, Lock, RotateCcw, Save } from "lucide-react";
import type { ProfileTemplate, ProfileSection } from "../../types/pipeline";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { cn } from "../../lib/cn";

/**
 * instructions.txt step 7: "User defines job profile template".
 *
 * The user controls which sections a profile has, what each is called, their
 * order, and the guidance the model gets for each. Not raw HTML — the PDF and
 * DocX renderers build from the same structured JSON rather than converting the
 * HTML, so a free-form template would have nothing to turn into a Word heading
 * or list. Sections come from a catalogue where each carries the shape all three
 * renderers know how to lay out.
 *
 * Disabling a section removes it from the generation schema, so the model is
 * never asked for content that would be discarded.
 */

interface Props {
  template: ProfileTemplate;
  onSave: (sections: ProfileSection[]) => Promise<{ profiles_marked_stale: number }>;
  onReset: () => Promise<ProfileTemplate>;
  saving: boolean;
  profileCount: number;
}

const SHAPE_LABEL: Record<string, string> = {
  prose: "paragraphs",
  list: "bullet list",
  labelled_list: "labelled list",
  badges: "chips",
  inline: "single line",
};

export function ProfileTemplateEditor({
  template,
  onSave,
  onReset,
  saving,
  profileCount,
}: Props) {
  const [sections, setSections] = useState<ProfileSection[]>(template.sections);
  const [dirty, setDirty] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);

  const catalogue = useMemo(
    () => Object.fromEntries(template.catalogue.map((c) => [c.key, c])),
    [template.catalogue],
  );

  function edit(mutate: (s: ProfileSection[]) => void) {
    setSections((prev) => {
      const next = structuredClone(prev);
      mutate(next);
      return next;
    });
    setDirty(true);
    setProblems([]);
  }

  function move(i: number, delta: number) {
    const j = i + delta;
    if (j < 0 || j >= sections.length) return;
    edit((s) => {
      [s[i], s[j]] = [s[j], s[i]];
    });
  }

  const includedCount = sections.filter((s) => s.include).length;
  const local: string[] = [];
  if (!includedCount) local.push("Every section is disabled.");
  for (const s of sections) {
    const spec = catalogue[s.key];
    if (!spec) continue;
    if (!spec.removable && !s.include)
      local.push(`"${spec.default_heading}" cannot be removed.`);
    if (s.include && !s.heading.trim())
      local.push(`"${spec.default_heading}" is included but has no heading.`);
  }
  const all = [...local, ...problems];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[11.5px] leading-snug text-text-secondary">
          {includedCount} of {sections.length} sections included. Order here is the document order,
          and each heading is what appears in the HTML, PDF and Word versions alike.
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

      {profileCount > 0 && dirty && (
        <div className="rounded-[10px] border border-brand-border bg-brand-bg px-3.5 py-2.5 text-[11.5px] text-text">
          {profileCount} profile{profileCount === 1 ? "" : "s"} already generated under the current
          template. Saving marks them stale — regenerate to pick up these sections.
        </div>
      )}

      <ul className="space-y-1.5">
        {sections.map((section, i) => {
          const spec = catalogue[section.key];
          if (!spec) return null;
          return (
            <li
              key={section.key}
              className={cn(
                "rounded-[10px] border px-3.5 py-2.5",
                section.include ? "border-border bg-card" : "border-border bg-panel opacity-60",
              )}
            >
              <div className="flex flex-wrap items-center gap-2">
                <input
                  type="checkbox"
                  checked={section.include}
                  disabled={!spec.removable}
                  title={spec.removable ? "Include this section" : "This section is required"}
                  onChange={(e) => edit((s) => void (s[i].include = e.target.checked))}
                  className="accent-[var(--color-accent)] disabled:opacity-50"
                />
                <input
                  value={section.heading}
                  onChange={(e) => edit((s) => void (s[i].heading = e.target.value))}
                  placeholder={spec.default_heading}
                  className="min-w-0 flex-1 rounded-[6px] border border-border bg-card px-2 py-1 text-[12.5px] font-semibold text-text outline-none focus:border-accent"
                />
                <Badge color="teal">{SHAPE_LABEL[spec.shape] ?? spec.shape}</Badge>
                {!spec.removable && (
                  <span title="Required section">
                    <Lock size={11} className="text-text-muted" />
                  </span>
                )}
                <span className="flex shrink-0 gap-0.5">
                  <button
                    onClick={() => move(i, -1)}
                    disabled={i === 0}
                    title="Move up"
                    className="text-text-muted hover:text-text disabled:opacity-30"
                  >
                    <ArrowUp size={12} />
                  </button>
                  <button
                    onClick={() => move(i, 1)}
                    disabled={i === sections.length - 1}
                    title="Move down"
                    className="text-text-muted hover:text-text disabled:opacity-30"
                  >
                    <ArrowDown size={12} />
                  </button>
                </span>
              </div>
              <p className="mt-1 text-[11px] leading-snug text-text-muted">{spec.description}</p>
              {section.include && (
                <textarea
                  value={section.guidance}
                  rows={2}
                  placeholder={spec.default_guidance}
                  onChange={(e) => edit((s) => void (s[i].guidance = e.target.value))}
                  className="mt-1.5 w-full resize-y rounded-[6px] border border-border bg-card px-2 py-1 text-[11.5px] leading-snug text-text-secondary outline-none focus:border-accent"
                />
              )}
            </li>
          );
        })}
      </ul>

      <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
        <Button
          variant="primary"
          disabled={saving || !dirty || local.length > 0}
          onClick={async () => {
            try {
              await onSave(sections);
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
            const def = await onReset();
            setSections(def.sections);
            setDirty(true);
            setProblems([]);
          }}
        >
          <span className="flex items-center gap-1.5">
            <RotateCcw size={12} /> Load defaults
          </span>
        </Button>
        <span className="text-[11px] text-text-muted">
          Guidance left blank uses the default shown as placeholder text.
        </span>
      </div>
    </div>
  );
}
