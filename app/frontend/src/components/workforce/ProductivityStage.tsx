import { useCallback, useEffect, useMemo, useState } from "react";
import { Download, FileText, Play, Search, Sparkles } from "lucide-react";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Modal } from "../ui/Modal";
import { ProgressBar } from "../wizard/ProgressBar";
import { JobPulse } from "../wizard/JobPulse";
import { opportunityColor } from "../../lib/heat";
import { useJobStream } from "../../hooks/useJobStream";
import type { workforceApi } from "../../services/workforceApi";
import type {
  ProductivityReport,
  ProductivityRole,
  SkillDetail,
  SkillEstimate,
} from "../../types/workforce";

/**
 * Step 5 — personal productivity.
 *
 * Pick a role through job family › category › profile, see its tasks ordered by where
 * a prompt helps *that person* most, and generate a Claude Skill per task. The output
 * is a file, deliberately: the point is that someone downloads it, uploads it into
 * Claude, and uses it on Monday.
 *
 * The ordering here is augmentation × share of the week. Step 6 orders by automation,
 * and on the same data the two lists genuinely differ — which is the entire reason
 * step 3 scores two axes instead of one.
 */

type Api = ReturnType<typeof workforceApi>;

export function ProductivityStage({
  api,
  onError,
}: {
  api: Api;
  onError: (message: string) => void;
}) {
  const [report, setReport] = useState<ProductivityReport | null>(null);
  const [family, setFamily] = useState<string>("");
  const [category, setCategory] = useState<string>("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [estimate, setEstimate] = useState<SkillEstimate | null>(null);
  const [viewing, setViewing] = useState<SkillDetail | null>(null);

  const load = useCallback(async () => {
    try {
      setReport(await api.productivityRoles());
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  }, [api, onError]);

  const { state: job, attach } = useJobStream(() => void load());

  useEffect(() => {
    void load();
  }, [load]);

  const role: ProductivityRole | null = useMemo(
    () => report?.roles.find((r) => r.profile_key === selected) ?? null,
    [report, selected],
  );

  // The cost of the selected role's outstanding skills, fetched when the selection
  // changes rather than computed here — the server owns the token assumptions.
  useEffect(() => {
    if (!selected) {
      setEstimate(null);
      return;
    }
    let live = true;
    api
      .skillEstimate(selected)
      .then((e) => live && setEstimate(e))
      .catch(() => live && setEstimate(null));
    return () => {
      live = false;
    };
  }, [api, selected, report]);

  const categories = useMemo(() => {
    if (!report) return [];
    const pool = family ? report.roles.filter((r) => r.family === family) : report.roles;
    return Array.from(new Set(pool.map((r) => r.category))).sort();
  }, [report, family]);

  const shown = useMemo(() => {
    if (!report) return [];
    const q = query.trim().toLowerCase();
    return report.roles.filter(
      (r) =>
        (!family || r.family === family) &&
        (!category || r.category === category) &&
        (!q || r.title.toLowerCase().includes(q)),
    );
  }, [report, family, category, query]);

  const generate = async (body: { cluster_ids?: number[]; redo?: boolean }) => {
    if (!selected) return;
    try {
      const h = await api.generateSkills({ profile_key: selected, ...body });
      attach(h.job_id, h.stage);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  const view = (skillId: string) => {
    void api
      .skill(skillId)
      .then(setViewing)
      .catch((e) => onError(e instanceof Error ? e.message : String(e)));
  };

  if (!report) return <p className="text-[12px] text-text-muted">Loading…</p>;

  return (
    <div className="space-y-3">
      <div className="rounded-[10px] border border-border bg-panel px-4 py-3">
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
          <Stat label="Roles with assessed work" value={report.roles.length} />
          <Stat label="Rankable tasks" value={report.eligible_pairs} />
          <Stat label="Skills written" value={report.total_skills} />
        </div>
        <p className="mt-2 text-[11.5px] leading-snug text-text-secondary">
          Tasks are ordered by <strong className="text-text">augmentation × share of the
          week</strong> — where a prompt gives this person the most time back. That is a
          different order from the agent priority in step 6, deliberately: a task can be
          hard to automate and still be one AI helps most with.
        </p>
      </div>

      {/* family › category › profile, as the instructions ask */}
      <div className="flex flex-wrap items-center gap-2 rounded-[10px] border border-border bg-panel px-4 py-2.5">
        <span className="text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
          Filter
        </span>
        <select
          value={family}
          onChange={(e) => {
            setFamily(e.target.value);
            setCategory("");
          }}
          className="rounded-[6px] border border-border bg-card px-2 py-1 text-[11.5px] text-text"
        >
          <option value="">All job families</option>
          {report.families.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="rounded-[6px] border border-border bg-card px-2 py-1 text-[11.5px] text-text"
        >
          <option value="">All categories</option>
          {categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <label className="flex min-w-[12rem] flex-1 items-center gap-2 rounded-[6px] border border-border bg-card px-2 py-1">
          <Search size={12} className="shrink-0 text-text-muted" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Find a role"
            className="min-w-0 flex-1 bg-transparent text-[11.5px] text-text outline-none placeholder:text-text-muted"
          />
        </label>
        <span className="text-[11px] text-text-muted">{shown.length} roles</span>
      </div>

      <div className="grid gap-3 lg:grid-cols-[20rem_1fr]">
        <div className="max-h-[32rem] overflow-y-auto rounded-[10px] border border-border">
          {shown.slice(0, 300).map((r) => (
            <button
              key={r.profile_key}
              onClick={() => setSelected(r.profile_key)}
              className={`flex w-full items-center gap-2 border-b border-border px-3 py-1.5 text-left last:border-0 transition-colors ${
                selected === r.profile_key ? "bg-accent-bg" : "hover:bg-panel"
              }`}
            >
              <span className="min-w-0 flex-1">
                <span
                  className={`block truncate text-[12px] ${
                    selected === r.profile_key ? "font-bold text-accent" : "font-semibold text-text"
                  }`}
                >
                  {r.title}
                </span>
                <span className="block truncate text-[10.5px] text-text-muted">{r.category}</span>
              </span>
              {r.skills > 0 && (
                <Badge color="success">
                  {r.skills}
                </Badge>
              )}
            </button>
          ))}
          {shown.length === 0 && (
            <p className="px-3 py-3 text-[12px] text-text-muted">No role matches that filter.</p>
          )}
        </div>

        <div className="min-w-0">
          {!role ? (
            <p className="rounded-[10px] border border-border bg-panel px-4 py-6 text-center text-[12px] text-text-muted">
              Pick a role to see where a prompt helps that person most.
            </p>
          ) : (
            <div className="space-y-2.5">
              <div className="rounded-[10px] border border-border bg-panel px-4 py-3">
                <p className="text-[13px] font-bold text-text">{role.title}</p>
                <p className="mt-0.5 text-[11.5px] text-text-secondary">
                  {role.family} › {role.category} · {role.tasks.length} rankable tasks covering{" "}
                  {role.assessed_share}% of the week · {role.skills} skills written
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-2.5">
                  {estimate && estimate.skills > 0 && (
                    <Button variant="primary" onClick={() => void generate({})} disabled={job.running}>
                      <span className="flex items-center gap-1.5">
                        <Sparkles size={12} />
                        Write {estimate.skills} skill{estimate.skills === 1 ? "" : "s"}
                      </span>
                    </Button>
                  )}
                  {role.skills > 0 && (
                    <>
                      <Button onClick={() => void generate({ redo: true })} disabled={job.running}>
                        <span className="flex items-center gap-1.5">
                          <Play size={12} />
                          Rewrite all {role.tasks.length}
                        </span>
                      </Button>
                      <a
                        href={api.roleZipUrl(role.profile_key)}
                        className="flex items-center gap-1.5 rounded-[10px] border border-border bg-card px-3 py-2 text-[11px] font-bold text-text transition-colors hover:bg-panel"
                      >
                        <Download size={12} />
                        All {role.skills} as a zip
                      </a>
                    </>
                  )}
                  <JobPulse job={job} />
                </div>
                {estimate && estimate.skills === 0 && role.skills > 0 && (
                  <p className="mt-1.5 text-[11px] text-text-muted">
                    Every rankable task for this role has a skill.
                  </p>
                )}
              </div>

              {(job.running || job.summary || job.error) && <ProgressBar job={job} />}

              <div className="overflow-hidden rounded-[10px] border border-border">
                <div className="flex items-center gap-3 border-b border-border bg-panel px-3 py-1.5 text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
                  <span className="min-w-0 flex-1">Task</span>
                  <span className="w-14 text-right">% of week</span>
                  <span className="w-16 text-right">Augment</span>
                  <span className="w-16 text-right">Rank</span>
                  <span className="w-32" />
                </div>
                {role.tasks.map((t) => (
                  <div
                    key={t.cluster_id}
                    className="flex items-center gap-3 border-b border-border px-3 py-1.5 last:border-0"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[12px] font-semibold text-text">
                        {t.cluster}
                      </span>
                      <span className="block truncate text-[10.5px] text-text-muted">
                        {t.task_names.join(" · ")}
                      </span>
                    </span>
                    <span className="w-14 text-right text-[11.5px] tabular-nums text-text-secondary">
                      {t.proportion.toFixed(1)}
                    </span>
                    <span className="w-16 text-right">
                      <span
                        className="inline-block min-w-[2.6rem] rounded-[5px] px-1.5 py-0.5 text-center text-[11px] font-bold tabular-nums text-white"
                        style={{ background: opportunityColor(t.augmentation) }}
                      >
                        {t.augmentation.toFixed(0)}%
                      </span>
                    </span>
                    <span className="w-16 text-right text-[11.5px] font-semibold tabular-nums text-text">
                      {t.rank_score.toFixed(1)}
                    </span>
                    <span className="flex w-32 shrink-0 justify-end gap-1.5">
                      {t.skill ? (
                        <>
                          <button
                            onClick={() => view(t.skill!.id)}
                            title={t.skill.name}
                            className="flex items-center gap-1 rounded-[6px] border border-border bg-card px-1.5 py-0.5 text-[10.5px] font-semibold text-text transition-colors hover:bg-panel"
                          >
                            <FileText size={10} /> View
                          </button>
                          <a
                            href={api.skillDownloadUrl(t.skill.id)}
                            title={`${t.skill.name}.md`}
                            className="flex items-center gap-1 rounded-[6px] border border-border bg-card px-1.5 py-0.5 text-[10.5px] font-semibold text-text transition-colors hover:bg-panel"
                          >
                            <Download size={10} /> .md
                          </a>
                        </>
                      ) : (
                        <button
                          onClick={() => void generate({ cluster_ids: [t.cluster_id] })}
                          disabled={job.running}
                          className="flex items-center gap-1 rounded-[6px] border border-accent-border bg-accent-bg px-1.5 py-0.5 text-[10.5px] font-semibold text-accent transition-colors hover:bg-accent hover:text-white disabled:opacity-50"
                        >
                          <Sparkles size={10} /> Write
                        </button>
                      )}
                    </span>
                  </div>
                ))}
              </div>

              {role.tasks.some((t) => t.skill) && (
                <div className="space-y-1.5">
                  {role.tasks
                    .filter((t) => t.skill)
                    .map((t) => (
                      <div
                        key={t.cluster_id}
                        className="rounded-[10px] border border-border bg-panel px-3.5 py-2.5"
                      >
                        <p className="font-mono text-[11.5px] font-bold text-accent">
                          {t.skill!.name}.md
                        </p>
                        <p className="mt-0.5 text-[11.5px] leading-snug text-text">
                          {t.skill!.description}
                        </p>
                        <p className="mt-1 text-[11px] italic leading-snug text-text-secondary">
                          {t.skill!.hook}
                        </p>
                      </div>
                    ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {viewing && (
        <Modal
          title={`${viewing.name}.md`}
          subtitle={`${viewing.role_title} · ${viewing.cluster_name}`}
          onClose={() => setViewing(null)}
          footer={
            <a
              href={api.skillDownloadUrl(viewing.id)}
              className="flex items-center justify-center gap-1.5 text-[11.5px] font-bold text-accent hover:underline"
            >
              <Download size={12} /> Download {viewing.name}.md
            </a>
          }
        >
          {/* Rendered as the file, not as prose: what the person uploads is exactly
              this text, so showing it verbatim is showing them the artefact. */}
          <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-text">
            {viewing.markdown}
          </pre>
        </Modal>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-[15px] font-bold tabular-nums text-accent">
        {typeof value === "number" ? value.toLocaleString() : value}
      </span>
      <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-muted">
        {label}
      </span>
    </span>
  );
}
