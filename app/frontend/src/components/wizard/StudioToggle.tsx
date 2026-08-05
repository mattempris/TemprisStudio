import { ArrowRight, Lock } from "lucide-react";
import { useProjectStore, type Studio } from "../../stores/projectStore";

/** What a studio needs before it can be entered. */
export interface StudioGate {
  ready: boolean;
  missing: string[];
}

/** Display name per studio. The store's values are ids and are never shown. */
export const STUDIO_LABEL: Record<Studio, string> = {
  "job-architecture": "Job Architecture",
  workforce: "Work Architecture",
  "work-design": "Work Design",
};

const ORDER: Studio[] = ["job-architecture", "workforce", "work-design"];

/**
 * Move between the three studios.
 *
 * Vertical rather than a segmented row: three labels do not fit across a 224px sidebar —
 * "Work Architecture" alone needs about 105px at this weight — and abbreviating them would
 * throw away the naming the rebrand exists to establish. Stacked also matches the step nav
 * directly beneath, so the sidebar reads as one control surface rather than two idioms, and
 * it leaves room for a per-studio lock reason.
 *
 * A studio absent from `gates` is ungated, which is how Job Architecture stays always-on
 * without a special case. When one is locked it says which asset is missing rather than
 * being inertly greyed out, since "disabled with no reason" is the least useful thing a
 * control can be.
 */
export function StudioToggle({ gates }: { gates: Partial<Record<Studio, StudioGate>> }) {
  const { studio, setStudio } = useProjectStore();
  // The reason shown below the rows: the first locked studio's, since a later one is
  // usually blocked by the same thing and stacking three reasons fills the sidebar.
  const blocked = ORDER.map((s) => [s, gates[s]] as const).find(([, g]) => g && !g.ready);

  return (
    <div>
      <p className="mb-1 text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
        Tempris Studio
      </p>
      <div className="rounded-[10px] border border-border bg-panel p-1">
        <div className="space-y-0.5">
          {ORDER.map((s, i) => {
            const gate = gates[s];
            const ready = gate ? gate.ready : true;
            const active = studio === s;
            return (
              <button
                key={s}
                onClick={() => ready && setStudio(s)}
                disabled={!ready}
                title={
                  ready
                    ? `${STUDIO_LABEL[s]} Studio`
                    : `Needs: ${gate?.missing.join(", ") ?? "earlier steps"}`
                }
                className={`flex w-full items-center gap-2 rounded-[7px] px-2 py-1.5 text-left text-[11.5px] font-semibold transition-colors ${
                  active
                    ? "bg-card text-accent shadow-[var(--shadow-card)]"
                    : ready
                      ? "text-text-secondary hover:text-text"
                      : "cursor-not-allowed text-text-muted"
                }`}
              >
                <span className="tabular-nums text-text-muted">{i + 1}</span>
                <span className="min-w-0 flex-1 truncate">{STUDIO_LABEL[s]}</span>
                {!ready && <Lock size={9} className="shrink-0" />}
              </button>
            );
          })}
        </div>
        {blocked && blocked[1] && blocked[1].missing.length > 0 && (
          <p className="px-2 pb-1 pt-1.5 text-[10.5px] leading-snug text-text-muted">
            {STUDIO_LABEL[blocked[0]]} Studio needs{" "}
            {blocked[1].missing.join(", ").toLowerCase()}.
          </p>
        )}
      </div>
    </div>
  );
}

/** A gate as a full-width call to action, for the foot of a page. */
function Proceed({
  to,
  gate,
  unlockedNote,
}: {
  to: Studio;
  gate: StudioGate;
  unlockedNote: string;
}) {
  const setStudio = useProjectStore((s) => s.setStudio);
  if (!gate.ready) {
    return (
      <div className="rounded-[10px] border border-border bg-panel px-4 py-3 text-center">
        <p className="text-[12.5px] font-semibold text-text-secondary">{unlockedNote}</p>
        <p className="mt-0.5 text-[11.5px] text-text-muted">
          Still needed: {gate.missing.join(", ")}.
        </p>
      </div>
    );
  }
  return (
    <button
      onClick={() => setStudio(to)}
      className="flex w-full items-center justify-center gap-2 rounded-[10px] border border-accent-border bg-accent-bg px-4 py-3 text-[13px] font-bold text-accent transition-colors hover:bg-accent hover:text-white"
    >
      Proceed to {STUDIO_LABEL[to]} Studio <ArrowRight size={14} />
    </button>
  );
}

// Name kept: PipelinePage imports it, and renaming an export buys nothing.
export function ProceedToWorkforce({ ready, missing }: StudioGate) {
  return (
    <Proceed
      to="workforce"
      gate={{ ready, missing }}
      unlockedNote="Work Architecture Studio unlocks once the job architecture is complete"
    />
  );
}

export function ProceedToWorkDesign({ ready, missing }: StudioGate) {
  return (
    <Proceed
      to="work-design"
      gate={{ ready, missing }}
      unlockedNote="Work Design Studio unlocks once there is an agent or an augmentation to apply"
    />
  );
}
