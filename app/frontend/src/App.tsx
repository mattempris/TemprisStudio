import { useProjectStore } from "./stores/projectStore";
import { ProjectSelectPage } from "./pages/ProjectSelectPage";

const TEMPRIS_LOGO_URL =
  "https://temprispublicfiles.blob.core.windows.net/files/Tempris%20Logos/Clear%20background/Tempris_Logo_Red.png";

function App() {
  const { clientSlug, project, clear } = useProjectStore();

  return (
    <div className="min-h-screen bg-canvas">
      <header className="flex items-center justify-between border-b-2 border-brand bg-card px-6 py-3">
        <div className="flex items-center gap-3">
          <img src={TEMPRIS_LOGO_URL} alt="Tempris" className="h-8 w-auto" />
          <span className="text-[14px] font-semibold tracking-wide text-text-secondary">
            JAStudio
          </span>
        </div>
        {project && (
          <button
            onClick={clear}
            className="text-[11.5px] font-semibold text-text-muted hover:text-text"
          >
            {clientSlug} / {project.display_name} — switch project
          </button>
        )}
      </header>

      <main>
        {project ? (
          <div className="mx-auto max-w-2xl px-6 py-12">
            <h1 className="text-[20px] font-bold text-text">{project.display_name}</h1>
            <p className="mt-1 text-[13px] text-text-muted">
              Project pipeline UI coming in Phase 1.
            </p>
          </div>
        ) : (
          <ProjectSelectPage />
        )}
      </main>
    </div>
  );
}

export default App;
