import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { ProjectMeta } from "../types/project";

/** Which half of the app is showing. JAStudio builds the architecture; Workforce
 *  Studio consumes it. Persisted with the selection so a reload returns you to the
 *  one you were in. */
export type Studio = "job-architecture" | "workforce";

interface ProjectStore {
  clientSlug: string | null;
  project: ProjectMeta | null;
  studio: Studio;
  setStudio: (studio: Studio) => void;
  setSelection: (clientSlug: string, project: ProjectMeta) => void;
  clear: () => void;
}

/**
 * The selection survives a reload.
 *
 * Pipeline stages are long-running and the browser gets refreshed mid-run; being
 * dropped back to a list of every client and having to find your project again
 * is the wrong response to F5. The backend keeps the job alive and the page
 * re-attaches to it via `active_job_id`, so restoring the selection is what
 * makes that recovery actually reachable.
 */
export const useProjectStore = create<ProjectStore>()(
  persist(
    (set) => ({
      clientSlug: null,
      project: null,
      studio: "job-architecture",
      setStudio: (studio) => set({ studio }),
      // Switching project returns to the architecture side: the new project may not
      // have one built yet, and landing in Workforce Studio on an empty project is
      // a dead end rather than a starting point.
      setSelection: (clientSlug, project) =>
        set({ clientSlug, project, studio: "job-architecture" }),
      clear: () => set({ clientSlug: null, project: null, studio: "job-architecture" }),
    }),
    { name: "jastudio-selection" },
  ),
);
