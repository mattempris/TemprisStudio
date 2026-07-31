import { useCallback, useEffect, useRef, useState } from "react";
import { Merge } from "lucide-react";
import type { DedupePreview } from "../../types/pipeline";
import { Button } from "../ui/Button";

/**
 * instructions.txt step 2: "dedupe the dataset using a parameterised cosine
 * similarity threshold".
 *
 * The backend caches the candidate-pair graph, so each threshold change is a
 * sub-millisecond re-grouping rather than a re-embed — the slider is live.
 */
interface Props {
  preview: (threshold: number) => Promise<DedupePreview>;
  onConfirm: (threshold: number) => void;
  confirming: boolean;
  initialThreshold?: number;
}

export function DedupePanel({ preview, onConfirm, confirming, initialThreshold = 0.9 }: Props) {
  const [threshold, setThreshold] = useState(initialThreshold);
  const [result, setResult] = useState<DedupePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);

  const fetchPreview = useCallback(
    async (value: number) => {
      const id = ++requestId.current;
      try {
        const res = await preview(value);
        if (id === requestId.current) {
          setResult(res);
          setError(null);
        }
      } catch (e) {
        if (id === requestId.current) setError(e instanceof Error ? e.message : String(e));
      }
    },
    [preview],
  );

  useEffect(() => {
    const t = setTimeout(() => void fetchPreview(threshold), 120);
    return () => clearTimeout(t);
  }, [threshold, fetchPreview]);

  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between">
        <label className="text-[12.5px] font-semibold text-text">
          Similarity threshold
          <span className="ml-2 font-normal text-text-muted">
            Jobs this similar are treated as the same role
          </span>
        </label>
        <span className="text-[15px] font-bold tabular-nums text-accent">{threshold.toFixed(2)}</span>
      </div>
      <input
        type="range"
        min={0.5}
        max={0.99}
        step={0.01}
        value={threshold}
        onChange={(e) => setThreshold(Number(e.target.value))}
        className="w-full accent-[var(--color-accent)]"
      />

      {error && <p className="mt-3 text-[12px] text-brand">{error}</p>}

      {result && (
        <>
          <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 rounded-[10px] border border-border bg-panel px-4 py-3">
            {[
              ["Input jobs", result.total_items],
              ["Distinct jobs", result.group_count],
              ["Merged away", result.items_merged_away],
              ["Groups with duplicates", result.duplicate_group_count],
            ].map(([label, value]) => (
              <div key={String(label)}>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">{label}</p>
                <p className="text-[15px] font-bold tabular-nums text-accent">{value}</p>
              </div>
            ))}
          </div>

          {result.groups.length > 0 && (
            <div className="mt-4">
              <p className="mb-2 flex items-center gap-1.5 text-[12px] font-semibold text-text">
                <Merge size={13} className="text-accent" />
                Groups that will be merged
              </p>
              <ul className="space-y-2">
                {result.groups.slice(0, 12).map((g) => (
                  <li key={g.group_id} className="rounded-[10px] border border-border px-3 py-2">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-[12px] font-semibold text-text">
                        {g.member_titles.length} jobs → 1
                      </span>
                      <span className="text-[10.5px] tabular-nums text-text-muted">
                        avg similarity {g.avg_similarity.toFixed(3)}
                      </span>
                    </div>
                    <ul className="mt-1">
                      {g.member_titles.map((title, i) => (
                        <li key={i} className="text-[12px] text-text-secondary">
                          {g.member_ids[i] === g.representative_id ? (
                            <span className="font-semibold text-text">{title} (kept as representative)</span>
                          ) : (
                            title
                          )}
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
              {result.groups.length > 12 && (
                <p className="mt-2 text-[11.5px] text-text-muted">
                  …and {result.groups.length - 12} more merge groups
                </p>
              )}
            </div>
          )}

          {result.groups.length === 0 && (
            <p className="mt-4 text-[12px] text-text-muted">
              No duplicates at this threshold — every job is treated as distinct. Lower the threshold to merge
              more aggressively.
            </p>
          )}
        </>
      )}

      <div className="mt-5">
        <Button variant="primary" onClick={() => onConfirm(threshold)} disabled={!result || confirming}>
          {confirming ? "Saving…" : `Confirm ${result?.group_count ?? ""} distinct jobs`}
        </Button>
      </div>
    </div>
  );
}
