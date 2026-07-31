import { useEffect, useState } from "react";
import { Plus } from "lucide-react";
import { api, ApiError } from "../services/api";
import { useProjectStore } from "../stores/projectStore";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";

export function ProjectSelectPage() {
  const [clients, setClients] = useState<string[] | null>(null);
  const [selectedClient, setSelectedClient] = useState<string | null>(null);
  const [projects, setProjects] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creatingClient, setCreatingClient] = useState(false);
  const [newClientName, setNewClientName] = useState("");
  const [creatingProject, setCreatingProject] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");

  const setSelection = useProjectStore((s) => s.setSelection);

  useEffect(() => {
    api
      .listClients()
      .then(setClients)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (!selectedClient) return;
    setProjects(null);
    api
      .listProjects(selectedClient)
      .then(setProjects)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [selectedClient]);

  async function handleCreateClient() {
    setError(null);
    try {
      const { client_slug } = await api.createClient(newClientName);
      setClients((prev) => (prev ? [...prev, client_slug].sort() : [client_slug]));
      setSelectedClient(client_slug);
      setCreatingClient(false);
      setNewClientName("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function handleCreateProject() {
    if (!selectedClient) return;
    setError(null);
    try {
      const project = await api.createProject({
        client_slug: selectedClient,
        project_name: newProjectName,
      });
      setSelection(selectedClient, project);
      setCreatingProject(false);
      setNewProjectName("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function handleSelectProject(projectSlug: string) {
    if (!selectedClient) return;
    setError(null);
    try {
      const project = await api.getProject(selectedClient, projectSlug);
      setSelection(selectedClient, project);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <div className="mx-auto max-w-2xl px-6 py-12">
      <h1 className="mb-1 text-[20px] font-bold text-text">Select a client and project</h1>
      <p className="mb-8 text-[13.5px] text-text-secondary">
        Choose an existing client or start a new one to begin building a job architecture.
      </p>

      {error && (
        <div className="mb-6 rounded-[10px] border border-brand-border bg-brand-bg px-4 py-3 text-[12.5px] text-text">
          {error}
        </div>
      )}

      <Card className="mb-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-[13.5px] font-extrabold uppercase tracking-wide text-text-secondary">
            Client
          </h2>
          <Button onClick={() => setCreatingClient((v) => !v)}>
            <span className="flex items-center gap-1">
              <Plus size={13} /> New client
            </span>
          </Button>
        </div>

        {creatingClient && (
          <div className="mb-3 flex gap-2">
            <input
              className="flex-1 rounded-[7px] border border-border px-3 py-2 text-[13px] outline-none focus:border-accent"
              placeholder="Client name"
              value={newClientName}
              onChange={(e) => setNewClientName(e.target.value)}
            />
            <Button variant="primary" onClick={handleCreateClient} disabled={!newClientName.trim()}>
              Create
            </Button>
          </div>
        )}

        {clients === null ? (
          <p className="text-[12.5px] text-text-muted">Loading clients…</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {clients.map((c) => (
              <button
                key={c}
                onClick={() => setSelectedClient(c)}
                className={`rounded-full border px-3 py-1.5 text-[11px] font-bold transition-colors ${
                  selectedClient === c
                    ? "border-accent-border bg-accent-bg text-accent"
                    : "border-border bg-card text-text-secondary hover:border-text-muted"
                }`}
              >
                {c}
              </button>
            ))}
          </div>
        )}
      </Card>

      {selectedClient && (
        <Card>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-[13.5px] font-extrabold uppercase tracking-wide text-text-secondary">
              Project — <Badge color="accent">{selectedClient}</Badge>
            </h2>
            <Button onClick={() => setCreatingProject((v) => !v)}>
              <span className="flex items-center gap-1">
                <Plus size={13} /> New project
              </span>
            </Button>
          </div>

          {creatingProject && (
            <div className="mb-3 flex gap-2">
              <input
                className="flex-1 rounded-[7px] border border-border px-3 py-2 text-[13px] outline-none focus:border-accent"
                placeholder="Project name"
                value={newProjectName}
                onChange={(e) => setNewProjectName(e.target.value)}
              />
              <Button variant="primary" onClick={handleCreateProject} disabled={!newProjectName.trim()}>
                Create
              </Button>
            </div>
          )}

          {projects === null ? (
            <p className="text-[12.5px] text-text-muted">Loading projects…</p>
          ) : projects.length === 0 ? (
            <p className="text-[12.5px] text-text-muted">No projects yet for this client.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {projects.map((p) => (
                <li key={p}>
                  <button
                    onClick={() => handleSelectProject(p)}
                    className="w-full rounded-[10px] border border-border bg-card px-4 py-3 text-left text-[13px] font-semibold text-text shadow-card transition-shadow hover:shadow-card-hover"
                  >
                    {p}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  );
}
