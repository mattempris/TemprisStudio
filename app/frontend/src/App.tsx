import { useProjectStore } from "./stores/projectStore";
import { ProjectSelectPage } from "./pages/ProjectSelectPage";
import { PipelinePage } from "./pages/PipelinePage";
import { PaletteSwitcher } from "./components/ui/PaletteSwitcher";

const TEMPRIS_LOGO_URL =
  "https://temprispublicfiles.blob.core.windows.net/files/Tempris%20Logos/Clear%20background/Tempris_Logo_Red.png";

function App() {
  const { clientSlug, project, clear } = useProjectStore();

  return (
    <div className="min-h-screen bg-canvas">
      <header className="sticky top-0 z-40 flex items-center justify-between border-b-2 border-brand bg-card px-6 py-3">
        <div className="flex items-center gap-3">
          <img src={TEMPRIS_LOGO_URL} alt="Tempris" className="h-8 w-auto" />
          <span className="text-[14px] font-semibold tracking-wide text-text-secondary">
            JAStudio
          </span>
        </div>
        <div className="flex items-center gap-3">
          <PaletteSwitcher />
          {project && (
            <button
              onClick={clear}
              className="text-[11.5px] font-semibold text-text-muted hover:text-text"
            >
              {clientSlug} / {project.display_name} — switch project
            </button>
          )}
        </div>
      </header>

      <main>
        {project && clientSlug ? (
          <PipelinePage clientSlug={clientSlug} projectSlug={project.project_slug} />
        ) : (
          <ProjectSelectPage />
        )}
      </main>
    </div>
  );
}

export default App;
