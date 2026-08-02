import { Cpu, Zap } from "lucide-react";
import type { EmbeddingModelsInfo } from "../../types/pipeline";
import { Badge } from "../ui/Badge";
import { cn } from "../../lib/cn";

/**
 * How the next embedding run should execute: which model, and on what device.
 *
 * Both choices belong together because they are the same decision — "how do I
 * want this embed to run" — and both are per-run rather than global, since the
 * right answer depends on the data in front of you and on whether the GPU is
 * busy.
 *
 * Switching model invalidates any embeddings already cached for this entity: the
 * two job models both emit 1024 dimensions, so their vectors are shape
 * compatible but mean entirely different things. That is stated here rather than
 * only enforced server-side, because a 409 after the fact is a worse way to find
 * out.
 */

interface Props {
  info: EmbeddingModelsInfo;
  entity: "job" | "skill" | "task";
  model: string | null;
  onModel: (model: string | null) => void;
  forceCpu: boolean;
  onForceCpu: (v: boolean) => void;
  /** True when a tree already exists — switching model then means a rebuild. */
  hasExistingTree: boolean;
  disabled?: boolean;
}

export function EmbeddingOptions({
  info,
  entity,
  model,
  onModel,
  forceCpu,
  onForceCpu,
  hasExistingTree,
  disabled,
}: Props) {
  const slot = info[entity];
  if (!slot) return null;

  const effective = model ?? slot.current;
  const changed = effective !== slot.current;

  return (
    <div className="space-y-2.5 rounded-[10px] border border-border bg-panel px-4 py-3">
      <p className="text-[11px] font-extrabold uppercase tracking-wider text-text-muted">
        Embedding model
      </p>

      {slot.selectable ? (
        <div className="space-y-1.5">
          {slot.models.map((m) => {
            const active = effective === m.name;
            return (
              <label
                key={m.name}
                className={cn(
                  "flex cursor-pointer gap-2.5 rounded-[8px] border px-3 py-2 transition-colors",
                  active ? "border-accent-border bg-accent-bg" : "border-border bg-card hover:border-accent-border",
                  (!m.installed || disabled) && "cursor-not-allowed opacity-60",
                )}
              >
                <input
                  type="radio"
                  name={`embed-model-${entity}`}
                  checked={active}
                  disabled={!m.installed || disabled}
                  onChange={() => onModel(m.name === slot.current ? null : m.name)}
                  className="mt-0.5 accent-[var(--color-accent)]"
                />
                <span className="min-w-0">
                  <span className="flex flex-wrap items-center gap-1.5">
                    <span className="text-[12.5px] font-semibold text-text">{m.name}</span>
                    {m.name === slot.current && <Badge color="teal">default</Badge>}
                    {m.loaded && <Badge color="success">loaded</Badge>}
                    {!m.installed && <Badge color="brand">not installed</Badge>}
                    <span className="text-[10px] tabular-nums text-text-muted">{m.dim}d</span>
                  </span>
                  <span className="mt-0.5 block text-[11px] leading-snug text-text-muted">
                    {m.note}
                  </span>
                </span>
              </label>
            );
          })}
        </div>
      ) : (
        <p className="text-[11.5px] text-text-secondary">
          {slot.current} — the only model for {entity}s.
        </p>
      )}

      {changed && hasExistingTree && (
        <p className="rounded-[8px] border border-warning-border bg-warning-bg px-3 py-2 text-[11.5px] leading-snug text-text">
          Changing model means re-embedding. Vectors from the two models are not
          comparable — both are {slot.models[0]?.dim ?? 1024}-dimensional, so this
          cannot be detected from the data itself — and anything already built from
          the old ones has to be rebuilt.
        </p>
      )}

      <label
        className={cn(
          "flex items-start gap-2 border-t border-border pt-2.5 text-[11.5px]",
          disabled ? "opacity-60" : "cursor-pointer",
        )}
      >
        <input
          type="checkbox"
          checked={forceCpu}
          disabled={disabled}
          onChange={(e) => onForceCpu(e.target.checked)}
          className="mt-0.5 accent-[var(--color-accent)]"
        />
        <span>
          <span className="flex items-center gap-1.5 font-semibold text-text">
            {forceCpu ? <Cpu size={12} /> : <Zap size={12} />}
            Run on CPU
          </span>
          <span className="mt-0.5 block leading-snug text-text-muted">
            {forceCpu
              ? "Slower, but leaves the GPU free for other work."
              : "Uses the GPU when enough VRAM is free, falling back to CPU automatically if not."}
          </span>
        </span>
      </label>
    </div>
  );
}
