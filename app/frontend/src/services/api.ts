import type { ProjectMeta } from "../types/project";

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(body.detail ?? res.statusText, res.status);
  }
  return res.json();
}

export const api = {
  listClients: () => request<string[]>("/projects/clients"),
  listProjects: (clientSlug: string) => request<string[]>(`/projects/clients/${clientSlug}`),
  createClient: (name: string) => request<{ client_slug: string }>("/projects/clients", {
    method: "POST",
    body: JSON.stringify({ name }),
  }),
  createProject: (req: {
    client_slug: string;
    project_name: string;
    client_company_description?: string;
    accent_color?: string;
  }) =>
    request<ProjectMeta>("/projects", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  getProject: (clientSlug: string, projectSlug: string) =>
    request<ProjectMeta>(`/projects/${clientSlug}/${projectSlug}`),

  // `is_demo` is false for every real project, which is what keeps the reset control from
  // rendering anywhere it could do damage.
  demoStatus: (clientSlug: string, projectSlug: string) =>
    request<DemoStatus>(`/projects/${clientSlug}/${projectSlug}/demo/status`),
  demoReset: (clientSlug: string, projectSlug: string) =>
    request<DemoResetResult>(`/projects/${clientSlug}/${projectSlug}/demo/reset`, {
      method: "POST",
    }),
};

export interface DemoStatus {
  is_demo: boolean;
  drifted: boolean;
  seeded_at?: string;
  seeded_from?: { client: string; project: string };
  blobs_at_seed?: number;
  blobs_now?: number;
  counts?: { added: number; removed: number; changed: number };
}

export interface DemoResetResult {
  counts: { delete: number; restore: number };
  unrepairable?: string[];
  warning?: string;
}

export { ApiError };
