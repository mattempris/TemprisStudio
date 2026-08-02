/**
 * Cyan-to-magenta scale for cluster sizes.
 *
 * Cluster sizes are heavily skewed — a 916-job cut at 153 profiles runs 1 to 26
 * with a median of 5 — so the scale is logarithmic. On a linear scale nearly every
 * tile would sit at the cyan end and the one large cluster would be the only thing
 * visible, which is the opposite of the point.
 *
 * Interpolated in HSL through blue and violet. Lightness rises slightly towards
 * magenta rather than staying flat: at equal HSL lightness a cyan is markedly
 * brighter than a magenta, so a flat ramp both washed out the white numerals at the
 * cyan end (~2.4:1 against white, unreadable at 10px) and made the small clusters
 * read as the emphasised ones. These endpoints hold white text above ~4:1
 * throughout while keeping the ramp's direction obvious.
 */

const HUE_LOW = 187; // cyan
const HUE_HIGH = 303; // magenta
const SAT = 76;
const LIGHT_LOW = 33;
const LIGHT_HIGH = 42;

/** Colour for a position 0..1 along the scale. */
export function heatColorAt(t: number): string {
  const c = Math.min(1, Math.max(0, t));
  return `hsl(${HUE_LOW + (HUE_HIGH - HUE_LOW) * c} ${SAT}% ${
    LIGHT_LOW + (LIGHT_HIGH - LIGHT_LOW) * c
  }%)`;
}

/** Where `value` sits on a log scale between `min` and `max`. */
export function heatPosition(value: number, min: number, max: number): number {
  const lo = Math.log(Math.max(1, min));
  const hi = Math.log(Math.max(1, max));
  if (hi <= lo) return 0;
  return Math.min(1, Math.max(0, (Math.log(Math.max(1, value)) - lo) / (hi - lo)));
}

export function heatColor(value: number, min: number, max: number): string {
  return heatColorAt(heatPosition(value, min, max));
}

/** CSS gradient matching the scale, for a legend. */
export const HEAT_GRADIENT = `linear-gradient(to right, ${[0, 0.2, 0.4, 0.6, 0.8, 1]
  .map((t) => heatColorAt(t))
  .join(", ")})`;
