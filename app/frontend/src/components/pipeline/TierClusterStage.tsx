import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, ChevronRight, Play, Sparkles } from "lucide-react";
import type { JobHandle, TierClusters, TierPreview, TierStatus } from "../../types/pipeline";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { cn } from "../../lib/cn";

/**
 * One tier of the hierarchy — used three times, for profiles, categories and
 * families (instructions.txt steps 5-6, split so each is reviewed on its own).
 *
 * The flow mirrors the dedupe step deliberately: move a slider, see the
 * consequences immediately, then commit. The difference is that there are two
 * things to decide here, so there are two sliders:
 *
 *   cluster count — how many groups. Preview is instant; the tree is cached.
 *   stability gate — how uncertain an item must be before the model re-checks it.
 *     This is the one that costs money, so it always shows how many items it will
 *     send and never spends anything until "Confirm".
 *
 * On small tiers the stability scores come back with the size preview, so both
 * sliders are live. At job scale the bootstrap takes seconds rather than
 * milliseconds, so that tier asks for one explicit "Assess stability" press
 * first — stated plainly rather than hidden behind a spinner.
 */

interface Props {
  title: string;
  status: TierStatus;
  preview: (k: number) => Promise<TierPreview>;
  gatePreview: (gate: number) => Promise<TierPreview>;
  onBuild: () => Promise<JobHandle>;
  onAnalyse: (k: number) => Promise<JobHandle>;
  onConfirm: (k: number, gate: number) => Promise<JobHandle>;
  loadClusters: () => Promise<TierClusters>;
  onRename: (clusterId: number, name: string) => Promise<unknown>;
  runJob: (start: () => Promise<JobHandle>) => void;
  busy: boolean;
  progress: React.ReactNode;
}

export function TierClusterStage({
  title,
  status,
  preview,
  gatePreview,
  onBuild,
  onAnalyse,
  onConfirm,
  loadClusters,
  onRename,
  runJob,
  busy,
  progress,
}: Props) {
  const [k, setK] = useState<number>(status.k ?? 0);
  const [gate, setGate] = useState<number>(status.gate ?? 0.58);
  const [result, setResult] = useState<TierPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [clusters, setClusters] = useState<TierClusters | null>(null);
  const requestId = useRef(0);

  const maxK = status.max_k ?? 2;
  const suggested = Math.max(2, Math.min(maxK, Math.round((status.item_count ?? 12) / 6)));

  // Default k once the item count is known, so the slider starts somewhere sane.
  useEffect(() => {
    if (k === 0 && status.item_count) setK(status.k ?? suggested);
  }, [status.item_count, status.k, k, suggested]);

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
    if (!status.built || !k) return;
    const t = setTimeout(() => void fetchPreview(k), 150);
    return () => clearTimeout(t);
  }, [k, status.built, fetchPreview]);

  // The gate only needs a round-trip when stability was computed by an explicit
  // analyse pass; when it came back with the preview the numbers are already here.
  useEffect(() => {
    if (!result?.stability_included) return;
    const t = setTimeout(() => {
      void gatePreview(gate)
        .then((r) => setResult((prev) => (prev ? { ...prev, ...r } : r)))
        .catch(() => {});
    }, 150);
    return () => clearTimeout(t);
  }, [gate, result?.stability_included, result?.k, gatePreview]);

  const refreshClusters = useCallback(async () => {
    if (!status.confirmed) return;
    try {
      setClusters(await loadClusters());
    } catch {
      setClusters(null);
    }
  }, [status.confirmed, loadClusters]);

  useEffect(() => {
    void refreshClusters();
  }, [refreshClusters]);

  if (!status.ready_to_run) {
    return (
      <p className="rounded-[10px] border border-border bg-panel px-4 py-3 text-[12.5px] text-text-secondary">
        Confirm the {status.below} tier first — {title.toLowerCase()} are groups of{" "}
        {status.below === "profile" ? "job profiles" : `job ${status.below}s`}.
      </p>
    );
  }

  const stabilityKnown = !!result?.stability_included;
  const needsAnalyse = !!status.built && !status.stability_inline && !stabilityKnown;

  return (
    <div className="space-y-4">
      {!status.built ? (
        <div className="space-y-2">
          <p className="text-[12.5px] text-text-secondary">
            {status.item_count ?? "?"} {status.item_noun} to group into {title.toLowerCase()}.
          </p>
          <Button variant="primary" onClick={() => runJob(onBuild)} disabled={busy}>
            <span className="flex items-center gap-1.5">
              <Play size={12} /> Prepare {title.toLowerCase()}
            </span>
          </Button>
          {progress}
        </div>
      ) : (
        <>
          {/* cluster count */}
          <div>
            <div className="mb-1.5 flex items-baseline justify-between">
              <label className="text-[12.5px] font-semibold text-text">
                How many {title.toLowerCase()}
                <span className="ml-2 font-normal text-text-muted">
                  from {status.item_count} {status.item_noun}
                </span>
              </label>
              <span className="text-[15px] font-bold tabular-nums text-accent">{k}</span>
            </div>
            <input
              type="range"
              min={2}
              max={maxK}
              value={k}
              onChange={(e) => setK(Number(e.target.value))}
              className="w-full accent-[var(--color-accent)]"
            />
            {result && result.k === k && (
              <div className="mt-1.5 flex flex-wrap gap-1">
                {result.sizes.map((size, i) => (
                  <span
                    key={i}
                    title={`Cluster ${i}: ${size}`}
                    className="rounded-sm bg-accent-bg px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-accent"
                  >
                    {size}
                  </span>
                ))}
              </div>
            )}
          </div>

          {result && result.k === k && (
            <div className="flex flex-wrap gap-x-6 gap-y-2 rounded-[10px] border border-border bg-panel px-4 py-3">
              <Stat label="Singletons" value={result.singletons} />
              <Stat label="Largest" value={result.largest} />
              {result.mean_stability != null && (
                <Stat label="Mean stability" value={result.mean_stability.toFixed(2)} />
              )}
              {result.singletons > result.sizes.length / 2 && (
                <p className="w-full text-[11.5px] text-warning">
                  Over half these clusters hold a single item — consider fewer.
                </p>
              )}
            </div>
          )}

          {needsAnalyse && (
            <div className="rounded-[10px] border border-border bg-panel px-4 py-3">
              <p className="text-[12.5px] font-semibold text-text">Stability</p>
              <p className="mt-0.5 mb-2 text-[11.5px] leading-snug text-text-secondary">
                {status.item_count} items is enough that scoring stability takes a few
                seconds rather than being instant, so it is a separate step here. It
                costs no model calls — only the routing afterwards does.
              </p>
              <Button variant="primary" onClick={() => runJob(() => onAnalyse(k))} disabled={busy}>
                <span className="flex items-center gap-1.5">
                  <Sparkles size={12} /> Assess stability at {k} {title.toLowerCase()}
                </span>
              </Button>
            </div>
          )}

          {/* the gate — the control that decides spend */}
          {stabilityKnown && (
            <div className="rounded-[10px] border border-border bg-panel px-4 py-3">
              <div className="mb-1.5 flex items-baseline justify-between">
                <label className="text-[12.5px] font-semibold text-text">
                  Stability gate
                  <span className="ml-2 font-normal text-text-muted">
                    Items below this get re-checked by the model
                  </span>
                </label>
                <span className="text-[15px] font-bold tabular-nums text-accent">
                  {gate.toFixed(2)}
                </span>
              </div>
              <input
                type="range"
                min={0.3}
                max={0.9}
                step={0.01}
                value={gate}
                onChange={(e) => setGate(Number(e.target.value))}
                className="w-full accent-[var(--color-accent)]"
              />

              {result?.distribution && result.distribution.length > 0 && (
                <div className="mt-2">
                  <div className="flex h-10 items-end gap-px">
                    {result.distribution.map((b) => {
                      const peak = Math.max(...result.distribution!.map((x) => x.count)) || 1;
                      const below = b.to <= gate;
                      return (
                        <span
                          key={b.from}
                          title={`${b.from}–${b.to}: ${b.count} items`}
                          className={cn(
                            "flex-1 rounded-t-sm",
                            below ? "bg-warning" : "bg-accent",
                          )}
                          style={{ height: `${Math.max(2, (100 * b.count) / peak)}%` }}
                        />
                      );
                    })}
                  </div>
                  <div className="mt-0.5 flex justify-between text-[9.5px] text-text-muted">
                    <span>0.0 less stable</span>
                    <span>more stable 1.0</span>
                  </div>
                </div>
              )}

              <p className="mt-2 text-[12px] leading-snug text-text">
                <span className="font-bold tabular-nums text-accent">
                  {result?.n_routed ?? 0}
                </span>{" "}
                of {status.item_count} items ({result?.pct_routed ?? 0}%) would be sent to the
                model — one call each, plus a re-check for any it is unsure about.
                {(result?.n_routed ?? 0) === 0 &&
                  " Nothing needs routing at this gate; confirming will just name the clusters."}
              </p>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="primary"
              onClick={() => runJob(() => onConfirm(k, gate))}
              disabled={busy || !!error || !k}
            >
              <span className="flex items-center gap-1.5">
                <Check size={12} />
                {status.confirmed ? "Re-cluster and rename" : `Confirm and name ${title.toLowerCase()}`}
              </span>
            </Button>
            {status.confirmed && (
              <span className="text-[11.5px] text-text-muted">
                Currently {status.k} {title.toLowerCase()}, gate {status.gate?.toFixed(2)},{" "}
                {status.n_moved} moved by the model.
              </span>
            )}
          </div>

          {error && (
            <p className="flex items-start gap-1.5 text-[12px] text-brand">
              <AlertTriangle size={13} className="mt-0.5 shrink-0" /> {error}
            </p>
          )}

          {progress}

          {clusters && <ClusterList data={clusters} onRename={onRename} onRenamed={refreshClusters} />}
        </>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">{label}</p>
      <p className="text-[13px] font-bold tabular-nums text-text">{value}</p>
    </div>
  );
}

function ClusterList({
  data,
  onRename,
  onRenamed,
}: {
  data: TierClusters;
  onRename: (id: number, name: string) => Promise<unknown>;
  onRenamed: () => void;
}) {
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");

  return (
    <div className="space-y-1.5">
      <p className="text-[11px] font-extrabold uppercase tracking-wider text-text-muted">
        {data.clusters.length} clusters · {data.n_moved} moved by the model
      </p>
      <ul className="space-y-1">
        {data.clusters.map((c) => (
          <li key={c.id} className="overflow-hidden rounded-[10px] border border-border bg-card">
            <div className="flex items-center gap-2 px-3 py-2">
              <button onClick={() => setOpen((o) => ({ ...o, [c.id]: !o[c.id] }))} className="shrink-0">
                <ChevronRight
                  size={13}
                  className={cn("text-text-muted transition-transform", open[c.id] && "rotate-90")}
                />
              </button>
              {editing === c.id ? (
                <input
                  autoFocus
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") setEditing(null);
                    if (e.key === "Enter" && draft.trim()) {
                      void onRename(c.id, draft.trim()).then(() => {
                        setEditing(null);
                        onRenamed();
                      });
                    }
                  }}
                  onBlur={() => setEditing(null)}
                  className="min-w-0 flex-1 rounded-[6px] border border-accent bg-card px-2 py-0.5 text-[12.5px] font-semibold text-text outline-none"
                />
              ) : (
                <button
                  onClick={() => {
                    setEditing(c.id);
                    setDraft(c.name);
                  }}
                  title="Click to rename"
                  className="min-w-0 flex-1 truncate text-left text-[12.5px] font-semibold text-text hover:text-accent"
                >
                  {c.name}
                </button>
              )}
              <span className="shrink-0 text-[11px] tabular-nums text-text-muted">{c.size}</span>
            </div>
            {open[c.id] && (
              <ul className="border-t border-border bg-panel/40 px-3 py-2 pl-9">
                {c.members.map((m) => (
                  <li key={m.item_id} className="flex items-center gap-2 py-0.5 text-[11.5px]">
                    <span className="min-w-0 flex-1 truncate text-text-secondary">{m.label}</span>
                    {m.moved && (
                      <Badge color="warning" title={`Moved from ${m.moved_from ?? "another cluster"}`}>
                        moved
                      </Badge>
                    )}
                    {!m.moved && m.routed_by_llm && <Badge color="teal">checked</Badge>}
                    <span className="w-8 shrink-0 text-right tabular-nums text-text-muted">
                      {m.stability_score?.toFixed(2) ?? "—"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
