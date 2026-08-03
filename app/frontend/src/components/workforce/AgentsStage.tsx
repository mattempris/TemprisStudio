import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bot, Download, Search, Trash2, Upload, UserCheck } from "lucide-react";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Modal } from "../ui/Modal";
import { ProgressBar } from "../wizard/ProgressBar";
import { JobPulse } from "../wizard/JobPulse";
import { opportunityColor } from "../../lib/heat";
import { useJobStream } from "../../hooks/useJobStream";
import type { workforceApi } from "../../services/workforceApi";
import type {
  AgentCandidate,
  AgentCandidateReport,
  AgentDetail,
  AgentImpactReport,
} from "../../types/workforce";

/**
 * Step 6 — agent definitions.
 *
 * Task clusters ranked by the time an agent would release, and a full eight-section
 * engineering specification per agent. Each spec is two model calls at high effort, so
 * the threshold control sits next to the button — a bulk run over hundreds of clusters
 * is a real commitment of time.
 *
 * The ranking is automation × how much of the organisation's week the cluster consumes.
 * That deliberately puts a common, moderately automatable task above a rare, highly
 * automatable one — the opposite of what step 5 does with the same data, and the honest
 * order for something you have to build and then maintain.
 */

type Api = ReturnType<typeof workforceApi>;

export function AgentsStage({ api, onError }: { api: Api; onError: (m: string) => void }) {
  const [report, setReport] = useState<AgentCandidateReport | null>(null);
  const [impact, setImpact] = useState<AgentImpactReport | null>(null);
  const [threshold, setThreshold] = useState(0);
  const [domain, setDomain] = useState("");
  const [query, setQuery] = useState("");
  const [viewing, setViewing] = useState<AgentDetail | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [r, i] = await Promise.all([api.agentCandidates(0), api.agentImpact()]);
      setReport(r);
      setImpact(i);
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
    return report.clusters.filter(
      (c) =>
        c.time_released >= threshold &&
        (!domain || c.domain === domain) &&
        (!q || c.cluster.toLowerCase().includes(q) || c.category.toLowerCase().includes(q)),
    );
  }, [report, threshold, domain, query]);

  const pending = shown.filter((c) => !c.agent).length;

  const generate = async (body: { cluster_ids?: number[]; threshold?: number; redo?: boolean }) => {
    try {
      const h = await api.generateAgents(body);
      attach(h.job_id, h.stage);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  const upload = async (file: File) => {
    try {
      const r = await api.uploadCatalogue(file);
      if (r.truncated) {
        onError(
          `${r.filename} was truncated to fit the prompt — the first part is used, ` +
            `${r.chars.toLocaleString()} characters were supplied.`,
        );
      }
      await load();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  if (!report) return <p className="text-[12px] text-text-muted">Loading…</p>;

  return (
    <div className="space-y-3">
      <div className="rounded-[10px] border border-border bg-panel px-4 py-3">
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
          <Stat label="Candidate clusters" value={report.clusters.length} />
          <Stat label="Agents specified" value={report.total_agents} />
          {impact && impact.totals.agents > 0 && (
            <>
              <Stat
                label={`${report.unit} released`}
                value={impact.totals.time_released.toFixed(2)}
              />
              <Stat label="Need a person in the loop" value={impact.totals.supervised} />
            </>
          )}
        </div>
        <p className="mt-2 text-[11.5px] leading-snug text-text-secondary">
          Ranked by <strong className="text-text">automation × time consumed</strong>, in{" "}
          {report.unit} — so a common, moderately automatable task outranks a rare, highly
          automatable one. Each specification is two model calls at high
          effort, so a bulk run over many clusters takes a while.
        </p>
      </div>

      {/* Software catalogue: shared context, passed as a cached prompt prefix. */}
      <div className="rounded-[10px] border border-border bg-panel px-4 py-2.5">
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
            Software catalogue
          </span>
          <input
            ref={fileInput}
            type="file"
            accept=".pdf,.docx,.doc,.txt,.html,.htm"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void upload(f);
              e.target.value = "";
            }}
          />
          <Button onClick={() => fileInput.current?.click()}>
            <span className="flex items-center gap-1.5">
              <Upload size={12} /> Upload
            </span>
          </Button>
          {report.context_documents.map((d) => (
            <span
              key={d.id}
              className="flex items-center gap-1.5 rounded-[6px] border border-border bg-card px-2 py-1 text-[11px] text-text"
            >
              {d.filename}
              <span className="text-text-muted">{d.chars.toLocaleString()} chars</span>
              <button
                onClick={() =>
                  void api
                    .deleteCatalogue(d.id)
                    .then(load)
                    .catch((e) => onError(e instanceof Error ? e.message : String(e)))
                }
                aria-label={`Remove ${d.filename}`}
                className="text-text-muted transition-colors hover:text-brand"
              >
                <Trash2 size={11} />
              </button>
            </span>
          ))}
          {report.context_documents.length === 0 && (
            <span className="text-[11px] text-text-muted">
              Optional. Upload the systems this organisation actually runs and the specs will
              name them instead of describing a generic system.
            </span>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 rounded-[10px] border border-border bg-panel px-4 py-2.5">
        <span className="text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
          Only above
        </span>
        <input
          type="range"
          min={0}
          max={Math.max(1, Math.ceil((report.clusters[0]?.time_released ?? 1) * 100) / 100)}
          step={0.01}
          value={threshold}
          onChange={(e) => setThreshold(Number(e.target.value))}
          className="w-40 accent-[var(--color-accent)]"
        />
        <span className="text-[11.5px] font-semibold tabular-nums text-text">
          {threshold.toFixed(2)} {report.unit}
        </span>
        <select
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          className="rounded-[6px] border border-border bg-card px-2 py-1 text-[11.5px] text-text"
        >
          <option value="">All task domains</option>
          {report.domains.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
        <label className="flex min-w-[10rem] flex-1 items-center gap-2 rounded-[6px] border border-border bg-card px-2 py-1">
          <Search size={12} className="shrink-0 text-text-muted" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Find a cluster"
            className="min-w-0 flex-1 bg-transparent text-[11.5px] text-text outline-none placeholder:text-text-muted"
          />
        </label>
        <span className="text-[11px] text-text-muted">
          {shown.length} shown · {pending} without a spec
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2.5">
        {pending > 0 && (
          <Button
            variant="primary"
            onClick={() => void generate({ threshold, cluster_ids: shown.filter((c) => !c.agent).map((c) => c.cluster_id) })}
            disabled={job.running}
          >
            <span className="flex items-center gap-1.5">
              <Bot size={12} />
              Specify {pending} agent{pending === 1 ? "" : "s"}
            </span>
          </Button>
        )}
        <JobPulse job={job} />
      </div>

      {(job.running || job.summary || job.error) && <ProgressBar job={job} />}

      <div className="overflow-hidden rounded-[10px] border border-border">
        <div className="flex items-center gap-3 border-b border-border bg-panel px-3 py-1.5 text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
          <span className="min-w-0 flex-1">Task cluster</span>
          <span className="w-16 text-right">Automate</span>
          <span className="w-20 text-right">{report.unit} freed</span>
          <span className="w-32 text-right">Agent</span>
        </div>
        {shown.slice(0, 300).map((c) => (
          <CandidateRow
            key={c.cluster_id}
            candidate={c}
            unit={report.unit}
            busy={job.running}
            onGenerate={() => void generate({ cluster_ids: [c.cluster_id] })}
            onView={() =>
              c.agent &&
              void api
                .agent(c.agent.id)
                .then(setViewing)
                .catch((e) => onError(e instanceof Error ? e.message : String(e)))
            }
            downloadUrl={c.agent ? api.agentDownloadUrl(c.agent.id) : undefined}
          />
        ))}
      </div>

      {impact && impact.totals.agents > 0 && (
        <div className="rounded-[10px] border border-border bg-panel px-4 py-3">
          <p className="mb-1.5 text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
            Impact summary — prioritised by time released
          </p>
          {impact.agents.map((a, i) => (
            <div key={a.id} className="flex items-baseline gap-3 border-t border-border py-1.5">
              <span className="w-5 shrink-0 text-right text-[11px] tabular-nums text-text-muted">
                {i + 1}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[12px] font-semibold text-text">{a.name}</span>
                <span className="block text-[10.5px] leading-snug text-text-secondary">
                  {a.purpose}
                </span>
              </span>
              {a.human_in_the_loop && (
                <span
                  title="Needs a person to approve its output"
                  className="shrink-0 text-text-muted"
                >
                  <UserCheck size={12} />
                </span>
              )}
              <span className="w-20 shrink-0 text-right text-[11.5px] font-semibold tabular-nums text-text">
                {a.time_released.toFixed(2)}
              </span>
            </div>
          ))}
          <p className="mt-2 text-[11px] text-text-muted">
            {impact.totals.time_released.toFixed(2)} {impact.unit} across{" "}
            {impact.totals.agents} agent{impact.totals.agents === 1 ? "" : "s"} ·{" "}
            {impact.totals.supervised} need a person in the loop,{" "}
            {impact.totals.unsupervised} do not · mean automation{" "}
            {impact.totals.mean_automation}%. Estimates from the step 3 assessment, not
            measurements.
          </p>
        </div>
      )}

      {viewing && <SpecModal detail={viewing} onClose={() => setViewing(null)} api={api} />}
    </div>
  );
}

function CandidateRow({
  candidate: c,
  unit,
  busy,
  onGenerate,
  onView,
  downloadUrl,
}: {
  candidate: AgentCandidate;
  unit: string;
  busy: boolean;
  onGenerate: () => void;
  onView: () => void;
  downloadUrl?: string;
}) {
  return (
    <div className="flex items-center gap-3 border-b border-border px-3 py-1.5 last:border-0">
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[12px] font-semibold text-text">{c.cluster}</span>
        <span className="block truncate text-[10.5px] text-text-muted">
          {c.domain} › {c.category} · {c.roles} roles · {c.n_actions} actions
          {c.top_roles.length > 0 && ` · ${c.top_roles.join(", ")}`}
        </span>
      </span>
      <span className="w-16 text-right">
        <span
          className="inline-block min-w-[2.6rem] rounded-[5px] px-1.5 py-0.5 text-center text-[11px] font-bold tabular-nums text-white"
          style={{ background: opportunityColor(c.automation) }}
        >
          {c.automation.toFixed(0)}%
        </span>
      </span>
      <span className="w-20 text-right text-[11.5px] font-semibold tabular-nums text-text">
        {c.time_released.toFixed(2)}
      </span>
      <span className="flex w-32 shrink-0 items-center justify-end gap-1.5">
        {c.agent ? (
          <>
            {c.agent.human_in_the_loop && (
              <span title="Needs a person in the loop" className="text-text-muted">
                <UserCheck size={11} />
              </span>
            )}
            <Badge
              color="success"
              // Abbreviated to keep the row tight; the full word lives in the title so
              // the pill stays small without being a private code.
              title={`${c.agent.n_capabilities} capabilities specified for this agent`}
            >
              {c.agent.n_capabilities} caps
            </Badge>
            <button
              onClick={onView}
              className="rounded-[6px] border border-border bg-card px-1.5 py-0.5 text-[10.5px] font-semibold text-text transition-colors hover:bg-panel"
            >
              Spec
            </button>
            {downloadUrl && (
              <a
                href={downloadUrl}
                title={`${c.agent.name} specification`}
                className="rounded-[6px] border border-border bg-card px-1.5 py-0.5 text-[10.5px] font-semibold text-text transition-colors hover:bg-panel"
              >
                <Download size={10} />
              </a>
            )}
          </>
        ) : (
          <button
            onClick={onGenerate}
            disabled={busy}
            title={`Releases ${c.time_released.toFixed(2)} ${unit}`}
            className="flex items-center gap-1 rounded-[6px] border border-accent-border bg-accent-bg px-1.5 py-0.5 text-[10.5px] font-semibold text-accent transition-colors hover:bg-accent hover:text-white disabled:opacity-50"
          >
            <Bot size={10} /> Specify
          </button>
        )}
      </span>
    </div>
  );
}

/**
 * The eight sections, one collapsible each.
 *
 * Rendered as formatted JSON rather than as prose. The spec's audience is an engineer
 * who is going to build from it, the shape varies section to section, and a bespoke
 * renderer per section would be a lot of code that hides fields when the model returns
 * something unexpected. The download is the same content.
 */
function SpecModal({
  detail,
  onClose,
  api,
}: {
  detail: AgentDetail;
  onClose: () => void;
  api: Api;
}) {
  const [open, setOpen] = useState<string>("business_context");
  return (
    <Modal
      title={detail.name}
      subtitle={`${detail.n_capabilities} capabilities · ${detail.time_released.toFixed(2)} ${
        detail.time_released_unit
      } released · ${detail.automation_pct}% automatable${
        detail.human_in_the_loop ? " · needs a person in the loop" : " · runs unsupervised"
      }`}
      onClose={onClose}
      footer={
        <a
          href={api.agentDownloadUrl(detail.id)}
          className="flex items-center justify-center gap-1.5 text-[11.5px] font-bold text-accent hover:underline"
        >
          <Download size={12} /> Download {detail.slug}-agent-spec.json
        </a>
      }
    >
      <div className="space-y-2">
        <p className="text-[12px] leading-snug text-text-secondary">{detail.purpose}</p>
        {detail.sections.map((s) => (
          <div key={s} className="overflow-hidden rounded-[8px] border border-border">
            <button
              onClick={() => setOpen(open === s ? "" : s)}
              className="flex w-full items-center justify-between gap-2 bg-panel px-2.5 py-1.5 text-left text-[11.5px] font-bold text-text transition-colors hover:bg-card"
            >
              {s.replace(/_/g, " ")}
              <span className="text-text-muted">{open === s ? "−" : "+"}</span>
            </button>
            {open === s && (
              <pre className="overflow-x-auto bg-card px-2.5 py-2 font-mono text-[10px] leading-relaxed text-text">
                {JSON.stringify(detail.spec[s], null, 2)}
              </pre>
            )}
          </div>
        ))}
      </div>
    </Modal>
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
