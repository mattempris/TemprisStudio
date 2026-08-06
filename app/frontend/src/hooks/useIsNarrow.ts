import { useEffect, useState } from "react";

/**
 * True below the width at which Work Design Studio stops being a three-panel workbench and
 * becomes a single column.
 *
 * This has to be JS rather than a Tailwind class because two of the consequences are behavioural,
 * not visual: drag-and-drop is switched off, and both treemaps default to their list view. A
 * treemap does not survive being narrow — cells fall below the label thresholds and become a
 * texture — and a drag gesture on a narrow screen is a touch gesture competing with the page
 * scroll. Neither can be expressed as a `lg:` variant.
 *
 * 1280px is Tailwind's `xl`, which is the breakpoint the studio's own three-column grid uses
 * (`xl:grid-cols-[...]` in WorkDesignPage) — not `lg`. Named once here so the JS and the class
 * cannot drift apart; if the grid moves to another breakpoint, this moves with it.
 */
const WORKBENCH = "(min-width: 1280px)";

export function useIsNarrow(): boolean {
  // Initialise from the real value rather than a default, so the first paint is already correct
  // and a narrow load never renders a treemap it is about to throw away.
  const [narrow, setNarrow] = useState(
    () => typeof window !== "undefined" && !window.matchMedia(WORKBENCH).matches,
  );

  useEffect(() => {
    const mq = window.matchMedia(WORKBENCH);
    const onChange = () => setNarrow(!mq.matches);
    onChange();
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  return narrow;
}
