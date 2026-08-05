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

/**
 * Colour for an AI opportunity percentage.
 *
 * Same ramp, deliberately, so the app has one visual language for "further along
 * means more" — but linear rather than logarithmic, because these are percentages of
 * a bounded quantity and are not skewed the way cluster sizes are.
 *
 * 100, not 80. The assessment used to cap both axes at 80 and this matched it, so the
 * hot end of the ramp meant "as automatable as anything gets". The cap is gone — some
 * work genuinely is fully automatable, and the residual supervision is now modelled as
 * the agent's own oversight tasks instead of being baked into a ceiling. Where a real
 * taxonomy's scores cluster in a narrow band, `opportunityColorIn` stretches the ramp to
 * the observed range, which is what keeps the picture readable now the scale is wider.
 */
export const OPPORTUNITY_CEILING = 100;

export function opportunityColor(pct: number, ceiling = OPPORTUNITY_CEILING): string {
  return heatColorAt(pct / ceiling);
}

/**
 * The same ramp stretched to a given range, for the graph.
 *
 * In a table the colour is decoration — the number is written beside it. On the graph
 * the colour *is* the number, and the absolute 0-80 scale turns out to say almost
 * nothing: real cluster automation on a whole taxonomy spans about 20% to 40%, which
 * on 0-80 is hues 217 to 242 — twenty-five degrees of blue, indistinguishable at the
 * size a node is drawn. Stretching to the observed range makes the picture readable.
 *
 * Safe only because the legend is labelled with the actual endpoints. An unlabelled
 * stretched ramp would let a workforce that is 20-40% automatable look as though its
 * hot end were fully absorbable.
 */
export function opportunityColorIn(pct: number, lo: number, hi: number): string {
  return hi > lo ? heatColorAt((pct - lo) / (hi - lo)) : heatColorAt(0.5);
}

/** Observed range of a set of scores, widened to at least 10 points so a taxonomy
 *  that genuinely agrees with itself does not get a ramp built out of noise. */
export function opportunitySpan(values: number[], minWidth = 10): [number, number] {
  if (!values.length) return [0, OPPORTUNITY_CEILING];
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (hi - lo < minWidth) {
    const pad = (minWidth - (hi - lo)) / 2;
    lo = Math.max(0, lo - pad);
    hi = Math.min(OPPORTUNITY_CEILING, hi + pad);
  }
  return [Math.floor(lo), Math.ceil(hi)];
}

/** CSS gradient matching the scale, for a legend. */
export const HEAT_GRADIENT = `linear-gradient(to right, ${[0, 0.2, 0.4, 0.6, 0.8, 1]
  .map((t) => heatColorAt(t))
  .join(", ")})`;
