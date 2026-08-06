import { CheckboxDropdown } from "../ui/CheckboxDropdown";
import type { WorkDesignFacetOptions, WorkDesignFacets } from "../../types/workDesign";
import type { PoolResult } from "../../types/workDesign";

/**
 * The filter that decides which slice of the workforce is being redesigned.
 *
 * Dropdowns rather than chips. The chip version laid every option out permanently — 14 job
 * families and 14 task domains across four rows — which cost about 140px above the panels a
 * user actually works in, on a page whose whole point is that the pool, the job and the levers
 * are visible together. Closed, this is one line.
 *
 * The two facets do different things, and saying so matters. **Job family** narrows the
 * *sample* — a smaller set of people, each still with a whole week. **Task domain** is a lens
 * on the profile: it removes part of every job's week, so the total stops being the sample's
 * whole week. The summary line reports that rather than leaving a 12% cluster looking like 30%.
 *
 * The business-framework dropdowns render only when the HRIS carried those columns, so a
 * project without them sees no empty controls.
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
  const s = pool?.sample;
  const bf = options.business_framework;

  // Department is filtered by the selected function, which is the one cascade in this bar. Done
  // here rather than server-side because it is a property of the options already loaded.
  const departments = (bf?.level_3 ?? []).filter(
    (x) =>
      facets.business_level_1.length === 0 || facets.business_level_1.includes(x.grandparent),
  );

  const anySelected =
    facets.job_family_ids.length > 0 ||
    facets.task_family_ids.length > 0 ||
    facets.business_level_1.length > 0 ||
    facets.business_level_3.length > 0;

  return (
    <div className="sticky top-[76px] z-30 rounded-[10px] border border-border bg-panel px-3 py-2">
      <div className="flex flex-wrap items-start gap-2">
        <CheckboxDropdown
          className="w-[190px]"
          label={options.level_titles?.job?.family ?? "Job family"}
          hint="Narrows the sample — fewer people, each with a whole week"
          options={options.job.family.map((o) => ({
            value: o.id,
            label: o.name,
            count: o.leaves,
          }))}
          selected={facets.job_family_ids}
          onChange={(v) => onChange({ ...facets, job_family_ids: v })}
        />
        <CheckboxDropdown
          className="w-[190px]"
          label={options.level_titles?.task?.family ?? "Task domain"}
          hint="A lens on the work, not on the people — it removes part of every job's week"
          options={options.task.family.map((o) => ({
            value: o.id,
            label: o.name,
            count: o.leaves,
          }))}
          selected={facets.task_family_ids}
          onChange={(v) => onChange({ ...facets, task_family_ids: v })}
        />
        {bf && bf.level_1.length > 0 && (
          <CheckboxDropdown
            className="w-[170px]"
            label="Function"
            options={bf.level_1.map((x) => ({
              value: x.value,
              label: x.value,
              count: x.headcount,
            }))}
            selected={facets.business_level_1}
            onChange={(v) =>
              // Clearing or changing the function invalidates any department chosen beneath it;
              // keeping a stale child selection would filter to an empty intersection and read
              // as "no work matches this filter".
              onChange({ ...facets, business_level_1: v, business_level_3: [] })
            }
          />
        )}
        {bf && departments.length > 0 && (
          <CheckboxDropdown
            className="w-[170px]"
            label="Department"
            options={departments.map((x) => ({
              value: x.value,
              label: x.value,
              count: x.headcount,
            }))}
            selected={facets.business_level_3}
            onChange={(v) => onChange({ ...facets, business_level_3: v })}
          />
        )}

        {s && (
          <p className="ml-auto self-center text-right text-[11px] tabular-nums leading-snug text-text-secondary">
            <strong className="text-text">{s.job_profiles}</strong> job profiles ·{" "}
            <strong className="text-text">
              {s.headcount.toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </strong>{" "}
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
            {anySelected && (
              <>
                {" · "}
                <button
                  onClick={() =>
                    onChange({
                      ...facets,
                      job_family_ids: [],
                      task_family_ids: [],
                      business_level_1: [],
                      business_level_3: [],
                    })
                  }
                  className="font-semibold text-accent hover:underline"
                >
                  clear filters
                </button>
              </>
            )}
          </p>
        )}
      </div>
    </div>
  );
}
