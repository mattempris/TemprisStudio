import type { ReactElement } from "react";
import { useProjectStore, type Studio } from "./stores/projectStore";
import { ProjectSelectPage } from "./pages/ProjectSelectPage";
import { PipelinePage } from "./pages/PipelinePage";
import { PaletteSwitcher } from "./components/ui/PaletteSwitcher";
import { WorkforcePage } from "./pages/WorkforcePage";
import { WorkDesignPage } from "./pages/WorkDesignPage";
import { STUDIO_LABEL } from "./components/wizard/StudioToggle";

const TEMPRIS_LOGO_URL =
  "https://temprispublicfiles.blob.core.windows.net/files/Tempris%20Logos/Clear%20background/Tempris_Logo_Red.png";

interface StudioPageProps {
  clientSlug: string;
  projectSlug: string;
}

/**
 * Which page each studio renders.
 *
 * A map rather than the nested ternary this replaces, because that ternary's `else` branch
 * was PipelinePage — so a third studio value rendered the job-architecture wizard while the
 * sidebar said Work Design, with nothing to indicate anything was wrong.
 *
 * The `??` fallback below is not defensive noise. `jastudio-selection` is persisted with no
 * `version`/`migrate`, so a browser can hold a studio id this build does not know — a rolled
 * back deploy, or a shared machine. A bare lookup would render `undefined`.
 */
const STUDIO_PAGES: Record<Studio, (p: StudioPageProps) => ReactElement> = {
  "job-architecture": (p) => <PipelinePage {...p} />,
  workforce: (p) => (
    <WorkforcePage
      {...p}
      // Re-reads the graph's colours from the tokens when the palette changes. Only this
      // page needs it: d3 reads resolved token values into a canvas, where the treemaps
      // set CSS variables inline and re-resolve on the attribute change by themselves.
      paletteKey={document.documentElement.dataset.palette ?? "light"}
    />
  ),
  "work-design": (p) => <WorkDesignPage {...p} />,
};

function App() {
  const { clientSlug, project, clear, studio } = useProjectStore();
  const render = STUDIO_PAGES[studio] ?? STUDIO_PAGES["job-architecture"];

  return (
    <div className="min-h-screen bg-canvas">
      <header className="sticky top-0 z-40 flex items-center justify-between border-b-2 border-brand bg-card px-6 py-3">
        <div className="flex items-center gap-3">
          <img src={TEMPRIS_LOGO_URL} alt="Tempris" className="h-8 w-auto" />
          {/* The logo already carries the wordmark, so this must not repeat "Tempris". */}
          <span className="text-[14px] font-semibold tracking-wide text-text-secondary">
            Studio
          </span>
          {/* There is no router, so without this nothing in the chrome says which of the
              three studios you are looking at. */}
          {project && clientSlug && (
            <span className="text-[12px] text-text-muted">
              · {STUDIO_LABEL[studio] ?? STUDIO_LABEL["job-architecture"]}
            </span>
          )}
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
          render({ clientSlug, projectSlug: project.project_slug })
        ) : (
          <ProjectSelectPage />
        )}
      </main>
    </div>
  );
}

export default App;
