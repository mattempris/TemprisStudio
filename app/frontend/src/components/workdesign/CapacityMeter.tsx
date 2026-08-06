import type { Capacity } from "../../types/workDesign";

/**
 * A designed job's fill against its capacity.
 *
 * Over-capacity is a reading, not an error. "This work needs 11.5 people" is the most useful
 * thing this studio produces, and blocking or clamping the design to keep the bar under 100%
 * would throw that finding away. So the bar shows the overflow past a tick at 100% and the
 * required headcount is offered as a one-click fix rather than enforced.
 */
export function CapacityMeter({
  capacity,
  unit,
  onFit,
}: {
  capacity: Capacity;
  unit: string;
  onFit?: (headcount: number) => void;
}) {
  const { capacity_hours_per_week: cap, assigned_hours_per_week: used } = capacity;
  const fill = capacity.fill_pct ?? 0;
  // Under capacity the bar is the fill; over it, the bar is full and the excess is drawn as a
  // second segment so the shortfall has a size rather than just a number.
  const usedPct = Math.min(100, fill);
  const overPct = fill > 100 ? Math.min(60, fill - 100) : 0;

  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-[11.5px] tabular-nums">
        <span className="text-text-secondary">
          <strong className="text-text">{used.toFixed(1)}</strong> / {cap.toFixed(1)} h a week
          {capacity.fill_pct !== null && (
            <span className={capacity.over_capacity ? "ml-1.5 font-bold text-brand" : "ml-1.5 text-text-muted"}>
              {capacity.fill_pct.toFixed(0)}%
            </span>
          )}
        </span>
        {capacity.over_capacity ? (
          <span className="text-[11px] font-semibold text-brand">
            needs {capacity.required_headcount?.toFixed(1)} {unit === "FTE" ? "people" : "holders"}
          </span>
        ) : (
          <span className="text-[11px] text-text-muted">
            {capacity.spare_hours_per_week.toFixed(1)} h spare
          </span>
        )}
      </div>

      <div className="relative mt-1 h-1.5 w-full overflow-hidden rounded-full bg-panel">
        <div
          className={capacity.over_capacity ? "h-full bg-brand" : "h-full bg-accent"}
          style={{ width: `${usedPct}%` }}
        />
        {overPct > 0 && (
          <div
            className="absolute top-0 h-full bg-brand/50"
            style={{ left: "100%", width: `${overPct}%` }}
          />
        )}
      </div>

      {capacity.over_capacity && onFit && capacity.required_headcount !== null && (
        <button
          onClick={() => onFit(capacity.required_headcount as number)}
          className="mt-1.5 text-[11px] font-semibold text-accent hover:underline"
        >
          Fit headcount to the work → {capacity.required_headcount.toFixed(1)}
        </button>
      )}
    </div>
  );
}
