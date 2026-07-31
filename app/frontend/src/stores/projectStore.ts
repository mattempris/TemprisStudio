import { create } from "zustand";
import type { ProjectMeta } from "../types/project";

interface ProjectStore {
  clientSlug: string | null;
  project: ProjectMeta | null;
  setSelection: (clientSlug: string, project: ProjectMeta) => void;
  clear: () => void;
}

export const useProjectStore = create<ProjectStore>((set) => ({
  clientSlug: null,
  project: null,
  setSelection: (clientSlug, project) => set({ clientSlug, project }),
  clear: () => set({ clientSlug: null, project: null }),
}));
