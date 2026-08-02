import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, ChevronRight, Play, Sparkles } from "lucide-react";
import type {
  JobHandle,
  SizeStats,
  TierClusters,
  TierName,
  TierPreview,
  TierStatus,
} from "../../types/pipeline";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Collapsible } from "../ui/Collapsible";
import { Tooltip, TitleListTooltip } from "../ui/Tooltip";
import { cn } from "../../lib/cn";

/** Every tier resolves down to real job titles, so say which relationship it is. */
const TOOLTIP_SUBHEADING: Record<TierName, string> = {
  profile: "Source job titles:",
  category: "Job titles beneath this category:",
  family: "Job titles beneath this family:",
};

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

  // Stability is known either because the preview computed it inline (small tiers)
  // or because an analyse pass has run at this exact k. The second half matters:
  // the analyse job's results live server-side, so without it a large tier finished
  // its bootstrap and then showed nothing — the gate slider never appeared and the
  // "Assess stability" button sat there as if the pass had not happened.
  const analysed = status.analysed_k === k;
  const stabilityKnown = !!result?.stability_included || analysed;
  const needsAnalyse = !!status.built && !status.stability_inline && !stabilityKnown;

  useEffect(() => {
    if (!stabilityKnown) return;
    const t = setTimeout(() => {
      void gatePreview(gate)
        // Merged onto the preview, never used in place of it: the gate response
        // carries only the stability fields, so adopting it wholesale left the
        // component with no `sizes` and crashed the step on load.
        .then((r) => setResult((prev) => (prev ? { ...prev, ...r } : prev)))
        .catch(() => {});
    }, 150);
    return () => clearTimeout(t);
  }, [gate, stabilityKnown, result?.k, gatePreview]);

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
                  <Tooltip
                    key={i}
                    width={330}
                    content={
                      <TitleListTooltip
                        heading={`Group ${i + 1} · ${size} ${status.item_noun}`}
                        subheading={TOOLTIP_SUBHEADING[status.tier]}
                        titles={result.titles?.[i] ?? []}
                        total={result.title_counts?.[i] ?? 0}
                        omitted={result.titles_omitted?.[i] ?? 0}
                      />
                    }
                  >
                    <span className="cursor-default rounded-sm bg-accent-bg px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-accent">
                      {size}
                    </span>
                  </Tooltip>
                ))}
              </div>
            )}
          </div>

          {result && result.k === k && (
            <div className="flex flex-wrap gap-x-6 gap-y-2 rounded-[10px] border border-border bg-panel px-4 py-3">
              <SizeStatRow stats={result} singletons={result.singletons} largest={result.largest} />
              {result.mean_stability != null && (
                <Stat label="Mean stability" value={result.mean_stability.toFixed(2)} />
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

              <StabilityGuidance
                noun={status.item_noun}
                gate={gate}
                pctRouted={result?.pct_routed ?? 0}
                meanStability={result?.mean_stability ?? null}
              />
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

          {clusters && (
            <ClusterList
              data={clusters}
              itemNoun={status.item_noun}
              onRename={onRename}
              onRenamed={refreshClusters}
            />
          )}
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

/**
 * How to read the stability numbers.
 *
 * Two parts, because they answer different questions. The one-line read is about
 * *this* cut and changes as the gate moves; the collapsible explains what the score
 * is at all. Without the second part the gate is a slider with no units — and the
 * defaults it ships with come from a real measurement, not a guess, which is worth
 * saying rather than leaving the user to discover by trial.
 */
function StabilityGuidance({
  noun,
  gate,
  pctRouted,
  meanStability,
}: {
  noun: string;
  gate: number;
  pctRouted: number;
  meanStability: number | null;
}) {
  const read =
    pctRouted === 0
      ? `Every ${noun.replace(/s$/, "")} sits above the gate: the grouping is stable and nothing needs a second opinion.`
      : pctRouted < 15
        ? `A small tail is uncertain. This is the usual shape — spend is low and the clusters that come back are largely the geometry's own.`
        : pctRouted < 40
          ? `A meaningful minority is uncertain, which is normal for a mixed workforce: cross-functional and generalist roles genuinely sit between groups.`
          : `Most items are uncertain at this gate. Either the cluster count is fighting the data, or the gate is set high enough to re-check assignments the model will simply confirm — try lowering it before paying for all of these.`;

  return (
    <div className="mt-3 space-y-2 border-t border-border pt-2.5">
      <p className="text-[11.5px] leading-snug text-text-secondary">
        {meanStability != null && (
          <>
            Mean stability <span className="font-bold tabular-nums text-text">{meanStability.toFixed(2)}</span>.{" "}
          </>
        )}
        {read}
      </p>

      <Collapsible title="How to read stability" subtitle="What the score measures, and where the default gate comes from">
        <div className="space-y-2 text-[11.5px] leading-relaxed text-text-secondary">
          <p>
            Each item's score is how often it lands with the <em>same peers</em> when the
            grouping is rebuilt from 50 random 90% samples of the data. 1.00 means it
            grouped with the same items every single time; 0.20 means its placement
            depends on which items happen to be present.
          </p>
          <p>
            It measures <strong>confidence in the placement, not quality of the role</strong>.
            A low score is not a data problem to fix — role descriptions in a single
            organisation sit at roughly 0.73–0.75 mean pairwise similarity, so genuinely
            cross-functional roles sit between groups no matter which algorithm is used.
            That tail is what the model is for.
          </p>
          <ul className="ml-4 list-disc space-y-1">
            <li>
              <strong>Above 0.80</strong> — unambiguous. Re-checking these wastes money;
              the model almost always confirms what the geometry already said.
            </li>
            <li>
              <strong>0.55–0.80</strong> — the useful band. Placement is defensible but not
              obvious, and a model re-check changes a real fraction of it.
            </li>
            <li>
              <strong>Below 0.55</strong> — genuinely ambiguous. Worth re-checking, and worth
              reading afterwards: a cluster of items that all route with low confidence
              usually means a group is <em>missing</em> from the taxonomy rather than
              mis-assigned.
            </li>
          </ul>
          <p>
            The default gate of <strong>0.58</strong> comes from a sweep across 0.55/0.60/0.70
            on a 2,736-role build: 0.70 sent far more items and changed almost nothing extra.
            Currently at <strong className="tabular-nums">{gate.toFixed(2)}</strong>.
          </p>
        </div>
      </Collapsible>
    </div>
  );
}

/**
 * The spread of cluster sizes. Quartiles rather than just mean and largest,
 * because the mean hides the shape: 40 clusters averaging 4 members looks the same
 * whether every cluster holds 4 or one holds 90 and the rest hold 1.
 *
 * Single-member clusters are reported as a plain count with no warning attached — a
 * job that genuinely has no near neighbours is a legitimate profile of one, not a
 * sign the cluster count is wrong.
 */
function SizeStatRow({
  stats,
  singletons,
  largest,
}: {
  stats: SizeStats;
  singletons: number;
  largest: number;
}) {
  return (
    <>
      {stats.smallest != null && <Stat label="Smallest" value={stats.smallest} />}
      {stats.size_p25 != null && <Stat label="p25" value={stats.size_p25} />}
      {stats.size_median != null && <Stat label="Median" value={stats.size_median} />}
      {stats.size_p75 != null && <Stat label="p75" value={stats.size_p75} />}
      <Stat label="Largest" value={largest} />
      {stats.size_mean != null && <Stat label="Mean" value={stats.size_mean} />}
      <Stat label="Single-item" value={singletons} />
    </>
  );
}

function ClusterList({
  data,
  itemNoun,
  onRename,
  onRenamed,
}: {
  data: TierClusters;
  itemNoun: string;
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
      {data.size_median != null && (
        <div className="flex flex-wrap gap-x-6 gap-y-2 rounded-[10px] border border-border bg-panel px-4 py-3">
          <SizeStatRow
            stats={data}
            singletons={data.singletons ?? 0}
            largest={data.largest ?? 0}
          />
        </div>
      )}
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
                // Hovering the name shows the underlying job titles — the question
                // you actually have when reading a generated cluster name is "what
                // is in here?", and expanding the row to find out loses your place
                // in a list of 150.
                <Tooltip
                  className="min-w-0 flex-1"
                  width={300}
                  content={
                    <TitleListTooltip
                      heading={`${c.size} ${itemNoun}`}
                      subheading={TOOLTIP_SUBHEADING[data.tier]}
                      titles={c.titles ?? []}
                      total={c.title_count ?? 0}
                      omitted={c.titles_omitted ?? 0}
                    />
                  }
                >
                  <button
                    onClick={() => {
                      setEditing(c.id);
                      setDraft(c.name);
                    }}
                    title="Click to rename"
                    className="block w-full truncate text-left text-[12.5px] font-semibold text-text hover:text-accent"
                  >
                    {c.name}
                  </button>
                </Tooltip>
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
