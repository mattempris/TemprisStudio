import { useState } from "react";
import { BarChart3, ChevronRight, FileDown, FileText, X } from "lucide-react";
import type { JEDetail, ProfileRow } from "../../types/pipeline";
import { cn } from "../../lib/cn";

/**
 * Aggregate-first Job Evaluation browser.
 *
 * This is the UX fix instructions.txt calls for ("implement improvements to the
 * way JE results are browsed i.e. hide the detail below job level aggregate
 * results"). The legacy reference (Insurance Demo's levelling.html) exposes all
 * 3 personas x 4 domains x 5 sub-factors — 60 scored rows plus 12 rationale
 * paragraphs — on the first click of a role row, with no stop at the aggregate.
 *
 * Here there are three deliberate steps:
 *   1. this list: title, level, aggregate score, uncertainty spread only
 *   2. the drawer: domain rollups (opened by a distinct secondary control, NOT
 *      the row's main click target, which opens the profile document instead)
 *   3. per-domain toggle: the individual sub-factor scores and rationale
 */
interface Props {
  profiles: ProfileRow[];
  onOpenProfile: (key: string) => void;
  loadJe: (key: string) => Promise<JEDetail>;
  exportUrl: (key: string, fmt: "html" | "docx" | "pdf") => string;
  pdfAvailable: boolean;
}

export function JEResultsBrowser({ profiles, onOpenProfile, loadJe, exportUrl, pdfAvailable }: Props) {
  const [detail, setDetail] = useState<JEDetail | null>(null);
  const [loadingKey, setLoadingKey] = useState<string | null>(null);

  const scored = profiles.filter((p) => p.has_je && p.aggregate_score !== undefined);
  const bounds = scored.length
    ? {
        min: Math.min(...scored.map((p) => p.spread_low ?? p.aggregate_score!)),
        max: Math.max(...scored.map((p) => p.spread_high ?? p.aggregate_score!)),
      }
    : { min: 0, max: 100 };
  const span = Math.max(1, bounds.max - bounds.min);

  async function openDetail(key: string) {
    setLoadingKey(key);
    try {
      setDetail(await loadJe(key));
    } finally {
      setLoadingKey(null);
    }
  }

  return (
    <div>
      <table className="w-full border-collapse">
        <thead>
          <tr>
            {["Anchor role", "Level", "Score", "Range", ""].map((h, i) => (
              <th
                key={h || i}
                className={cn(
                  "border-b border-border pb-2 text-[10px] font-extrabold uppercase tracking-wider text-text-muted",
                  i >= 2 ? "text-right" : "text-left",
                )}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {profiles.map((p) => (
            <tr key={p.profile_key} className="border-b border-border last:border-0">
              {/* primary click target = the profile document, not the JE detail */}
              <td className="py-3 pr-3">
                <button
                  onClick={() => onOpenProfile(p.profile_key)}
                  className="group text-left"
                  title="Open the role profile document"
                >
                  <span className="flex items-center gap-1.5 text-[13px] font-semibold text-text group-hover:text-accent">
                    <FileText size={13} className="shrink-0 text-text-muted group-hover:text-accent" />
                    {p.title}
                  </span>
                  {p.breadcrumb.length > 0 && (
                    <span className="mt-0.5 block text-[11px] text-text-muted">
                      {p.breadcrumb.join(" › ")}
                    </span>
                  )}
                </button>
              </td>

              <td className="py-3 pr-3">
                {p.level_name ? (
                  <span className="inline-block rounded-full border border-accent-border bg-accent-bg px-2 py-0.5 text-[10px] font-bold text-accent">
                    {p.level_name}
                  </span>
                ) : (
                  <span className="text-[11px] text-text-muted">—</span>
                )}
              </td>

              <td className="py-3 pr-3 text-right text-[13px] font-bold tabular-nums text-text">
                {p.aggregate_score?.toFixed(1) ?? "—"}
              </td>

              {/* uncertainty band: Harsh--Generous with the Balanced tick */}
              <td className="py-3 pr-3">
                {p.spread_low !== undefined && p.spread_high !== undefined ? (
                  <div className="ml-auto w-28">
                    <div className="relative h-2 rounded-full bg-panel">
                      <div
                        className="absolute h-2 rounded-full bg-accent-bg"
                        style={{
                          left: `${((p.spread_low - bounds.min) / span) * 100}%`,
                          width: `${Math.max(2, ((p.spread_high - p.spread_low) / span) * 100)}%`,
                        }}
                      />
                      <div
                        className="absolute top-[-2px] h-3 w-[2px] rounded bg-accent"
                        style={{ left: `${((p.aggregate_score! - bounds.min) / span) * 100}%` }}
                      />
                    </div>
                    <div className="mt-1 text-right text-[10px] tabular-nums text-text-muted">
                      {p.spread_low.toFixed(0)}–{p.spread_high.toFixed(0)}
                    </div>
                  </div>
                ) : null}
              </td>

              <td className="py-3 text-right">
                {p.has_je && (
                  /* visually distinct secondary control — the deliberate second step */
                  <button
                    onClick={() => openDetail(p.profile_key)}
                    disabled={loadingKey === p.profile_key}
                    className="inline-flex items-center gap-1 rounded-[7px] border border-border bg-card px-2 py-1 text-[10.5px] font-bold text-text-secondary hover:border-text-muted disabled:opacity-50"
                  >
                    <BarChart3 size={11} />
                    {loadingKey === p.profile_key ? "Loading…" : "Scoring detail"}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {detail && (
        <JEDetailDrawer
          detail={detail}
          onClose={() => setDetail(null)}
          exportUrl={exportUrl}
          pdfAvailable={pdfAvailable}
        />
      )}
    </div>
  );
}

/** Slide-over: domain rollups by default, sub-factors behind a per-domain toggle. */
function JEDetailDrawer({
  detail,
  onClose,
  exportUrl,
  pdfAvailable,
}: {
  detail: JEDetail;
  onClose: () => void;
  exportUrl: (key: string, fmt: "html" | "docx" | "pdf") => string;
  pdfAvailable: boolean;
}) {
  const [openDomains, setOpenDomains] = useState<Set<string>>(new Set());
  const [persona, setPersona] = useState<"Balanced" | "Generous" | "Harsh">("Balanced");

  const toggle = (name: string) =>
    setOpenDomains((prev) => {
      const next = new Set(prev);
      next.has(name) ? next.delete(name) : next.add(name);
      return next;
    });

  const rollups = detail.domain_rollups[persona] ?? {};

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button className="flex-1 bg-black/20" onClick={onClose} aria-label="Close" />
      <aside className="w-full max-w-xl overflow-y-auto border-l border-border bg-card shadow-modal">
        <header className="sticky top-0 flex items-start justify-between gap-3 border-b border-border bg-card px-5 py-4">
          <div>
            <p className="text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
              Job evaluation detail
            </p>
            <h3 className="mt-0.5 text-[16px] font-bold text-text">{detail.profile_key}</h3>
            <div className="mt-2 flex items-center gap-3">
              <span className="text-[20px] font-bold tabular-nums text-accent">
                {detail.aggregate_score.toFixed(1)}
              </span>
              <span className="rounded-full border border-accent-border bg-accent-bg px-2 py-0.5 text-[10px] font-bold text-accent">
                {detail.level_name}
              </span>
            </div>
          </div>
          <button onClick={onClose} className="rounded p-1 text-text-muted hover:text-text">
            <X size={17} />
          </button>
        </header>

        <div className="px-5 py-4">
          {/* persona switch — the ensemble's three voices */}
          <div className="mb-4 flex items-center gap-1 rounded-[8px] bg-panel p-0.5">
            {(["Harsh", "Balanced", "Generous"] as const).map((p) => (
              <button
                key={p}
                onClick={() => setPersona(p)}
                className={cn(
                  "flex-1 rounded-[6px] px-3 py-1.5 text-[12px] font-medium transition-colors",
                  persona === p ? "bg-card font-semibold text-accent shadow-card" : "text-text-muted",
                )}
              >
                {p}
                <span className="ml-1.5 tabular-nums opacity-70">
                  {detail.persona_scores[p]?.toFixed(0)}
                </span>
              </button>
            ))}
          </div>

          <p className="mb-3 text-[12px] text-text-secondary">
            Weighted contribution by domain. Expand a domain for its individual sub-factor scores.
          </p>

          {detail.framework.domains.map((domain) => {
            const isOpen = openDomains.has(domain.name);
            const scores = detail.personas[persona]?.[domain.name] ?? {};
            const rationale = String(scores["Rationale"] ?? "");
            return (
              <div key={domain.name} className="mb-2 rounded-[10px] border border-border">
                <button
                  onClick={() => toggle(domain.name)}
                  className="flex w-full items-center gap-3 px-4 py-3 text-left"
                >
                  <ChevronRight
                    size={14}
                    className={cn("shrink-0 text-text-muted transition-transform", isOpen && "rotate-90")}
                  />
                  <span className="flex-1 text-[13px] font-semibold text-text">{domain.name}</span>
                  <span className="text-[10.5px] text-text-muted">weight {domain.weight}%</span>
                  <span className="w-14 text-right text-[13px] font-bold tabular-nums text-accent">
                    {(rollups[domain.name] ?? 0).toFixed(1)}
                  </span>
                </button>

                {isOpen && (
                  <div className="border-t border-border px-4 py-3">
                    {rationale && (
                      <p className="mb-3 rounded-[8px] border-l-[3px] border-accent bg-accent-bg px-3 py-2 text-[12px] leading-relaxed text-text">
                        {rationale}
                      </p>
                    )}
                    <ul>
                      {domain.subdomains.map((sub) => {
                        const raw = Number(scores[sub.name] ?? 0);
                        return (
                          <li
                            key={sub.name}
                            className="flex items-center gap-3 border-b border-border py-2 last:border-0"
                          >
                            <span className="flex-1 text-[12px] text-text-secondary">{sub.name}</span>
                            <span className="flex items-center gap-0.5">
                              {[1, 2, 3, 4, 5].map((n) => (
                                <span
                                  key={n}
                                  className={cn(
                                    "h-3.5 w-2 rounded-sm",
                                    n <= raw ? "bg-accent" : "bg-border",
                                  )}
                                />
                              ))}
                            </span>
                            <span className="w-6 text-right text-[12px] font-bold tabular-nums text-text">
                              {raw}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                )}
              </div>
            );
          })}

          <div className="mt-5 flex flex-wrap gap-2 border-t border-border pt-4">
            <a
              href={exportUrl(detail.profile_key, "docx")}
              className="inline-flex items-center gap-1.5 rounded-[7px] border border-border bg-card px-3 py-2 text-[11px] font-bold text-text-secondary hover:bg-panel"
            >
              <FileDown size={12} /> DocX
            </a>
            <a
              href={pdfAvailable ? exportUrl(detail.profile_key, "pdf") : undefined}
              aria-disabled={!pdfAvailable}
              title={pdfAvailable ? "Download PDF" : "PDF export needs WeasyPrint's native libraries on this host"}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-[7px] border px-3 py-2 text-[11px] font-bold",
                pdfAvailable
                  ? "border-border bg-card text-text-secondary hover:bg-panel"
                  : "pointer-events-none border-border bg-panel text-text-muted opacity-60",
              )}
            >
              <FileDown size={12} /> PDF
            </a>
            <a
              href={exportUrl(detail.profile_key, "html")}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 rounded-[7px] border border-border bg-card px-3 py-2 text-[11px] font-bold text-text-secondary hover:bg-panel"
            >
              <FileText size={12} /> HTML
            </a>
          </div>

          {detail.stale && (
            <p className="mt-3 rounded-[8px] border-l-[3px] border-warning bg-warning-bg px-3 py-2 text-[11.5px] text-text">
              This evaluation was computed under an earlier job evaluation framework or clustering. Re-run it
              to bring it up to date.
            </p>
          )}
        </div>
      </aside>
    </div>
  );
}
