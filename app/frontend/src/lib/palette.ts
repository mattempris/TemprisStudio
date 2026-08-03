/**
 * Palette definitions and persistence.
 *
 * Separate from the switcher component because Vite's Fast Refresh requires a module
 * to export only components or only plain values — mixing them forces a full page
 * reload on every edit to the file.
 *
 * A palette is a `data-palette` attribute on `<html>`; `palettes.css` overrides the
 * same design tokens the whole app already reads, so nothing else needs to know
 * palettes exist. Stored in localStorage rather than project state because it is a
 * property of the person looking, not of the project.
 */

export const PALETTES = [
  { id: "light", name: "Light", swatch: ["#f2f3f6", "#1d4ed8", "#c00000"] },
  { id: "dark", name: "Dark", swatch: ["#12141c", "#60a5fa", "#ef4444"] },
  { id: "vintage", name: "Vintage", swatch: ["#efe7d8", "#4a6b57", "#9c3b2e"] },
  { id: "cyberpunk", name: "Cyberpunk", swatch: ["#0a0a14", "#05d9e8", "#ff2a6d"] },
  { id: "sepia", name: "Sepia", swatch: ["#ece3d6", "#7a5c3a", "#8a3f2a"] },
  { id: "upbeat", name: "Upbeat", swatch: ["#f6f8ff", "#4f46e5", "#e5326b"] },
] as const;

export type PaletteId = (typeof PALETTES)[number]["id"];

const KEY = "jastudio.palette";

function isPalette(v: string | null): v is PaletteId {
  return !!v && PALETTES.some((p) => p.id === v);
}

/** Read and apply the stored palette. Called from main.tsx before first paint so a
 *  non-default palette does not flash the light theme. */
export function applyStoredPalette(): PaletteId {
  const stored = localStorage.getItem(KEY);
  const id: PaletteId = isPalette(stored) ? stored : "light";
  document.documentElement.dataset.palette = id;
  return id;
}

export function setPalette(id: PaletteId): void {
  document.documentElement.dataset.palette = id;
  localStorage.setItem(KEY, id);
}
