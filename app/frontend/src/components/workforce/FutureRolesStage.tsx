import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Dumbbell, Search, Sparkles, Upload } from "lucide-react";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { ProgressBar } from "../wizard/ProgressBar";
import { JobPulse } from "../wizard/JobPulse";
import { useJobStream } from "../../hooks/useJobStream";
import type { workforceApi } from "../../services/workforceApi";
import type { FutureRoleCandidate, FutureRoleReport } from "../../types/workforce";

/**
 * Step 7 — future role design.
 *
 * Per role: the three-movement narrative, what it becomes, and what the person should be
 * getting better at. Ordered by how much of the week changes shape, because that is
 * where a redesign is most worth having.
 *
 * `deliberate_practice` is given its own block rather than being folded into the skills
 * list. It is the field that stops this being a deskilling document: someone who hands
 * every routine judgement to an agent cannot review that agent's work in two years, and
 * a redesign that does not say what to keep sharp has quietly caused that.
 */

type Api = ReturnType<typeof workforceApi>;

export function FutureRolesStage({ api, onError }: { api: Api; onError: (m: string) => void }) {
  const [report, setReport] = useState<FutureRoleReport | null>(null);
  const [family, setFamily] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setReport(await api.futureRoles());
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  }, [api, onError]);

  const { state: job, attach } = useJobStream(() => void load());

  useEffect(() => {
    void load();
  }, [load]);

  const shown = useMemo(() => {
    if (!report) return [];
    const q = query.trim().toLowerCase();
    return report.roles.filter(
      (r) => (!family || r.family === family) && (!q || r.title.toLowerCase().includes(q)),
    );
  }, [report, family, query]);

  const role: FutureRoleCandidate | null =
    shown.find((r) => r.profile_key === selected) ??
    report?.roles.find((r) => r.profile_key === selected) ??
    null;

  const design = async (body: { profile_keys?: string[]; limit?: number; redo?: boolean }) => {
    try {
      const h = await api.designFutureRoles(body);
      attach(h.job_id, h.stage);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  if (!report) return <p className="text-[12px] text-text-muted">Loading…</p>;
  const pending = shown.filter((r) => !r.design).length;
  const perRole =
    report.estimate_all.roles > 0 ? report.estimate_all.est_usd / report.estimate_all.roles : 0;

  return (
    <div className="space-y-3">
      <div className="rounded-[10px] border border-border bg-panel px-4 py-3">
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
          <Stat label="Roles affected" value={report.roles.length} />
          <Stat label="Designed" value={report.designed} />
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2.5">
          <input
            ref={fileInput}
            type="file"
            accept=".pdf,.docx,.doc,.txt,.html,.htm,.xlsx"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f)
                void api
                  .uploadStrategicContext(f)
                  .then(load)
                  .catch((err) => onError(err instanceof Error ? err.message : String(err)));
              e.target.value = "";
            }}
          />
          <Button onClick={() => fileInput.current?.click()}>
            <span className="flex items-center gap-1.5">
              <Upload size={12} /> Strategic context
            </span>
          </Button>
          {report.has_strategic_context ? (
            <Badge color="success">context supplied</Badge>
          ) : (
            <span className="text-[11px] text-text-muted">
              Optional: how the organisation wants freed-up time used. Folded into every
              design in a run as a cached prompt prefix.
            </span>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded-[10px] border border-border bg-panel px-4 py-2.5">
        <select
          value={family}
          onChange={(e) => setFamily(e.target.value)}
          className="rounded-[6px] border border-border bg-card px-2 py-1 text-[11.5px] text-text"
        >
          <option value="">All job families</option>
          {report.families.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
        <label className="flex min-w-[10rem] flex-1 items-center gap-2 rounded-[6px] border border-border bg-card px-2 py-1">
          <Search size={12} className="shrink-0 text-text-muted" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Find a role"
            className="min-w-0 flex-1 bg-transparent text-[11.5px] text-text outline-none placeholder:text-text-muted"
          />
        </label>
        {pending > 0 && (
          <Button
            variant="primary"
            onClick={() => void design({ profile_keys: shown.filter((r) => !r.design).map((r) => r.profile_key) })}
            disabled={job.running}
          >
            <span className="flex items-center gap-1.5">
              <Sparkles size={12} /> Design {pending} (~${(pending * perRole).toFixed(2)})
            </span>
          </Button>
        )}
        <JobPulse job={job} />
      </div>

      {(job.running || job.summary || job.error) && <ProgressBar job={job} />}

      <div className="grid gap-3 lg:grid-cols-[18rem_1fr]">
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
                <span className="block truncate text-[10.5px] text-text-muted">
                  {r.time_released_pct}% of the week changes shape
                </span>
              </span>
              {r.design && <Badge color="success">done</Badge>}
            </button>
          ))}
          {shown.length === 0 && (
            <p className="px-3 py-3 text-[12px] text-text-muted">No role matches that filter.</p>
          )}
        </div>

        <div className="min-w-0">
          {!role ? (
            <p className="rounded-[10px] border border-border bg-panel px-4 py-6 text-center text-[12px] text-text-muted">
              Pick a role to see how it is redesigned.
            </p>
          ) : !role.design ? (
            <div className="rounded-[10px] border border-border bg-panel px-4 py-4">
              <p className="text-[13px] font-bold text-text">{role.title}</p>
              <p className="mt-0.5 text-[11.5px] text-text-secondary">
                {role.family} › {role.category} · {role.automation}% automatable ·{" "}
                {role.time_released_pct}% of the week changes shape
              </p>
              {role.absorbed.length > 0 && (
                <p className="mt-1.5 text-[11.5px] text-text-secondary">
                  Changes shape first: {role.absorbed.join(", ")}
                </p>
              )}
              {role.agents.length > 0 && (
                <p className="mt-1 text-[11.5px] text-text-secondary">
                  Agents already specified against this work: {role.agents.join(", ")}
                </p>
              )}
              <div className="mt-2.5">
                <Button
                  variant="primary"
                  onClick={() => void design({ profile_keys: [role.profile_key] })}
                  disabled={job.running}
                >
                  <span className="flex items-center gap-1.5">
                    <Sparkles size={12} /> Design this role
                  </span>
                </Button>
              </div>
            </div>
          ) : (
            <Design role={role} onRedo={() => void design({ profile_keys: [role.profile_key], redo: true })} busy={job.running} />
          )}
        </div>
      </div>
    </div>
  );
}

function Design({
  role,
  onRedo,
  busy,
}: {
  role: FutureRoleCandidate;
  onRedo: () => void;
  busy: boolean;
}) {
  const d = role.design!;
  return (
    <div className="space-y-2.5">
      <div className="rounded-[10px] border border-border bg-panel px-4 py-3">
        <p className="text-[13px] font-bold text-text">{d.title}</p>
        <p className="mt-0.5 text-[11.5px] text-text-secondary">
          {role.family} › {role.category} · {d.automation_pct}% automatable ·{" "}
          {d.time_released_pct}% of the week changes shape
        </p>
        <p className="mt-1.5 text-[12px] font-semibold leading-snug text-accent">
          {d.future_purpose}
        </p>
        <div className="mt-2">
          <Button onClick={onRedo} disabled={busy}>
            Redesign
          </Button>
        </div>
      </div>

      {/* Three movements, laid out as three so the arc is legible without reading. */}
      <div className="grid gap-2 sm:grid-cols-3">
        {(
          [
            ["Today", d.evolution_today],
            ["First to change", d.evolution_after_automation],
            ["What it becomes", d.evolution_future],
          ] as const
        ).map(([label, text], i) => (
          <div key={label} className="rounded-[10px] border border-border bg-card px-3 py-2.5">
            <p className="mb-1 flex items-center gap-1 text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
              {label}
              {i < 2 && <ArrowRight size={9} />}
            </p>
            <p className="text-[11.5px] leading-snug text-text">{text}</p>
          </div>
        ))}
      </div>

      <div className="grid gap-2.5 sm:grid-cols-2">
        <List title="Future responsibilities" items={d.future_responsibilities} />
        <List title="Deepens rather than shrinks" items={d.deepened_tasks} />
        <List title="Changes shape first" items={d.absorbed_tasks} muted />
        <List title="Skills to build" items={d.skills_to_build} />
      </div>

      {/* Its own block, in accent, because it is the field that stops a redesign being
          a deskilling document — and it is the one a reader would otherwise skim. */}
      <div className="rounded-[10px] border border-accent-border bg-accent-bg px-3.5 py-2.5">
        <p className="mb-1 flex items-center gap-1.5 text-[10px] font-extrabold uppercase tracking-wider text-accent">
          <Dumbbell size={11} /> Keep sharp by hand
        </p>
        <ul className="space-y-1">
          {d.deliberate_practice.map((x, i) => (
            <li key={i} className="text-[11.5px] leading-snug text-text">
              · {x}
            </li>
          ))}
        </ul>
        <p className="mt-1.5 text-[10.5px] leading-snug text-text-secondary">
          Someone who hands every routine judgement to an agent cannot review that agent's
          work in two years. This is what keeps the role's judgement current.
        </p>
      </div>
    </div>
  );
}

function List({ title, items, muted }: { title: string; items: string[]; muted?: boolean }) {
  if (items.length === 0) return null;
  return (
    <div className="rounded-[10px] border border-border bg-panel px-3.5 py-2.5">
      <p className="mb-1 text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
        {title}
      </p>
      <ul className="space-y-0.5">
        {items.map((x, i) => (
          <li
            key={i}
            className={`text-[11.5px] leading-snug ${muted ? "text-text-secondary" : "text-text"}`}
          >
            · {x}
          </li>
        ))}
      </ul>
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
