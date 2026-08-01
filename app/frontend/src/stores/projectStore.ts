import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { ProjectMeta } from "../types/project";

interface ProjectStore {
  clientSlug: string | null;
  project: ProjectMeta | null;
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
      setSelection: (clientSlug, project) => set({ clientSlug, project }),
      clear: () => set({ clientSlug: null, project: null }),
    }),
    { name: "jastudio-selection" },
  ),
);
