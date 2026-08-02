import { Loader2 } from "lucide-react";
import type { JobState } from "../../hooks/useJobStream";

/**
 * A small "still working" marker to sit beside the button that started a job.
 *
 * The elapsed count comes from the server's own heartbeats, not a local timer, so
 * it advancing is evidence the backend is alive and still talking — which is
 * exactly the question during a naming pass that shows no countable progress for
 * minutes at a time.
 */
export function JobPulse({ job, className }: { job: JobState; className?: string }) {
  if (!job.running) return null;
  return (
    <span className={`flex items-center gap-1.5 text-[11.5px] text-text-secondary ${className ?? ""}`}>
      <Loader2 size={12} className="animate-spin text-accent" />
      <span className="font-semibold">
        {job.total > 0 ? `${job.current}/${job.total}` : "Working"}
      </span>
      {job.elapsed > 0 && (
        <span className="tabular-nums text-text-muted">{formatElapsed(job.elapsed)}</span>
      )}
    </span>
  );
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  return `${m}m ${Math.round(seconds - m * 60)}s`;
}
