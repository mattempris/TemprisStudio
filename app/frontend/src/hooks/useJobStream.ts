import { useCallback, useEffect, useRef, useState } from "react";
import type { ProgressEvent } from "../types/pipeline";

export interface JobState {
  jobId: string | null;
  stage: string | null;
  running: boolean;
  current: number;
  total: number;
  percent: number;
  message: string;
  summary: Record<string, unknown> | null;
  error: string | null;
  /** Seconds since the job started, from server heartbeats — lets the UI show
   * "still working" during a long stage rather than looking frozen. */
  elapsed: number;
}

const IDLE: JobState = {
  jobId: null,
  stage: null,
  running: false,
  current: 0,
  total: 0,
  percent: 0,
  message: "",
  summary: null,
  error: null,
  elapsed: 0,
};

/**
 * Subscribes to a pipeline job's progress WebSocket.
 *
 * The socket connects directly to the backend port rather than through the Vite
 * dev proxy, which is unreliable for long-lived connections — the same approach
 * jobMatching's client takes.
 */
export function useJobStream(onFinished?: (summary: Record<string, unknown>) => void) {
  const [state, setState] = useState<JobState>(IDLE);
  const socketRef = useRef<WebSocket | null>(null);
  const finishedRef = useRef(onFinished);
  finishedRef.current = onFinished;
  // Whether this job has already been accounted for — by completing, by failing, or by the
  // caller deliberately dropping it. Guards the close handler below from firing twice, and from
  // firing at all on a close we asked for.
  const settledRef = useRef(true);

  const disconnect = useCallback(() => {
    settledRef.current = true;
    socketRef.current?.close();
    socketRef.current = null;
  }, []);

  const attach = useCallback(
    (jobId: string, stage: string) => {
      disconnect();
      settledRef.current = false;
      setState({ ...IDLE, jobId, stage, running: true, message: "Connecting…" });

      const isHttps = window.location.protocol === "https:";
      const scheme = isHttps ? "wss" : "ws";
      // In dev the API runs on its own port; in the container nginx serves both
      // on the same origin, so fall back to the page's own host there.
      const devPort = import.meta.env.DEV ? ":9400" : "";
      const url = `${scheme}://${window.location.hostname}${devPort}/ws/pipeline/${jobId}`;
      const socket = new WebSocket(url);
      socketRef.current = socket;

      socket.onmessage = (raw) => {
        const evt = JSON.parse(raw.data) as ProgressEvent;
        setState((prev) => {
          switch (evt.type) {
            case "stage_start":
              return { ...prev, stage: evt.stage, total: evt.total, current: 0, percent: 0, message: evt.message };
            case "progress":
              return {
                ...prev,
                stage: evt.stage,
                current: evt.current,
                total: evt.total,
                percent: evt.percent,
                message: evt.message,
              };
            case "stage_complete":
              return { ...prev, message: `${evt.stage} complete` };
            case "heartbeat":
              return { ...prev, elapsed: evt.elapsed_seconds };
            case "complete":
              return { ...prev, running: false, percent: 100, summary: evt.summary, message: "Done" };
            case "error":
              return { ...prev, running: false, error: evt.message };
            default:
              return prev;
          }
        });

        if (evt.type === "complete") {
          settledRef.current = true;
          finishedRef.current?.(evt.summary);
          socket.close();
        }
        if (evt.type === "error") {
          settledRef.current = true;
          socket.close();
        }
      };

      socket.onerror = () =>
        setState((prev) => ({ ...prev, running: false, error: "connection to the progress stream failed" }));

      // A socket that closes without having said "complete" still means the job is no longer
      // being watched — and the job itself usually finished perfectly well. A multi-minute run
      // outlives a laptop sleep, an idle timeout, a proxy dropping a quiet connection or a
      // backend restart, and any of those closes the socket silently.
      //
      // Without this the page kept whatever it knew before the job started, so every gate that
      // reads status stayed shut over work that had actually been done: an AI opportunity
      // assessment completed against 90 clusters while the steps it unlocks stayed locked.
      // Re-reading status is cheap and correct whatever happened, so it is better done on any
      // unexpected close than only on the happy path.
      socket.onclose = () => {
        const unexpected = !settledRef.current;
        settledRef.current = true;
        setState((prev) => ({ ...prev, running: false }));
        if (unexpected) finishedRef.current?.({});
      };
    },
    [disconnect],
  );

  const reset = useCallback(() => {
    disconnect();
    setState(IDLE);
  }, [disconnect]);

  useEffect(() => disconnect, [disconnect]);

  return { state, attach, reset };
}
