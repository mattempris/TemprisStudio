import { useEffect, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { Button } from "../ui/Button";
import { Modal } from "../ui/Modal";

/**
 * Confirmation before re-running a step that has work built on top of it.
 *
 * The counts come from the server's `/lineage/preview`, which is produced by the same
 * walk that performs the invalidation — so what the dialog promises and what happens
 * cannot drift apart. Computing them in the browser would be the one place guaranteed to
 * fall out of step as steps are added.
 *
 * Two lists, not one, because the two outcomes are genuinely different: cleared work is
 * gone and has to be re-run, stale work is still there and still readable. Presenting
 * them as one "will be invalidated" list would either overstate the loss or hide it.
 */

export interface InvalidationItem {
  step: string;
  title: string;
  verb: "clear" | "mark_stale";
  count: number;
  counter: string;
}

export interface InvalidationPreview {
  step: string;
  title: string;
  affected: InvalidationItem[];
  clears: InvalidationItem[];
  marks_stale: InvalidationItem[];
  needs_confirmation: boolean;
}

export function RepeatConfirm({
  preview,
  loading,
  actionLabel,
  onCancel,
  onConfirm,
}: {
  preview: InvalidationPreview | null;
  loading: boolean;
  actionLabel: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <Modal
      title={`Re-run ${preview?.title ?? "this step"}?`}
      subtitle="Work built on top of this step will be affected."
      onClose={onCancel}
      footer={
        <div className="flex items-center justify-end gap-2">
          <Button onClick={onCancel}>Cancel</Button>
          <Button variant="primary" onClick={onConfirm} disabled={loading}>
            {actionLabel}
          </Button>
        </div>
      }
    >
      {loading ? (
        <p className="flex items-center gap-2 text-[12px] text-text-secondary">
          <Loader2 size={13} className="animate-spin text-accent" />
          Working out what this would affect…
        </p>
      ) : !preview || preview.affected.length === 0 ? (
        <p className="text-[12px] text-text-secondary">
          Nothing downstream exists yet, so this only re-runs the step itself.
        </p>
      ) : (
        <div className="space-y-3">
          {preview.clears.length > 0 && (
            <div>
              <p className="mb-1 flex items-center gap-1.5 text-[10px] font-extrabold uppercase tracking-wider text-brand">
                <AlertTriangle size={11} /> Will be cleared — has to be re-run
              </p>
              <ul className="space-y-0.5">
                {preview.clears.map((i) => (
                  <li key={i.step} className="flex items-baseline gap-2 text-[12px]">
                    <span className="w-16 shrink-0 text-right font-bold tabular-nums text-text">
                      {i.count.toLocaleString()}
                    </span>
                    <span className="text-text-secondary">
                      {i.counter} <span className="text-text-muted">· {i.title}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {preview.marks_stale.length > 0 && (
            <div>
              <p className="mb-1 text-[10px] font-extrabold uppercase tracking-wider text-warning">
                Marked out of date — kept and still readable
              </p>
              <ul className="space-y-0.5">
                {preview.marks_stale.map((i) => (
                  <li key={i.step} className="flex items-baseline gap-2 text-[12px]">
                    <span className="w-16 shrink-0 text-right font-bold tabular-nums text-text">
                      {i.count.toLocaleString()}
                    </span>
                    <span className="text-text-secondary">
                      {i.counter} <span className="text-text-muted">· {i.title}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <p className="border-t border-border pt-2 text-[11px] leading-snug text-text-muted">
            Nothing is deleted from storage. Cleared work is removed from the project so
            nothing reads a result that no longer matches its inputs; the lineage log keeps
            a record of every step that produced it.
          </p>
        </div>
      )}
    </Modal>
  );
}

/**
 * Fetches a preview and gates an action behind it.
 *
 * Returns `ask(run)` — call it in place of the action. If nothing downstream would be
 * cleared it runs straight through, because a dialog that says "this affects nothing" on
 * every click is a dialog people learn to dismiss without reading.
 */
export function useRepeatConfirm(
  fetchPreview: (step: string) => Promise<InvalidationPreview>,
) {
  const [pending, setPending] = useState<{ step: string; run: () => void } | null>(null);
  const [preview, setPreview] = useState<InvalidationPreview | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!pending) return;
    let live = true;
    setLoading(true);
    setPreview(null);
    fetchPreview(pending.step)
      .then((p) => {
        if (!live) return;
        setPreview(p);
        // Straight through when there is nothing to lose. The run still happens; it just
        // is not worth interrupting for.
        if (!p.needs_confirmation) {
          setPending(null);
          pending.run();
        }
      })
      .catch(() => live && setPreview(null))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
    // `pending` identity is the trigger; refetching on a new fetchPreview would be wrong.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending]);

  return {
    /** Wrap a destructive re-run. `step` is the lineage key. */
    ask: (step: string, run: () => void) => setPending({ step, run }),
    dialog:
      pending && (loading || preview?.needs_confirmation) ? (
        <RepeatConfirm
          preview={preview}
          loading={loading}
          actionLabel="Re-run and invalidate"
          onCancel={() => setPending(null)}
          onConfirm={() => {
            const p = pending;
            setPending(null);
            p.run();
          }}
        />
      ) : null,
  };
}

/**
 * What a completed run actually invalidated.
 *
 * Reported after the fact as well as before, because the two can legitimately differ: the
 * preview is a snapshot, and something else may have changed in between.
 */
export function InvalidatedNotice({ items }: { items: InvalidationItem[] }) {
  if (!items || items.length === 0) return null;
  const cleared = items.filter((i) => i.verb === "clear");
  const stale = items.filter((i) => i.verb === "mark_stale");
  return (
    <div className="rounded-[10px] border border-warning-border bg-warning-bg px-4 py-2.5">
      <p className="text-[12px] font-semibold text-text">
        Downstream work was invalidated by this run
      </p>
      {cleared.length > 0 && (
        <p className="mt-1 text-[11.5px] leading-snug text-text-secondary">
          <strong className="text-text">Cleared:</strong>{" "}
          {cleared
            .map((i) => `${i.count.toLocaleString()} ${i.counter}`)
            .join(", ")}
          . Those steps need running again.
        </p>
      )}
      {stale.length > 0 && (
        <p className="mt-1 text-[11.5px] leading-snug text-text-secondary">
          <strong className="text-text">Marked out of date:</strong>{" "}
          {stale.map((i) => `${i.count.toLocaleString()} ${i.counter}`).join(", ")}. Still
          readable and exportable, badged as stale.
        </p>
      )}
    </div>
  );
}
