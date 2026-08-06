import { useMemo, useState } from "react";
import { useDroppable } from "@dnd-kit/core";
import { LayoutGrid, List, Save, Trash2, X } from "lucide-react";
import { Treemap, type TreemapDatum } from "../workforce/Treemap";
import { Button } from "../ui/Button";
import { CapacityMeter } from "./CapacityMeter";
import type { Capacity, DesignedTaskLine } from "../../types/workDesign";

/**
 * The job being designed. One job definition, however many people hold it.
 *
 * Headcount raises the **capacity, not the size** — the panel is always one job description, and
 * a higher headcount simply means more hours of work can sit inside it.
 *
 * The treemap lays out over `capacity` rather than over the sum of its lines, so spare capacity
 * is drawn rather than implied. Without that a 40%-full job and a 200%-full job would look
 * identical, which defeats the point of having a capacity at all.
 */

const HEIGHT = 320;

export function JobDesignPanel({
  title,
  onTitle,
  headcount,
  onHeadcount,
  lines,
  onLines,
  capacity,
  unit,
  editingId,
  dirty,
  busy,
  onSave,
  onClear,
  onImport,
  forceList,
}: {
  title: string;
  onTitle: (v: string) => void;
  headcount: number;
  onHeadcount: (v: number) => void;
  lines: DesignedTaskLine[];
  onLines: (next: DesignedTaskLine[]) => void;
  capacity: Capacity;
  unit: string;
  editingId: string | null;
  dirty: boolean;
  busy: boolean;
  onSave: () => void;
  onClear: () => void;
  onImport: () => void;
  /** Narrow screen: pin the list and hide the toggle. See `useIsNarrow`. */
  forceList?: boolean;
}) {
  const [pref, setView] = useState<"map" | "list">("list");
  const view = forceList ? "list" : pref;
  const { setNodeRef, isOver } = useDroppable({ id: "design" });

  const data = useMemo<TreemapDatum<DesignedTaskLine>[]>(
    () =>
      lines.map((l) => ({
        id: l.id,
        value: l.hours_per_week,
        label: l.name,
        sub: `${l.hours_per_week.toFixed(1)} h`,
        // Oversight lines are shaded differently from the as-is work by carrying no score, so
        // supervision the design created is visibly not work it inherited.
        score: l.origin === "agent_oversight" ? null : l.automation_pct,
        payload: l,
      })),
    [lines],
  );

  function setHours(id: string, hours: number) {
    onLines(lines.map((l) => (l.id === id ? { ...l, hours_per_week: Math.max(0, hours) } : l)));
  }

  return (
    <div
      ref={setNodeRef}
      className={`rounded-[var(--radius-modal)] border bg-card shadow-modal transition-colors ${
        isOver ? "border-accent ring-2 ring-accent" : "border-border"
      }`}
    >
      <div className="border-b border-border px-4 py-3">
        <div className="flex items-center justify-between gap-2">
          <input
            value={title}
            onChange={(e) => onTitle(e.target.value)}
            placeholder="Name this job"
            className="min-w-0 flex-1 rounded-[7px] border border-transparent bg-transparent px-1 py-0.5 text-[14px] font-bold text-text outline-none hover:border-border focus:border-accent"
          />
          {!forceList && (
            <span className="flex shrink-0 rounded-[6px] border border-border bg-card p-0.5">
              {(["map", "list"] as const).map((v) => (
                <button
                  key={v}
                  onClick={() => setView(v)}
                  className={`rounded-[4px] px-1.5 py-0.5 transition-colors ${
                    view === v ? "bg-accent-bg text-accent" : "text-text-secondary hover:text-text"
                  }`}
                >
                  {v === "map" ? <LayoutGrid size={11} /> : <List size={11} />}
                </button>
              ))}
            </span>
          )}
        </div>
        <div className="mt-2 flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-[11px] font-semibold text-text-secondary">
            Headcount
            <input
              type="number"
              min={0.5}
              step={0.5}
              value={headcount}
              onChange={(e) => onHeadcount(Math.max(0.5, Number(e.target.value) || 0.5))}
              className="w-16 rounded-[6px] border border-border bg-card px-2 py-0.5 text-right text-[12px] tabular-nums text-text outline-none focus:border-accent"
            />
          </label>
          {editingId && (
            <span className="rounded-full border border-accent-border bg-accent-bg px-2 py-0.5 text-[10px] font-bold text-accent">
              editing
            </span>
          )}
        </div>
        <div className="mt-2">
          <CapacityMeter capacity={capacity} unit={unit} onFit={onHeadcount} />
        </div>
      </div>

      <div className="px-4 py-3">
        {lines.length === 0 ? (
          <div className="py-8 text-center">
            <p className="text-[12px] text-text-muted">
              {forceList
                ? "Add work with the + beside a row in the unreviewed work above."
                : "Drag work in from the left, or press the + on a cell."}
            </p>
            <Button onClick={onImport} className="mt-3">
              Import a job's task profile…
            </Button>
          </div>
        ) : view === "map" ? (
          // `extent` is the capacity, so cells occupy their true share of it and the unfilled
          // remainder is visible as spare capacity.
          <Treemap
            data={data}
            height={HEIGHT}
            extent={capacity.capacity_hours_per_week}
            emptyMessage="Nothing allocated yet."
          />
        ) : (
          <div className="max-h-[320px] overflow-y-auto">
            {lines.map((l) => (
              <div
                key={l.id}
                className="flex items-center gap-2 border-b border-border/60 py-1 last:border-0"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[11.5px] text-text">{l.name}</span>
                  <span className="block truncate text-[10.5px] text-text-muted">
                    {l.origin === "agent_oversight"
                      ? "oversight — created by an agent"
                      : l.cluster_name || "typed in"}
                  </span>
                </span>
                {/* Drag expresses intent; typing expresses quantity. A dropped line lands with a
                    sensible default and is edited here. */}
                <input
                  type="number"
                  min={0}
                  step={0.5}
                  value={l.hours_per_week}
                  onChange={(e) => setHours(l.id, Number(e.target.value))}
                  className="w-16 shrink-0 rounded-[6px] border border-border bg-card px-1.5 py-0.5 text-right text-[11px] tabular-nums text-text outline-none focus:border-accent"
                />
                <span className="w-8 shrink-0 text-right text-[10.5px] tabular-nums text-text-muted">
                  {capacity.capacity_hours_per_week
                    ? `${((100 * l.hours_per_week) / capacity.capacity_hours_per_week).toFixed(0)}%`
                    : ""}
                </span>
                <button
                  onClick={() => onLines(lines.filter((x) => x.id !== l.id))}
                  title="Remove — its hours return to the pool"
                  className="shrink-0 text-text-muted hover:text-brand"
                >
                  <X size={12} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 border-t border-border px-4 py-2.5">
        <Button variant="primary" onClick={onSave} disabled={busy || !dirty || !lines.length}>
          <span className="flex items-center gap-1.5">
            <Save size={12} /> {editingId ? "Save changes" : "Save as a job definition"}
          </span>
        </Button>
        {(lines.length > 0 || editingId) && (
          <button
            onClick={onClear}
            className="flex items-center gap-1 text-[11.5px] font-semibold text-text-muted hover:text-brand"
          >
            <Trash2 size={11} /> {editingId ? "Stop editing" : "Clear"}
          </button>
        )}
      </div>
    </div>
  );
}
