import type { FacetOption, WorkDesignFacetOptions, WorkDesignFacets } from "../../types/workDesign";
import type { PoolResult } from "../../types/workDesign";

/**
 * The filter that decides which slice of the workforce is being redesigned.
 *
 * Two rows do different things, and saying so matters. **Job family** narrows the *sample* — a
 * smaller set of people, each still with a whole week. **Task domain** is a lens on the profile:
 * it removes part of every job's week, so the total stops being the sample's whole week. The
 * summary line reports that rather than leaving a 12% cluster looking like 30%.
 *
 * The business-framework row renders only when the HRIS carried those columns, so a project
 * without them sees no empty dropdowns.
 */
export function FacetBar({
  options,
  facets,
  onChange,
  pool,
  unit,
}: {
  options: WorkDesignFacetOptions;
  facets: WorkDesignFacets;
  onChange: (next: WorkDesignFacets) => void;
  pool: PoolResult | null;
  unit: string;
}) {
  function toggleId(key: "job_family_ids" | "task_family_ids", id: number) {
    const cur = facets[key];
    onChange({
      ...facets,
      [key]: cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id],
    });
  }
  function toggleStr(key: "business_level_1" | "business_level_3", value: string) {
    const cur = facets[key];
    onChange({
      ...facets,
      [key]: cur.includes(value) ? cur.filter((x) => x !== value) : [...cur, value],
    });
  }

  const s = pool?.sample;
  const bf = options.business_framework;

  return (
    <div className="sticky top-[76px] z-20 rounded-[10px] border border-border bg-panel px-4 py-2.5">
      <Row
        label={options.level_titles?.job?.family ?? "Job family"}
        options={options.job.family}
        selected={facets.job_family_ids}
        onToggle={(id) => toggleId("job_family_ids", id)}
        onClear={() => onChange({ ...facets, job_family_ids: [] })}
      />
      <Row
        label={options.level_titles?.task?.family ?? "Task domain"}
        options={options.task.family}
        selected={facets.task_family_ids}
        onToggle={(id) => toggleId("task_family_ids", id)}
        onClear={() => onChange({ ...facets, task_family_ids: [] })}
        hint="a lens on the work, not on the people"
      />
      {bf && bf.level_1.length > 0 && (
        <StrRow
          label="Function"
          values={bf.level_1.map((x) => ({ value: x.value, count: x.headcount }))}
          selected={facets.business_level_1}
          onToggle={(v) => toggleStr("business_level_1", v)}
          onClear={() => onChange({ ...facets, business_level_1: [] })}
        />
      )}
      {bf && bf.level_3.length > 0 && (
        <StrRow
          label="Department"
          values={bf.level_3
            .filter(
              (x) =>
                facets.business_level_1.length === 0 ||
                facets.business_level_1.includes(x.grandparent),
            )
            .map((x) => ({ value: x.value, count: x.headcount }))}
          selected={facets.business_level_3}
          onToggle={(v) => toggleStr("business_level_3", v)}
          onClear={() => onChange({ ...facets, business_level_3: [] })}
        />
      )}

      {s && (
        <p className="mt-1.5 border-t border-border pt-1.5 text-[11px] tabular-nums text-text-secondary">
          <strong className="text-text">{s.job_profiles}</strong> job profiles ·{" "}
          <strong className="text-text">{s.headcount.toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong>{" "}
          {unit} · <strong className="text-text">{s.task_clusters}</strong> task clusters
          {s.shown_pct_of_week < 99.5 && (
            <span className="text-warning">
              {" "}
              · showing {s.shown_pct_of_week.toFixed(0)}% of their week
            </span>
          )}
          {s.assessed_clusters < s.task_clusters && (
            <span className="text-text-muted">
              {" "}
              · {s.task_clusters - s.assessed_clusters} not assessed
            </span>
          )}
        </p>
      )}
    </div>
  );
}

function Chips({ children }: { children: React.ReactNode }) {
  return <span className="flex flex-wrap items-center gap-1">{children}</span>;
}

function Row({
  label,
  options,
  selected,
  onToggle,
  onClear,
  hint,
}: {
  label: string;
  options: FacetOption[];
  selected: number[];
  onToggle: (id: number) => void;
  onClear: () => void;
  hint?: string;
}) {
  if (!options.length) return null;
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 py-0.5">
      <span
        className="w-[104px] shrink-0 text-[10px] font-extrabold uppercase tracking-wider text-text-muted"
        title={hint}
      >
        {label}
      </span>
      <Chips>
        {options.slice(0, 14).map((o) => (
          <button
            key={o.id}
            onClick={() => onToggle(o.id)}
            className={`rounded-full border px-2 py-0.5 text-[10.5px] font-semibold transition-colors ${
              selected.includes(o.id)
                ? "border-accent bg-accent-bg text-accent"
                : "border-border bg-card text-text-secondary hover:border-accent"
            }`}
          >
            {o.name} <span className="tabular-nums opacity-60">{o.leaves}</span>
          </button>
        ))}
        {selected.length > 0 && (
          <button onClick={onClear} className="text-[10.5px] font-semibold text-accent hover:underline">
            all
          </button>
        )}
      </Chips>
    </div>
  );
}

function StrRow({
  label,
  values,
  selected,
  onToggle,
  onClear,
}: {
  label: string;
  values: { value: string; count: number }[];
  selected: string[];
  onToggle: (v: string) => void;
  onClear: () => void;
}) {
  if (!values.length) return null;
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 py-0.5">
      <span className="w-[104px] shrink-0 text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
        {label}
      </span>
      <Chips>
        {values.slice(0, 14).map((o) => (
          <button
            key={o.value}
            onClick={() => onToggle(o.value)}
            className={`rounded-full border px-2 py-0.5 text-[10.5px] font-semibold transition-colors ${
              selected.includes(o.value)
                ? "border-accent bg-accent-bg text-accent"
                : "border-border bg-card text-text-secondary hover:border-accent"
            }`}
          >
            {o.value} <span className="tabular-nums opacity-60">{o.count}</span>
          </button>
        ))}
        {selected.length > 0 && (
          <button onClick={onClear} className="text-[10.5px] font-semibold text-accent hover:underline">
            all
          </button>
        )}
      </Chips>
    </div>
  );
}
