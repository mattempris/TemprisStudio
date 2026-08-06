import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ChevronRight, Play, Search, UserCheck } from "lucide-react";
import type {
  MatchBrowse,
  MatchBrowseRow,
  TaxonomyMatch,
  TaxonomySearchHit,
} from "../../types/pipeline";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { cn } from "../../lib/cn";

/**
 * Step 11 — anchor roles matched into the 3rd-party taxonomy.
 *
 * Two views, because they answer different questions:
 *   Structure — the client's profiles arranged under the external taxonomy's own
 *     Family › Sub-family › Specialisation tree — the vendor spells its own tier
 *     "Specialization"; our copy does not. This is the deliverable: where
 *     the organisation sits in the market structure.
 *   Review — only the matches the pipeline is unsure about, with the shortlist
 *     it chose from and an override control. Ordered worst-first.
 *
 * The review view exists because a low-confidence match is a finding, not a
 * failure. Hiding it behind a single "94% matched" number would be the easy and
 * wrong thing to build.
 */

interface Props {
  browse: () => Promise<MatchBrowse>;
  matches: (reviewOnly: boolean) => Promise<{ matches: TaxonomyMatch[] }>;
  search: (q: string) => Promise<{ results: TaxonomySearchHit[] }>;
  override: (profileKey: string, specCode: string, levelCode?: string | null) => Promise<unknown>;
  industries: string[];
  allIndustries: string[];
  onRun: (industries: string[]) => void;
  running: boolean;
  hasResults: boolean;
}

export function MatchingPanel({
  browse,
  matches,
  search,
  override,
  industries,
  allIndustries,
  onRun,
  running,
  hasResults,
}: Props) {
  const [tab, setTab] = useState<"structure" | "review">("structure");
  const [data, setData] = useState<MatchBrowse | null>(null);
  const [review, setReview] = useState<TaxonomyMatch[]>([]);
  const [picked, setPicked] = useState<string[]>(industries);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!hasResults) return;
    try {
      const [b, r] = await Promise.all([browse(), matches(true)]);
      setData(b);
      setReview(r.matches);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [browse, matches, hasResults]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const reviewCount = review.length;

  return (
    <div className="space-y-4">
      <div className="space-y-2.5 rounded-[10px] border border-border bg-panel px-4 py-3">
        <p className="text-[11px] font-extrabold uppercase tracking-wider text-text-muted">
          Industry scope
        </p>
        <p className="text-[11.5px] leading-snug text-text-secondary">
          Narrowing to the client's own sectors sharpens matches and shrinks the index.
          Cross-industry roles are always included.
        </p>
        <div className="flex flex-wrap gap-1.5">
          {allIndustries
            .filter((i) => i !== "Cross Industry")
            .map((ind) => {
              const on = picked.includes(ind);
              return (
                <button
                  key={ind}
                  onClick={() =>
                    setPicked((p) => (on ? p.filter((x) => x !== ind) : [...p, ind]))
                  }
                  className={cn(
                    "rounded-full border px-2.5 py-1 text-[10.5px] font-bold transition-colors",
                    on
                      ? "border-accent-border bg-accent-bg text-accent"
                      : "border-border bg-card text-text-muted hover:text-text",
                  )}
                >
                  {ind}
                </button>
              );
            })}
        </div>
        <Button variant="primary" onClick={() => onRun(picked)} disabled={running}>
          <span className="flex items-center gap-1.5">
            <Play size={12} />
            {hasResults ? "Re-run matching" : "Match anchor roles into the taxonomy"}
            {picked.length > 0 && ` (${picked.length} industries)`}
          </span>
        </Button>
      </div>

      {error && (
        <div className="rounded-[10px] border border-brand-border bg-brand-bg px-3 py-2 text-[12px]">
          {error}
        </div>
      )}

      {data && (
        <>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 rounded-[10px] bg-panel px-4 py-2.5">
            <Stat label="Matched" value={`${data.summary.matched ?? 0}`} />
            <Stat label="Families" value={`${data.summary.families ?? 0}`} />
            <Stat label="Mean confidence" value={fmtPct(data.summary.mean_confidence)} />
            {(data.summary.overridden ?? 0) > 0 && (
              <Stat label="User-set" value={`${data.summary.overridden}`} />
            )}
          </div>

          <div className="flex gap-1 rounded-[10px] bg-panel p-1">
            <Tab active={tab === "structure"} onClick={() => setTab("structure")}>
              Taxonomy structure
            </Tab>
            <Tab active={tab === "review"} onClick={() => setTab("review")}>
              Needs review
              {reviewCount > 0 && (
                <Badge color="warning" className="ml-1.5">
                  {reviewCount}
                </Badge>
              )}
            </Tab>
          </div>

          {tab === "structure" ? (
            <StructureView data={data} />
          ) : (
            <ReviewView
              items={review}
              search={search}
              onOverride={async (k, code, level) => {
                await override(k, code, level);
                await refresh();
              }}
            />
          )}
        </>
      )}
    </div>
  );
}

function Tab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center rounded-[8px] px-3.5 py-1.5 text-[12px] font-semibold transition-colors",
        active ? "bg-card text-accent shadow-card" : "text-text-muted hover:text-text",
      )}
    >
      {children}
    </button>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-[15px] font-bold tabular-nums text-accent">{value}</span>
      <span className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">
        {label}
      </span>
    </span>
  );
}

function fmtPct(v: number | null | undefined): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

function StructureView({ data }: { data: MatchBrowse }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const toggle = (k: string) => setOpen((o) => ({ ...o, [k]: !o[k] }));

  return (
    <div className="space-y-2">
      <ul className="space-y-1.5">
        {data.families.map((fam) => (
          <li key={fam.name} className="overflow-hidden rounded-[10px] border border-border bg-card">
            <TreeRow
              depth={0}
              name={fam.name}
              count={fam.profile_count}
              headcount={data.has_headcount ? fam.headcount : null}
              review={fam.needs_review}
              expanded={!!open[fam.name]}
              onToggle={() => toggle(fam.name)}
            />
            {open[fam.name] &&
              (fam.sub_families ?? []).map((sub) => {
                const sk = `${fam.name}/${sub.name}`;
                return (
                  <div key={sk} className="border-t border-border">
                    <TreeRow
                      depth={1}
                      name={sub.name}
                      count={sub.profile_count}
                      headcount={data.has_headcount ? sub.headcount : null}
                      review={sub.needs_review}
                      expanded={!!open[sk]}
                      onToggle={() => toggle(sk)}
                    />
                    {open[sk] &&
                      (sub.specializations ?? []).map((spec) => (
                        <div key={spec.code} className="border-t border-border bg-panel/40 px-4 py-2.5">
                          <p className="text-[12px] font-semibold text-text">
                            {spec.title}
                            <span className="ml-2 font-mono text-[10px] font-normal text-text-muted">
                              {spec.code}
                            </span>
                          </p>
                          <ul className="mt-1.5 space-y-1">
                            {spec.profiles.map((p) => (
                              <ProfileLine key={p.profile_key} row={p} showHeadcount={data.has_headcount} />
                            ))}
                          </ul>
                        </div>
                      ))}
                  </div>
                );
              })}
          </li>
        ))}
      </ul>

      {data.unmatched.length > 0 && (
        <div className="rounded-[10px] border border-warning-border bg-warning-bg px-4 py-3">
          <p className="flex items-center gap-1.5 text-[12px] font-bold text-warning">
            <AlertTriangle size={13} />
            {data.unmatched.length} profile{data.unmatched.length === 1 ? "" : "s"} with no
            defensible match
          </p>
          <p className="mt-1 text-[11.5px] leading-snug text-text-secondary">
            The taxonomy has no bucket for these roles. That is a coverage finding about the
            external taxonomy, not an error — record it or set a match manually under Needs review.
          </p>
          <ul className="mt-2 space-y-1">
            {data.unmatched.map((p) => (
              <li key={p.profile_key} className="text-[12px] text-text">
                {p.profile_title}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ProfileLine({ row, showHeadcount }: { row: MatchBrowseRow; showHeadcount: boolean }) {
  return (
    <li className="flex items-center gap-2 text-[12px]">
      <span className="min-w-0 flex-1 truncate text-text">{row.profile_title}</span>
      {row.overridden_by_user && (
        <span title="Set by a user, not the pipeline">
          <UserCheck size={12} className="text-success" />
        </span>
      )}
      {row.level_code && (
        <Badge color={streamColor(row.level_stream)}>{row.level_code}</Badge>
      )}
      {showHeadcount && row.headcount != null && (
        <span className="tabular-nums text-[11px] text-text-muted">{row.headcount} ppl</span>
      )}
      <span className="w-9 text-right tabular-nums text-[11px] text-text-muted">
        {fmtPct(row.confidence)}
      </span>
    </li>
  );
}

function streamColor(stream: string | null): "brand" | "accent" | "teal" | "purple" {
  if (stream === "Executive") return "brand";
  if (stream === "Management") return "purple";
  if (stream === "Professional") return "accent";
  return "teal";
}

function TreeRow({
  depth,
  name,
  count,
  headcount,
  review,
  expanded,
  onToggle,
}: {
  depth: number;
  name: string;
  count: number;
  headcount: number | null;
  review: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      onClick={onToggle}
      className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-panel"
      style={{ paddingLeft: `${12 + depth * 18}px` }}
    >
      <ChevronRight
        size={13}
        className={cn("shrink-0 text-text-muted transition-transform", expanded && "rotate-90")}
      />
      <span
        className={cn(
          "min-w-0 flex-1 truncate",
          depth === 0 ? "text-[13px] font-semibold text-text" : "text-[12.5px] text-text-secondary",
        )}
      >
        {name}
      </span>
      {review > 0 && <Badge color="warning">{review} to review</Badge>}
      {headcount != null && (
        <span className="tabular-nums text-[11px] text-text-muted">{headcount} ppl</span>
      )}
      <span className="tabular-nums text-[12px] font-bold text-text">{count}</span>
    </button>
  );
}

const REASON_TEXT: Record<string, string> = {
  no_match: "No candidate was a defensible match",
  low_confidence: "The reranker was not confident",
  weak_shortlist: "Nothing in the taxonomy is semantically close",
  low_level_confidence: "The career level is uncertain",
  invalid_level: "The model returned a level this specialisation does not offer",
};

function ReviewView({
  items,
  search,
  onOverride,
}: {
  items: TaxonomyMatch[];
  search: (q: string) => Promise<{ results: TaxonomySearchHit[] }>;
  onOverride: (profileKey: string, specCode: string, levelCode?: string | null) => Promise<void>;
}) {
  const sorted = useMemo(
    () => [...items].sort((a, b) => Number(a.matched) - Number(b.matched) || a.confidence - b.confidence),
    [items],
  );

  if (!sorted.length) {
    return (
      <p className="rounded-[10px] bg-success-bg px-4 py-3 text-[12.5px] text-text">
        Every match cleared the confidence threshold. Nothing needs review.
      </p>
    );
  }

  return (
    <ul className="space-y-2">
      {sorted.map((m) => (
        <ReviewCard key={m.profile_key} match={m} search={search} onOverride={onOverride} />
      ))}
    </ul>
  );
}

function ReviewCard({
  match,
  search,
  onOverride,
}: {
  match: TaxonomyMatch;
  search: (q: string) => Promise<{ results: TaxonomySearchHit[] }>;
  onOverride: (profileKey: string, specCode: string, levelCode?: string | null) => Promise<void>;
}) {
  const [picking, setPicking] = useState(false);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<TaxonomySearchHit[]>([]);
  const [chosen, setChosen] = useState<TaxonomySearchHit | null>(null);
  const [level, setLevel] = useState<string>("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (q.trim().length < 2) {
      setHits([]);
      return;
    }
    // Debounced so typing a specialisation name doesn't fire a request per key.
    const t = setTimeout(() => {
      void search(q).then((r) => setHits(r.results)).catch(() => setHits([]));
    }, 250);
    return () => clearTimeout(t);
  }, [q, search]);

  return (
    <li className="rounded-[10px] border border-warning-border bg-card">
      <div className="border-l-[3px] border-warning px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[13px] font-semibold text-text">{match.profile_title}</p>
            <p className="mt-0.5 text-[11.5px] text-text-secondary">
              {match.matched ? (
                <>
                  {match.family_title} › {match.sub_family_title} ›{" "}
                  <span className="font-semibold text-text">{match.spec_title}</span>
                </>
              ) : (
                <span className="italic text-text-muted">unmatched</span>
              )}
            </p>
          </div>
          <div className="shrink-0 text-right">
            <p className="text-[15px] font-bold tabular-nums text-warning">
              {fmtPct(match.confidence)}
            </p>
            {match.cosine != null && (
              <p className="text-[10px] tabular-nums text-text-muted">
                cos {match.cosine.toFixed(2)}
              </p>
            )}
          </div>
        </div>

        <div className="mt-2 flex flex-wrap gap-1.5">
          {match.review_reasons.map((r) => (
            <Badge key={r} color="warning">
              {REASON_TEXT[r] ?? r}
            </Badge>
          ))}
        </div>

        {match.rationale && (
          <p className="mt-2 rounded-[8px] bg-panel px-3 py-2 text-[11.5px] leading-snug text-text-secondary">
            {match.rationale}
          </p>
        )}

        {match.runner_up_title && (
          <p className="mt-1.5 text-[11.5px] text-text-muted">
            Runner-up: {match.runner_up_title}
          </p>
        )}

        {match.shortlist.length > 0 && (
          <details className="mt-2">
            <summary className="cursor-pointer text-[11px] font-bold text-accent">
              What it chose from ({match.shortlist.length} candidates)
            </summary>
            <ul className="mt-1.5 space-y-0.5">
              {match.shortlist.map((c) => (
                <li key={c.code} className="flex items-center gap-2 text-[11.5px]">
                  <span className="w-10 shrink-0 tabular-nums text-text-muted">
                    {c.cosine.toFixed(2)}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-text-secondary">
                    {c.family_title} › {c.title}
                  </span>
                  <button
                    onClick={() =>
                      void onOverride(match.profile_key, c.code, null)
                    }
                    className="shrink-0 text-[10.5px] font-bold text-accent hover:underline"
                  >
                    use this
                  </button>
                </li>
              ))}
            </ul>
          </details>
        )}

        <div className="mt-2.5">
          {!picking ? (
            <button
              onClick={() => setPicking(true)}
              className="flex items-center gap-1.5 text-[11px] font-bold text-accent hover:underline"
            >
              <Search size={11} /> Search the full taxonomy
            </button>
          ) : (
            <div className="space-y-2 rounded-[8px] border border-border bg-panel p-3">
              <input
                autoFocus
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search specialisations and typical titles…"
                className="w-full rounded-[8px] border border-border bg-card px-3 py-1.5 text-[12px] text-text outline-none focus:border-accent"
              />
              {hits.length > 0 && !chosen && (
                <ul className="max-h-48 space-y-0.5 overflow-y-auto">
                  {hits.map((h) => (
                    <li key={h.code}>
                      <button
                        onClick={() => {
                          setChosen(h);
                          setLevel(h.levels[0]?.code ?? "");
                        }}
                        className="w-full rounded-[6px] px-2 py-1 text-left text-[11.5px] hover:bg-card"
                      >
                        <span className="font-semibold text-text">{h.title}</span>
                        <span className="ml-1.5 text-text-muted">
                          {h.family_title} › {h.sub_family_title}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {chosen && (
                <div className="space-y-2">
                  <p className="text-[12px] text-text">
                    <span className="font-semibold">{chosen.title}</span>{" "}
                    <span className="text-text-muted">
                      {chosen.family_title} › {chosen.sub_family_title}
                    </span>
                  </p>
                  <label className="flex items-center gap-2 text-[11.5px] text-text-secondary">
                    Career level
                    <select
                      value={level}
                      onChange={(e) => setLevel(e.target.value)}
                      className="rounded-[6px] border border-border bg-card px-2 py-1 text-[11.5px] text-text"
                    >
                      {chosen.levels.map((l) => (
                        <option key={l.code} value={l.code}>
                          {l.title}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="flex gap-2">
                    <Button
                      variant="primary"
                      disabled={saving}
                      onClick={async () => {
                        setSaving(true);
                        try {
                          await onOverride(match.profile_key, chosen.code, level || null);
                          setPicking(false);
                          setChosen(null);
                          setQ("");
                        } finally {
                          setSaving(false);
                        }
                      }}
                    >
                      Set this match
                    </Button>
                    <Button onClick={() => setChosen(null)}>Back</Button>
                  </div>
                </div>
              )}
              {!chosen && (
                <Button
                  onClick={() => {
                    setPicking(false);
                    setQ("");
                  }}
                >
                  Cancel
                </Button>
              )}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}
