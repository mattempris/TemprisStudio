import { useState } from "react";
import { Save } from "lucide-react";
import type { Boilerplate } from "../../types/pipeline";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";

/**
 * Step 7's "default boiler plate documents": the fixed text that wraps every
 * generated profile, plus the document accent colour.
 *
 * This exists because step 1 deliberately strips company blurb and equality
 * statements OUT of the source job descriptions — they are noise for clustering
 * and would be inconsistent across files. The generated documents therefore have
 * nowhere else to get them, and stating them once per project is the point.
 *
 * Saving re-renders existing profiles rather than marking them stale: this is
 * text around the content, so no regeneration is needed.
 */

interface Props {
  value: Boilerplate;
  onSave: (v: Boilerplate) => Promise<{ profiles_rerendered: number }>;
  saving: boolean;
}

export function BoilerplateEditor({ value, onSave, saving }: Props) {
  const [draft, setDraft] = useState<Boilerplate>(value);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function set<K extends keyof Boilerplate>(key: K, v: Boilerplate[K]) {
    setDraft((d) => ({ ...d, [key]: v }));
    setDirty(true);
    setError(null);
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[11.5px] leading-snug text-text-secondary">
          Fixed text applied to every profile document. Step 1 removes this kind of
          content from the source descriptions, so it is stated once here instead.
        </p>
        {dirty && <Badge color="warning">unsaved</Badge>}
      </div>

      <Field
        label="About the company"
        hint="Appears near the end of each profile. Leave blank to omit."
        value={draft.client_company_description ?? ""}
        onChange={(v) => set("client_company_description", v)}
      />
      <Field
        label="Equality and diversity statement"
        hint="The statement stripped from incoming job descriptions, restated once. Leave blank to omit."
        value={draft.diversity_statement ?? ""}
        onChange={(v) => set("diversity_statement", v)}
      />

      <div>
        <label className="mb-1 block text-[11px] font-extrabold uppercase tracking-wider text-text-muted">
          Document accent colour
        </label>
        <div className="flex items-center gap-2">
          <input
            type="color"
            value={draft.accent_color}
            onChange={(e) => set("accent_color", e.target.value)}
            className="h-8 w-12 cursor-pointer rounded-[6px] border border-border bg-card p-0.5"
          />
          <input
            value={draft.accent_color}
            onChange={(e) => set("accent_color", e.target.value)}
            className="w-28 rounded-[6px] border border-border bg-card px-2 py-1 font-mono text-[11.5px] text-text outline-none focus:border-accent"
          />
          <span className="text-[11px] text-text-muted">
            Themes headings and rules in the HTML, PDF and Word versions.
          </span>
        </div>
      </div>

      {error && <p className="text-[11.5px] text-brand">{error}</p>}

      <div className="border-t border-border pt-3">
        <Button
          variant="primary"
          disabled={saving || !dirty}
          onClick={async () => {
            try {
              const res = await onSave(draft);
              setDirty(false);
              setError(
                res.profiles_rerendered
                  ? null
                  : "Saved. No profiles exist yet, so nothing was re-rendered.",
              );
            } catch (e) {
              setError(e instanceof Error ? e.message : String(e));
            }
          }}
        >
          <span className="flex items-center gap-1.5">
            <Save size={12} /> Save boilerplate
          </span>
        </Button>
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div>
      <label className="mb-1 block text-[11px] font-extrabold uppercase tracking-wider text-text-muted">
        {label}
      </label>
      <textarea
        value={value}
        rows={3}
        onChange={(e) => onChange(e.target.value)}
        className="w-full resize-y rounded-[6px] border border-border bg-card px-2 py-1.5 text-[11.5px] leading-snug text-text outline-none focus:border-accent"
      />
      <p className="mt-0.5 text-[11px] text-text-muted">{hint}</p>
    </div>
  );
}
