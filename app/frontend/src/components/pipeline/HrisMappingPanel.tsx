import { useMemo, useState } from "react";
import { AlertTriangle, Check, Sparkles, Table2 } from "lucide-react";
import type { HrisPreview, HrisMappingField } from "../../types/pipeline";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { cn } from "../../lib/cn";

/**
 * instructions.txt, Input assets: "Job Description XLSX / HRIS dump (AI estimate /
 * user confirm Job Title, job description and job level columns --> optionally map
 * headcount against each job where this column exists (for final analytics)".
 *
 * The AI proposes, the user confirms — so every field is a dropdown pre-set to
 * the suggestion, with the model's confidence and its reasoning shown next to it.
 * A suggestion presented without its reasoning is just an unexplained default,
 * and the whole point is that the user is checking it.
 *
 * Only the title is required. Description is optional because a titles-only HRIS
 * extract is a real and supported case. Headcount is optional and is the only
 * source of the headcount analytics in the skills, tasks and architecture views.
 */

const FIELDS: {
  key: HrisMappingField;
  label: string;
  hint: string;
  required?: boolean;
}[] = [
  { key: "job_title_col", label: "Job title", hint: "The role or position name", required: true },
  {
    key: "job_description_col",
    label: "Job description",
    hint: "Prose describing purpose or responsibilities. Leave unmapped for a titles-only extract.",
  },
  { key: "job_level_col", label: "Job level", hint: "Grade, band or career level" },
  {
    key: "headcount_col",
    label: "Headcount",
    hint: "People per role. The only source of headcount analytics later.",
  },
  // The organisation's own reporting cascade, broadest first. Optional throughout — most
  // sheets carry one or two of these and plenty carry none. They become a filter in Work
  // Design Studio, which hides the control entirely when nothing was mapped.
  {
    key: "business_level_1_col",
    label: "Business level 1",
    hint: "Broadest tier of the organisation's own structure, e.g. Corporate Functions",
  },
  {
    key: "business_level_2_col",
    label: "Business level 2",
    hint: "Next tier down, e.g. Finance",
  },
  {
    key: "business_level_3_col",
    label: "Business level 3",
    hint: "Finest tier, e.g. Procurement",
  },
];

interface Props {
  preview: HrisPreview;
  onConfirm: (mapping: Record<HrisMappingField, string | null>, limit: number | null) => void;
  onCancel: () => void;
  busy: boolean;
}

export function HrisMappingPanel({ preview, onConfirm, onCancel, busy }: Props) {
  const suggestion = preview.suggested_mapping;
  const [mapping, setMapping] = useState<Record<HrisMappingField, string | null>>({
    job_title_col: suggestion.job_title_col,
    job_description_col: suggestion.job_description_col,
    job_level_col: suggestion.job_level_col,
    headcount_col: suggestion.headcount_col,
    business_level_1_col: suggestion.business_level_1_col,
    business_level_2_col: suggestion.business_level_2_col,
    business_level_3_col: suggestion.business_level_3_col,
  });
  const [limitAll, setLimitAll] = useState(true);
  const [limit, setLimit] = useState(Math.min(100, preview.row_count));

  const mappedCols = useMemo(
    () => FIELDS.map((f) => mapping[f.key]).filter(Boolean) as string[],
    [mapping],
  );

  // Two fields pointing at the same column is always a mistake and produces
  // records whose description is a repeat of the title.
  const duplicates = useMemo(() => {
    const seen = new Set<string>();
    const dupes = new Set<string>();
    for (const c of mappedCols) {
      if (seen.has(c)) dupes.add(c);
      seen.add(c);
    }
    return dupes;
  }, [mappedCols]);

  const rowsToImport = limitAll ? preview.row_count : Math.min(limit, preview.row_count);
  const canConfirm = !!mapping.job_title_col && duplicates.size === 0 && !busy;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 rounded-[10px] bg-panel px-4 py-2.5">
        <span className="flex items-center gap-1.5 text-[12.5px] font-semibold text-text">
          <Table2 size={14} className="text-text-muted" />
          {preview.filename ?? "Spreadsheet"}
        </span>
        <Stat value={preview.row_count.toLocaleString()} label="rows" />
        <Stat value={preview.columns.length} label="columns" />
      </div>

      <div>
        <p className="mb-1 flex items-center gap-1.5 text-[11px] font-extrabold uppercase tracking-wider text-text-muted">
          <Sparkles size={11} /> Suggested column mapping
        </p>
        <p className="mb-3 text-[11.5px] leading-snug text-text-secondary">
          These were inferred from the column names and their actual values. Check each one and
          change anything that looks wrong.
        </p>

        <div className="space-y-2.5">
          {FIELDS.map((field) => {
            const value = mapping[field.key];
            const confidence = suggestion.confidence?.[field.key];
            const reasoning = suggestion.reasoning?.[field.key];
            const changed = value !== suggestion[field.key];
            return (
              <div
                key={field.key}
                className={cn(
                  "rounded-[10px] border bg-card px-3.5 py-2.5",
                  duplicates.size > 0 && value && duplicates.has(value)
                    ? "border-brand-border"
                    : "border-border",
                )}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <label className="min-w-[7.5rem] text-[12.5px] font-semibold text-text">
                    {field.label}
                    {field.required && <span className="ml-1 text-brand">*</span>}
                  </label>
                  <select
                    value={value ?? ""}
                    onChange={(e) =>
                      setMapping((m) => ({ ...m, [field.key]: e.target.value || null }))
                    }
                    className="min-w-[13rem] flex-1 rounded-[8px] border border-border bg-card px-2.5 py-1.5 text-[12px] text-text outline-none focus:border-accent"
                  >
                    <option value="">
                      {field.required ? "— select a column —" : "— not in this sheet —"}
                    </option>
                    {preview.columns.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                  {/* An unmapped field with confidence 0 is not "0% sure" — the
                      model looked and is confident the column isn't there, which
                      is a different and less alarming statement. */}
                  {!changed && value == null && (
                    <Badge color="teal">none found</Badge>
                  )}
                  {!changed && value != null && confidence != null && (
                    <Badge color={confidence >= 0.8 ? "success" : confidence >= 0.5 ? "warning" : "brand"}>
                      {Math.round(confidence * 100)}% sure
                    </Badge>
                  )}
                  {changed && <Badge color="accent">changed</Badge>}
                </div>
                <p className="mt-1 pl-0 text-[11px] leading-snug text-text-muted">
                  {reasoning && !changed ? reasoning : field.hint}
                </p>
              </div>
            );
          })}
        </div>
      </div>

      {duplicates.size > 0 && (
        <Callout tone="brand">
          <strong>{Array.from(duplicates).join(", ")}</strong> is mapped to more than one field.
          Each field needs its own column.
        </Callout>
      )}

      {!mapping.job_description_col && (
        <Callout tone="warning">
          No description column mapped — records will carry the job title only. That is supported,
          but normalisation has to infer responsibilities from the title alone, so profiles will be
          less specific.
        </Callout>
      )}

      {!mapping.headcount_col && (
        <Callout tone="warning">
          No headcount column mapped. Headcount analytics in the skills, task and architecture views
          will be unavailable — everything else works normally.
        </Callout>
      )}

      {/* Sample of what will actually be ingested, using the current mapping —
          the fastest way to catch a wrong column is to look at its values. */}
      {mapping.job_title_col && (
        <div>
          <p className="mb-1.5 text-[11px] font-extrabold uppercase tracking-wider text-text-muted">
            Preview with this mapping
          </p>
          <div className="overflow-x-auto rounded-[10px] border border-border">
            <table className="w-full min-w-[36rem] text-[11.5px]">
              <thead>
                <tr className="border-b border-border bg-panel text-left text-[9.5px] uppercase tracking-wider text-text-muted">
                  {FIELDS.filter((f) => mapping[f.key]).map((f) => (
                    <th key={f.key} className="px-2.5 py-1.5 font-extrabold">
                      {f.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.preview.slice(0, 5).map((row, i) => (
                  <tr key={i} className="border-b border-border/60 last:border-0 align-top">
                    {FIELDS.filter((f) => mapping[f.key]).map((f) => {
                      const raw = row[mapping[f.key] as string];
                      const text = raw == null ? "" : String(raw);
                      return (
                        <td
                          key={f.key}
                          className={cn(
                            "px-2.5 py-1.5",
                            f.key === "job_title_col" ? "font-semibold text-text" : "text-text-secondary",
                          )}
                        >
                          {text.length > 160 ? `${text.slice(0, 160)}…` : text || "—"}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Every ingested row costs a strip call now and a normalise call after
          dedupe, so the count is spend. Say so before they commit. */}
      <div className="rounded-[10px] border border-border bg-panel px-4 py-3">
        <p className="mb-2 text-[11px] font-extrabold uppercase tracking-wider text-text-muted">
          How many rows to import
        </p>
        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-[12.5px] text-text">
            <input
              type="radio"
              checked={limitAll}
              onChange={() => setLimitAll(true)}
              className="accent-[var(--color-accent)]"
            />
            All {preview.row_count.toLocaleString()}
          </label>
          <label className="flex items-center gap-2 text-[12.5px] text-text">
            <input
              type="radio"
              checked={!limitAll}
              onChange={() => setLimitAll(false)}
              className="accent-[var(--color-accent)]"
            />
            First
            <input
              type="number"
              min={1}
              max={preview.row_count}
              value={limit}
              onChange={(e) => setLimit(Math.max(1, Number(e.target.value) || 1))}
              onFocus={() => setLimitAll(false)}
              className="w-20 rounded-[8px] border border-border bg-card px-2 py-1 text-[12px] tabular-nums text-text outline-none focus:border-accent"
            />
            only
          </label>
        </div>
        <p className="mt-2 text-[11.5px] leading-snug text-text-secondary">
          {rowsToImport.toLocaleString()} rows will each need one content-stripping call, then one
          normalisation call per distinct role after deduplication. Trial a subset first if you are
          unsure of the data.
        </p>
      </div>

      <div className="flex items-center gap-3">
        <Button variant="primary" onClick={() => onConfirm(mapping, limitAll ? null : limit)} disabled={!canConfirm}>
          <span className="flex items-center gap-1.5">
            <Check size={12} /> Import {rowsToImport.toLocaleString()} rows
          </span>
        </Button>
        <Button onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        {!mapping.job_title_col && (
          <span className="text-[11.5px] text-text-muted">A job title column is required.</span>
        )}
      </div>
    </div>
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

function Callout({ tone, children }: { tone: "brand" | "warning"; children: React.ReactNode }) {
  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-[10px] border px-3.5 py-2.5 text-[11.5px] leading-snug",
        tone === "brand" ? "border-brand-border bg-brand-bg" : "border-warning-border bg-warning-bg",
      )}
    >
      <AlertTriangle
        size={13}
        className={cn("mt-0.5 shrink-0", tone === "brand" ? "text-brand" : "text-warning")}
      />
      <p className="text-text">{children}</p>
    </div>
  );
}
