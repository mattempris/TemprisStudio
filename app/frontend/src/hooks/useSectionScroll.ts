import { useCallback, useLayoutEffect, useRef } from "react";

/**
 * Stop the wizard throwing you to the bottom of the page when a step opens or closes.
 *
 * Both halves of the app are single-open accordions, so every toggle closes one step and
 * opens another. When the step that closes is a tall one — the clustering step at 565
 * clusters is thousands of pixels — the document suddenly gets much shorter. The browser
 * then clamps `scrollTop` to the new maximum, and that maximum *is* the bottom of the
 * page. Nothing in this app called a scroll function; the jump was the browser doing the
 * only thing it could with a scroll position that no longer existed.
 *
 * The sidebar had a second, separate version of the same problem: its links were plain
 * `href="#step"` anchors, so the browser jumped using the layout as it stood *before*
 * React re-rendered. By the time the accordion had actually opened and closed, the
 * position it had scrolled to belonged to a page that no longer existed.
 *
 * Two intents, so two functions:
 *
 *   hold(id)    the user clicked this section's own header. It should not move — you
 *               collapsed a step, you did not ask to go anywhere.
 *   reveal(id)  the user clicked this section in the sidebar. Take me there, once the
 *               layout has settled.
 *
 * Both defer to `useLayoutEffect`, which runs after React has committed the DOM but
 * before the browser paints — so the correction happens in the same frame and there is no
 * visible jump to correct.
 */
type Pending =
  | { id: string; mode: "hold"; top: number }
  | { id: string; mode: "reveal" }
  | null;

/** How much of the viewport bottom edge counts as "not really visible". */
const EDGE_SLACK = 80;

export function useSectionScroll(dep: unknown) {
  const pending = useRef<Pending>(null);

  /** Consume whatever is pending. Safe to call twice; the second call finds nothing. */
  const apply = useCallback(() => {
    const p = pending.current;
    pending.current = null;
    if (!p) return; // a state change we did not initiate, e.g. the initial auto-expand
    const el = document.getElementById(p.id);
    if (!el) return;

    if (p.mode === "reveal") {
      // scroll-margin-top on the section clears the sticky header.
      el.scrollIntoView({ block: "start" });
      return;
    }

    const delta = el.getBoundingClientRect().top - p.top;
    if (Math.abs(delta) <= 1) return;
    window.scrollBy({ top: delta });

    // The shift is not always available. Collapsing a tall step can leave the document
    // too short to scroll as far as holding the header still would require, and the
    // browser silently clamps — which is the original bug wearing a different hat. When
    // that happens, put the header back in view rather than leaving it wherever the
    // clamp landed.
    const after = el.getBoundingClientRect().top;
    if (after < 0 || after > window.innerHeight - EDGE_SLACK) {
      el.scrollIntoView({ block: "start" });
    }
  }, []);

  // The normal path: React has committed the new layout and has not painted yet, so the
  // correction lands in the same frame.
  useLayoutEffect(() => {
    apply();
  }, [dep, apply]);

  const schedule = useCallback(
    (p: Pending) => {
      pending.current = p;
      // Not every click changes which step is open — clicking the sidebar entry for the
      // step already showing is the obvious case. The layout effect then never fires, so
      // without this the scroll would not happen and the stale request would sit there
      // waiting to be applied against the next, unrelated toggle. `apply` is idempotent,
      // so when the effect does fire first this finds nothing to do.
      requestAnimationFrame(apply);
    },
    [apply],
  );

  const hold = useCallback(
    (id: string) => {
      const el = document.getElementById(id);
      schedule(el ? { id, mode: "hold", top: el.getBoundingClientRect().top } : null);
    },
    [schedule],
  );

  const reveal = useCallback((id: string) => schedule({ id, mode: "reveal" }), [schedule]);

  return { hold, reveal };
}
