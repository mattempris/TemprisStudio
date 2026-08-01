import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle } from "lucide-react";
import type { ClusterPreview } from "../../types/pipeline";
import { Button } from "../ui/Button";

/**
 * instructions.txt step 5: "Support interactive panel which allows the user to
 * select different cluster numbers for A) job profiles (groups of jobs),
 * B) job categories (groups of job profiles), and C) job families (groups of
 * job categories)."
 *
 * Every slider move re-cuts the cached Ward tree server-side — no re-embedding,
 * so it stays responsive. Requests are debounced and superseded so a fast drag
 * doesn't queue up stale responses.
 */
/** Tier labels differ per entity (jobs / skills / tasks) but the mechanics are
 *  identical, so the same panel drives all three. */
export interface TierLabels {
  tiers: [string, string, string];
  hints: [string, string, string];
  itemNoun: string;
  leafNoun: string;
}

const JOB_LABELS: TierLabels = {
  tiers: ["Job families", "Job categories", "Job profiles"],
  hints: ["Broadest grouping", "Groups of profiles", "Groups of jobs"],
  itemNoun: "normalised jobs",
  leafNoun: "profile",
};

interface Props {
  itemCount: number;
  initial: { families: number; categories: number; profiles: number };
  preview: (k: { families: number; categories: number; profiles: number }) => Promise<ClusterPreview>;
  onConfirm: (k: { families: number; categories: number; profiles: number }, gate: number) => void;
  confirming: boolean;
  labels?: TierLabels;
}

export function ClusterKPanel({
  itemCount,
  initial,
  preview,
  onConfirm,
  confirming,
  labels = JOB_LABELS,
}: Props) {
  const [k, setK] = useState(initial);
  const [gate, setGate] = useState(0.58);
  const [result, setResult] = useState<ClusterPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const requestId = useRef(0);

  const maxK = Math.max(2, itemCount - 1);

  const fetchPreview = useCallback(
    async (next: typeof k) => {
      // The tiers must nest: families <= categories <= profiles. Reject locally
      // so the user gets instant feedback instead of a round-trip 422.
      if (!(next.families <= next.categories && next.categories <= next.profiles)) {
        setError("Families ≤ Categories ≤ Profiles — each tier must be at least as fine as the one above it.");
        setResult(null);
        return;
      }
      setError(null);
      const id = ++requestId.current;
      setPending(true);
      try {
        const res = await preview(next);
        if (id === requestId.current) setResult(res); // ignore superseded responses
      } catch (e) {
        if (id === requestId.current) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (id === requestId.current) setPending(false);
      }
    },
    [preview],
  );

  useEffect(() => {
    const t = setTimeout(() => void fetchPreview(k), 180);
    return () => clearTimeout(t);
  }, [k, fetchPreview]);

  const tiers = [
    { key: "families" as const, label: labels.tiers[0], hint: labels.hints[0], sizes: result?.family_sizes },
    { key: "categories" as const, label: labels.tiers[1], hint: labels.hints[1], sizes: result?.category_sizes },
    { key: "profiles" as const, label: labels.tiers[2], hint: labels.hints[2], sizes: result?.profile_sizes },
  ];

  return (
    <div>
      <p className="mb-4 text-[13px] text-text-secondary">
        Choose how many clusters to create at each tier. {itemCount} {labels.itemNoun} will be organised into
        a {labels.tiers[0]} › {labels.tiers[1]} › {labels.tiers[2]} hierarchy.
      </p>

      <div className="space-y-4">
        {tiers.map((tier) => (
          <div key={tier.key}>
            <div className="mb-1.5 flex items-baseline justify-between">
              <label className="text-[12.5px] font-semibold text-text">
                {tier.label}
                <span className="ml-2 font-normal text-text-muted">{tier.hint}</span>
              </label>
              <span className="text-[15px] font-bold tabular-nums text-accent">{k[tier.key]}</span>
            </div>
            <input
              type="range"
              min={2}
              max={maxK}
              value={k[tier.key]}
              onChange={(e) => setK({ ...k, [tier.key]: Number(e.target.value) })}
              className="w-full accent-[var(--color-accent)]"
            />
            {tier.sizes && (
              <div className="mt-1.5 flex flex-wrap gap-1">
                {tier.sizes.map((size, i) => (
                  <span
                    key={i}
                    title={`Cluster ${i}: ${size} job${size === 1 ? "" : "s"}`}
                    className="rounded-sm bg-accent-bg px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-accent"
                  >
                    {size}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {error && (
        <div className="mt-4 flex items-start gap-2 rounded-[10px] border border-warning-border bg-warning-bg px-3 py-2">
          <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warning" />
          <p className="text-[12px] text-text">{error}</p>
        </div>
      )}

      {result && !error && (
        <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 rounded-[10px] border border-border bg-panel px-4 py-3">
          {[
            [`Singleton ${labels.leafNoun}s`, result.singleton_profiles],
            [`Largest ${labels.leafNoun}`, `${result.largest_profile_size} items`],
          ].map(([label, value]) => (
            <div key={String(label)}>
              <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">{label}</p>
              <p className="text-[13px] font-bold tabular-nums text-text">{value}</p>
            </div>
          ))}
          {result.singleton_profiles > result.profile_sizes.length / 2 && (
            <p className="w-full text-[11.5px] text-warning">
              Over half the {labels.leafNoun}s contain a single item — consider fewer clusters at that tier.
            </p>
          )}
        </div>
      )}

      <div className="mt-5 border-t border-border pt-4">
        <div className="mb-1.5 flex items-baseline justify-between">
          <label className="text-[12.5px] font-semibold text-text">
            Stability gate
            <span className="ml-2 font-normal text-text-muted">
              Items below this get re-checked by the model
            </span>
          </label>
          <span className="text-[15px] font-bold tabular-nums text-accent">{gate.toFixed(2)}</span>
        </div>
        <input
          type="range"
          min={0.4}
          max={0.8}
          step={0.01}
          value={gate}
          onChange={(e) => setGate(Number(e.target.value))}
          className="w-full accent-[var(--color-accent)]"
        />
        <p className="mt-1.5 text-[11.5px] leading-relaxed text-text-muted">
          Clustering places most items confidently. Those where the geometry is uncertain are sent to the model
          to reassign, which is where the cost goes — a lower gate means fewer model calls. Around 0.55–0.60
          works well; above 0.70 tends to pay for assignments the model simply confirms.
        </p>
      </div>

      <div className="mt-5 flex items-center gap-3">
        <Button variant="primary" onClick={() => onConfirm(k, gate)} disabled={!!error || pending || confirming}>
          {confirming ? "Clustering…" : "Confirm and name clusters"}
        </Button>
        {pending && <span className="text-[11.5px] text-text-muted">Updating preview…</span>}
      </div>
    </div>
  );
}
