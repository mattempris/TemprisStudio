import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Search } from "lucide-react";
import type { ProfileRow } from "../../types/pipeline";

type Filter = "all" | "unevaluated" | "evaluated";

/**
 * Pick which profiles to evaluate — one, a category, or everything.
 *
 * Evaluation used to be all-or-nothing, which is the wrong granularity once a project has
 * 565 profiles: the common actions are "try the framework on one profile before spending on
 * the set" and "re-level the handful someone challenged in the review meeting". Both were
 * previously a full ensemble run.
 *
 * Grouped by category with a header checkbox, because "a group of jobs" in practice means a
 * category — that is the unit people argue about levels in. The group header is the only
 * thing here that is not a plain list row, and it exists so selecting fourteen related
 * profiles is one click rather than fourteen.
 */
export function ProfileSelect({
  profiles,
  selected,
  onChange,
}: {
  profiles: ProfileRow[];
  selected: Set<string>;
  onChange: (next: Set<string>) => void;
}) {
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const rows = profiles.filter((p) => {
      if (filter === "unevaluated" && p.has_je) return false;
      if (filter === "evaluated" && !p.has_je) return false;
      if (!needle) return true;
      return (
        p.title.toLowerCase().includes(needle) ||
        p.breadcrumb.some((b) => b.toLowerCase().includes(needle))
      );
    });
    const by = new Map<string, ProfileRow[]>();
    for (const p of rows) {
      // The breadcrumb runs family › category › profile, so the category is the
      // next-to-last entry. Profiles with no confirmed category fall into one bucket
      // rather than each becoming its own group of one.
      const key = p.breadcrumb.length >= 2 ? p.breadcrumb[p.breadcrumb.length - 2] : "Ungrouped";
      const list = by.get(key);
      if (list) list.push(p);
      else by.set(key, [p]);
    }
    return [...by.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [profiles, q, filter]);

  const shown = groups.flatMap(([, rows]) => rows);

  function toggle(keys: string[], on: boolean) {
    const next = new Set(selected);
    for (const k of keys) {
      if (on) next.add(k);
      else next.delete(k);
    }
    onChange(next);
  }

  const allShownSelected = shown.length > 0 && shown.every((p) => selected.has(p.profile_key));

  return (
    <div className="rounded-[10px] border border-border bg-panel">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <div className="relative min-w-[180px] flex-1">
          <Search className="absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-text-muted" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search profiles and categories…"
            className="w-full rounded-[7px] border border-border bg-card py-1 pl-7 pr-2 text-[12px] text-text outline-none focus:border-accent"
          />
        </div>
        <div className="flex rounded-[7px] border border-border bg-card p-0.5">
          {(["all", "unevaluated", "evaluated"] as Filter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded-[5px] px-2 py-0.5 text-[11px] font-semibold capitalize transition-colors ${
                filter === f ? "bg-accent-bg text-accent" : "text-text-secondary hover:text-text"
              }`}
            >
              {f}
            </button>
          ))}
        </div>
        <button
          onClick={() => toggle(shown.map((p) => p.profile_key), !allShownSelected)}
          disabled={shown.length === 0}
          className="rounded-[7px] border border-border bg-card px-2 py-1 text-[11px] font-semibold text-text-secondary transition-colors hover:text-text disabled:opacity-40"
        >
          {allShownSelected ? "Clear" : `Select ${shown.length}`}
        </button>
      </div>

      <div className="max-h-[340px] overflow-y-auto">
        {groups.length === 0 ? (
          <p className="px-3 py-6 text-center text-[12px] text-text-muted">
            Nothing matches that search.
          </p>
        ) : (
          groups.map(([name, rows]) => {
            const keys = rows.map((p) => p.profile_key);
            const on = keys.every((k) => selected.has(k));
            const some = !on && keys.some((k) => selected.has(k));
            const isCollapsed = collapsed.has(name);
            return (
              <div key={name} className="border-b border-border last:border-0">
                <div className="flex items-center gap-2 bg-card/60 px-3 py-1.5">
                  <input
                    type="checkbox"
                    checked={on}
                    // Indeterminate is not settable through JSX, so it goes on via a ref.
                    ref={(el) => {
                      if (el) el.indeterminate = some;
                    }}
                    onChange={(e) => toggle(keys, e.target.checked)}
                    className="h-3 w-3 accent-[var(--color-accent)]"
                  />
                  <button
                    onClick={() =>
                      setCollapsed((c) => {
                        const n = new Set(c);
                        if (n.has(name)) n.delete(name);
                        else n.add(name);
                        return n;
                      })
                    }
                    className="flex min-w-0 flex-1 items-center gap-1 text-left"
                  >
                    {isCollapsed ? (
                      <ChevronRight className="h-3 w-3 shrink-0 text-text-muted" />
                    ) : (
                      <ChevronDown className="h-3 w-3 shrink-0 text-text-muted" />
                    )}
                    <span className="truncate text-[11.5px] font-bold text-text">{name}</span>
                    <span className="shrink-0 text-[10.5px] tabular-nums text-text-muted">
                      {rows.length}
                    </span>
                  </button>
                </div>
                {!isCollapsed &&
                  rows.map((p) => (
                    <label
                      key={p.profile_key}
                      className="flex cursor-pointer items-center gap-2 px-3 py-1 pl-7 hover:bg-card"
                    >
                      <input
                        type="checkbox"
                        checked={selected.has(p.profile_key)}
                        onChange={(e) => toggle([p.profile_key], e.target.checked)}
                        className="h-3 w-3 accent-[var(--color-accent)]"
                      />
                      <span className="min-w-0 flex-1 truncate text-[11.5px] text-text">
                        {p.title}
                      </span>
                      {p.has_je ? (
                        <span className="shrink-0 text-[10.5px] tabular-nums text-text-muted">
                          {p.level_name}
                        </span>
                      ) : (
                        <span className="shrink-0 text-[10.5px] text-text-muted">not evaluated</span>
                      )}
                    </label>
                  ))}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
