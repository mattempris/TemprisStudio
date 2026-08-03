import { ArrowRight, Lock } from "lucide-react";
import { useProjectStore } from "../../stores/projectStore";

/**
 * Switch between the two halves of the app.
 *
 * Workforce Studio only becomes reachable once the job architecture is complete —
 * it reads the hierarchy, the profiles and both taxonomies, and has nothing to show
 * without them. When it is unreachable the toggle says which asset is missing rather
 * than being inertly greyed out, since "disabled with no reason" is the least useful
 * thing a control can be.
 */
export function StudioToggle({ ready, missing }: { ready: boolean; missing: string[] }) {
  const { studio, setStudio } = useProjectStore();

  return (
    <div className="rounded-[10px] border border-border bg-panel p-1">
      <div className="flex gap-1">
        <button
          onClick={() => setStudio("job-architecture")}
          className={`flex-1 rounded-[7px] px-2 py-1.5 text-[11.5px] font-semibold transition-colors ${
            studio === "job-architecture"
              ? "bg-card text-accent shadow-[var(--shadow-card)]"
              : "text-text-secondary hover:text-text"
          }`}
        >
          Job architecture
        </button>
        <button
          onClick={() => ready && setStudio("workforce")}
          disabled={!ready}
          title={ready ? "Workforce Studio" : `Needs: ${missing.join(", ")}`}
          className={`flex flex-1 items-center justify-center gap-1 rounded-[7px] px-2 py-1.5 text-[11.5px] font-semibold transition-colors ${
            studio === "workforce"
              ? "bg-card text-accent shadow-[var(--shadow-card)]"
              : ready
                ? "text-text-secondary hover:text-text"
                : "cursor-not-allowed text-text-muted"
          }`}
        >
          {!ready && <Lock size={9} />}
          Workforce
        </button>
      </div>
      {!ready && missing.length > 0 && (
        <p className="px-2 pb-1 pt-1.5 text-[10.5px] leading-snug text-text-muted">
          Workforce Studio needs {missing.join(", ").toLowerCase()}.
        </p>
      )}
    </div>
  );
}

/** The same gate as a full-width call to action, for the foot of the page. */
export function ProceedToWorkforce({ ready, missing }: { ready: boolean; missing: string[] }) {
  const setStudio = useProjectStore((s) => s.setStudio);
  if (!ready) {
    return (
      <div className="rounded-[10px] border border-border bg-panel px-4 py-3 text-center">
        <p className="text-[12.5px] font-semibold text-text-secondary">
          Workforce Studio unlocks once the architecture is complete
        </p>
        <p className="mt-0.5 text-[11.5px] text-text-muted">Still needed: {missing.join(", ")}.</p>
      </div>
    );
  }
  return (
    <button
      onClick={() => setStudio("workforce")}
      className="flex w-full items-center justify-center gap-2 rounded-[10px] border border-accent-border bg-accent-bg px-4 py-3 text-[13px] font-bold text-accent transition-colors hover:bg-accent hover:text-white"
    >
      Proceed to Workforce Studio <ArrowRight size={14} />
    </button>
  );
}
