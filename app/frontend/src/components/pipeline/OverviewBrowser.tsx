import { useState } from "react";
import { ChevronRight, ExternalLink, UserCheck } from "lucide-react";
import type { Overview, OverviewProfile } from "../../types/pipeline";
import { Badge } from "../ui/Badge";
import { cn } from "../../lib/cn";

/**
 * The final deliverable: Family › Category › Profile, with every downstream
 * artifact hanging off the profile.
 *
 * Aggregate-first, like the JE browser. A family row shows headcount, profile
 * count, mean evaluation score and match coverage; the detail for one profile —
 * its source jobs, skills, tasks and external match — only appears when that
 * profile is opened. Columns for stages that have not run are omitted rather
 * than rendered empty, so a partial project doesn't look like a broken one.
 */

interface Props {
  data: Overview;
  onOpenProfile: (profileKey: string) => void;
}

export function OverviewBrowser({ data, onOpenProfile }: Props) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const toggle = (k: string) => setOpen((o) => ({ ...o, [k]: !o[k] }));
  const { totals, available, has_headcount: hasHc } = data;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-[10px] border border-border bg-panel px-4 py-3">
        <Stat value={totals.families} label="Families" />
        <Stat value={totals.categories} label="Categories" />
        <Stat value={totals.profile_count} label="Profiles" />
        <Stat value={totals.source_job_count} label="Source jobs" />
        {hasHc && totals.headcount != null && <Stat value={totals.headcount} label="People" />}
        {available.evaluation && totals.mean_je_score != null && (
          <Stat value={totals.mean_je_score} label="Mean JE score" />
        )}
        {available.skills && <Stat value={totals.skills} label="Skills" />}
        {available.tasks && <Stat value={totals.tasks} label="Tasks" />}
        {available.taxonomy_match && (
          <Stat value={`${totals.matched_count}/${totals.profile_count}`} label="Matched" />
        )}
      </div>

      <ul className="space-y-1.5">
        {data.families.map((fam) => {
          const fk = `f${fam.id}`;
          return (
            <li key={fk} className="overflow-hidden rounded-[10px] border border-border bg-card">
              <GroupRow
                depth={0}
                name={fam.name}
                node={fam}
                hasHeadcount={hasHc}
                showJe={available.evaluation}
                expanded={!!open[fk]}
                onToggle={() => toggle(fk)}
              />
              {open[fk] &&
                fam.categories.map((cat) => {
                  const ck = `${fk}c${cat.id}`;
                  return (
                    <div key={ck} className="border-t border-border">
                      <GroupRow
                        depth={1}
                        name={cat.name}
                        node={cat}
                        hasHeadcount={hasHc}
                        showJe={available.evaluation}
                        expanded={!!open[ck]}
                        onToggle={() => toggle(ck)}
                      />
                      {open[ck] &&
                        cat.profiles.map((p) => {
                          const pk = `${ck}p${p.profile_key}`;
                          return (
                            <div key={pk} className="border-t border-border bg-panel/40">
                              <ProfileRow
                                profile={p}
                                hasHeadcount={hasHc}
                                expanded={!!open[pk]}
                                onToggle={() => toggle(pk)}
                                onOpen={() => onOpenProfile(p.profile_key)}
                              />
                              {open[pk] && (
                                <ProfileDetail
                                  profile={p}
                                  available={available}
                                />
                              )}
                            </div>
                          );
                        })}
                    </div>
                  );
                })}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Stat({ value, label }: { value: string | number; label: string }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-[16px] font-bold tabular-nums text-accent">{value}</span>
      <span className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">
        {label}
      </span>
    </span>
  );
}

function Metric({ value, label }: { value: string | number; label: string }) {
  return (
    <span className="flex items-baseline gap-1">
      <span className="text-[12px] font-bold tabular-nums text-text">{value}</span>
      <span className="text-[9.5px] font-semibold uppercase tracking-wider text-text-muted">
        {label}
      </span>
    </span>
  );
}

function GroupRow({
  depth,
  name,
  node,
  hasHeadcount,
  showJe,
  expanded,
  onToggle,
}: {
  depth: number;
  name: string;
  node: { profile_count: number; headcount: number | null; mean_je_score: number | null };
  hasHeadcount: boolean;
  showJe: boolean;
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
      <span className="flex shrink-0 items-center gap-3">
        {showJe && node.mean_je_score != null && (
          <Metric value={node.mean_je_score} label="JE" />
        )}
        {hasHeadcount && node.headcount != null && (
          <Metric value={node.headcount} label="people" />
        )}
        <Metric value={node.profile_count} label="profiles" />
      </span>
    </button>
  );
}

function ProfileRow({
  profile,
  hasHeadcount,
  expanded,
  onToggle,
  onOpen,
}: {
  profile: OverviewProfile;
  hasHeadcount: boolean;
  expanded: boolean;
  onToggle: () => void;
  onOpen: () => void;
}) {
  return (
    <div className="flex items-center gap-2 py-1.5 pl-[66px] pr-3">
      <button onClick={onToggle} className="shrink-0">
        <ChevronRight
          size={13}
          className={cn("text-text-muted transition-transform", expanded && "rotate-90")}
        />
      </button>
      {/* Primary click opens the profile document, matching the JE browser's
          rule that the row's main action is "show me the profile". */}
      <button
        onClick={onOpen}
        className="group flex min-w-0 flex-1 items-center gap-1.5 text-left"
      >
        <span className="truncate text-[12.5px] font-semibold text-text group-hover:text-accent">
          {profile.title}
        </span>
        <ExternalLink size={11} className="shrink-0 text-text-muted group-hover:text-accent" />
      </button>

      {profile.evaluation && (
        <Badge color="accent">
          {profile.evaluation.level_name} · {profile.evaluation.aggregate_score}
        </Badge>
      )}
      {profile.taxonomy_match?.level_code && (
        <Badge color="teal">{profile.taxonomy_match.level_code}</Badge>
      )}
      {profile.taxonomy_match?.overridden_by_user && (
        <span title="Match set by a user">
          <UserCheck size={12} className="text-success" />
        </span>
      )}
      {hasHeadcount && profile.headcount != null && (
        <span className="tabular-nums text-[11px] text-text-muted">{profile.headcount} ppl</span>
      )}
    </div>
  );
}

function ProfileDetail({
  profile,
  available,
}: {
  profile: OverviewProfile;
  available: Overview["available"];
}) {
  return (
    <div className="space-y-3 border-t border-border bg-card px-4 py-3 pl-[66px]">
      {profile.source_titles.length > 0 && (
        <Section title={`Input jobs (${profile.source_job_count})`}>
          <p className="text-[11.5px] leading-relaxed text-text-secondary">
            {profile.source_titles.join(" · ")}
          </p>
        </Section>
      )}

      {available.taxonomy_match && (
        <Section title="3rd-party taxonomy">
          {profile.taxonomy_match ? (
            <p className="text-[11.5px] text-text-secondary">
              {profile.taxonomy_match.family_title} ›{" "}
              <span className="font-semibold text-text">{profile.taxonomy_match.spec_title}</span>
              <span className="ml-1.5 font-mono text-[10px] text-text-muted">
                {profile.taxonomy_match.spec_code}
              </span>
              {profile.taxonomy_match.level_title && ` · ${profile.taxonomy_match.level_title}`}
              <span className="ml-1.5 tabular-nums text-text-muted">
                {Math.round(profile.taxonomy_match.confidence * 100)}% confidence
              </span>
              {profile.taxonomy_match.needs_review && (
                <Badge color="warning" className="ml-1.5">
                  review
                </Badge>
              )}
            </p>
          ) : (
            <p className="text-[11.5px] italic text-text-muted">No defensible match.</p>
          )}
        </Section>
      )}

      {available.skills && profile.skills.length > 0 && (
        <Section title={`Skills required (${profile.skill_count})`}>
          <ul className="flex flex-wrap gap-1.5">
            {profile.skills.map((s) => (
              <li
                key={s.cluster_id}
                className="rounded-full border border-accent-border bg-accent-bg px-2.5 py-0.5 text-[11px] text-accent"
              >
                {s.cluster_name}
                {s.assigned_level && (
                  <span className="ml-1 font-bold">· {s.assigned_level}</span>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {available.tasks && profile.tasks.length > 0 && (
        <Section title={`Time allocation (${profile.task_count} tasks)`}>
          <ul className="space-y-1">
            {profile.tasks.map((t) => (
              <li key={t.name} className="flex items-center gap-2 text-[11.5px]">
                <span className="w-11 shrink-0 text-right font-bold tabular-nums text-text">
                  {t.proportion.toFixed(0)}%
                </span>
                {/* A bar makes the split readable at a glance; the taxonomy
                    cluster tells you where this task rolls up. */}
                <span className="h-1.5 w-24 shrink-0 overflow-hidden rounded-full bg-panel">
                  <span
                    className="block h-full rounded-full bg-accent"
                    style={{ width: `${Math.min(100, t.proportion)}%` }}
                  />
                </span>
                <span className="min-w-0 flex-1 truncate text-text-secondary">
                  {t.name}
                  {t.cluster_name && (
                    <span className="ml-1.5 text-text-muted">— {t.cluster_name}</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1 text-[9.5px] font-extrabold uppercase tracking-wider text-text-muted">
        {title}
      </p>
      {children}
    </div>
  );
}
