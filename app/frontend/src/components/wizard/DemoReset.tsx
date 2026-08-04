import { useEffect, useState } from "react";
import { RotateCcw, TriangleAlert } from "lucide-react";
import { api, type DemoStatus } from "../../services/api";
import { useProjectStore } from "../../stores/projectStore";
import { Modal } from "../ui/Modal";
import { Button } from "../ui/Button";

/**
 * Put a demo project back to its starting point.
 *
 * Demonstrating repeat-and-invalidate destroys work by design — re-run a tier and the tiers
 * above it are cleared and twenty downstream steps cascade. Without a reset, a demo project
 * is single-use, and "show me that again" means an hour of rebuilding.
 *
 * **Renders nothing at all unless the project is a seeded demo.** The check is the server's
 * (`is_demo`, which is true only for a project carrying a seed manifest), so this control
 * cannot appear on a client project even if someone drops it into a shared layout. A
 * disabled button would have been worse than absent here: it would advertise a destructive
 * action on projects that must never have one.
 */
export function DemoReset() {
  const [status, setStatus] = useState<DemoStatus | null>(null);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The app has no router — the selection lives in the store, not the URL.
  const { clientSlug, project } = useProjectStore();
  const projectSlug = project?.project_slug;

  useEffect(() => {
    if (!clientSlug || !projectSlug) return;
    api
      .demoStatus(clientSlug, projectSlug)
      .then(setStatus)
      .catch(() => setStatus(null)); // a project that cannot answer is simply not a demo
  }, [clientSlug, projectSlug]);

  if (!status?.is_demo || !clientSlug || !projectSlug) return null;
  // Bound after the guard so the closure below sees them as definitely present.
  const client = clientSlug;
  const proj = projectSlug;

  async function reset() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.demoReset(client, proj);
      if (res.warning) {
        // Surfaced rather than swallowed: the project is back apart from these, and a demo
        // that quietly half-reset is worse than one that says so.
        setError(res.warning);
        setBusy(false);
        return;
      }
      // A full reload rather than refetching each store: the reset replaces project state
      // wholesale, and every store in the app is holding some slice of the old one.
      window.location.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "reset failed");
      setBusy(false);
    }
  }

  const n = status.counts;
  const changes = n ? n.added + n.removed + n.changed : 0;

  return (
    <>
      <button
        onClick={() => setAsking(true)}
        title={
          status.drifted
            ? `${changes} files differ from the seeded state — reset to put them back`
            : "This demo project is at its seeded starting point"
        }
        className="flex w-full items-center justify-center gap-1.5 rounded-[7px] border border-dashed border-border bg-panel px-2 py-1.5 text-[11px] font-semibold text-text-secondary transition-colors hover:border-brand-border hover:bg-brand-bg hover:text-brand"
      >
        <RotateCcw className="h-3 w-3" />
        Reset demo
        {status.drifted && <span className="h-1.5 w-1.5 rounded-full bg-warning" />}
      </button>

      {asking && (
        <Modal
          onClose={() => {
            if (!busy) setAsking(false);
          }}
          title="Reset the demo project"
        >
          <div className="space-y-4 text-[13px] text-text-secondary">
          <p className="flex gap-2 rounded-[10px] border border-warning-border bg-warning-bg px-3 py-2.5 text-warning">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              Everything done in this project since it was seeded will be discarded — every
              re-clustered tier, every generated document, every assessment run after the
              starting point.
            </span>
          </p>
          <p>
            <strong className="text-text">{status.drifted ? `${changes} files differ` : "Nothing has changed yet"}</strong>
            {status.drifted
              ? " from the seeded state and will be put back."
              : " — resetting will leave the project exactly as it is."}
          </p>
          {status.seeded_from && (
            <p className="text-[12px] text-text-muted">
              Seeded from {status.seeded_from.client}/{status.seeded_from.project}
              {status.seeded_at ? ` on ${status.seeded_at.slice(0, 10)}` : ""}. That project is read
              only here and is never modified by a reset.
            </p>
          )}
          {error && (
            <p className="rounded-[10px] border border-brand-border bg-brand-bg px-3 py-2 text-[12.5px] text-brand">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2 pt-1">
            <Button onClick={() => setAsking(false)} disabled={busy}>
              Cancel
            </Button>
            <Button variant="primary" onClick={reset} disabled={busy}>
              {busy ? "Resetting…" : "Reset to seeded state"}
            </Button>
          </div>
          </div>
        </Modal>
      )}
    </>
  );
}
