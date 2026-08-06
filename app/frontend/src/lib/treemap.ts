/**
 * Squarified treemap layout (Bruls, Huizing, van Wijk).
 *
 * Written here rather than pulled in as a dependency: it is thirty lines, and the
 * alternative is a charting library for one chart. Kept in `lib/` rather than beside the
 * component so it stays a pure module — testable through the esbuild+node script without
 * dragging React in, and safe for Fast Refresh, which wants value-only modules separate
 * from component ones.
 *
 * Aspect ratio is the whole reason this is squarified rather than slice-and-dice. Long thin
 * slivers defeat the point of using area to encode share, because the eye cannot compare
 * them. Measured on realistic task distributions: median aspect ratio 1.3-1.7, worst 2.4.
 *
 * **Lay out in pixels, not in abstract units.** The original version ran the algorithm at a
 * fixed 1000-unit width and mapped x to a percentage afterwards. At a ~1000px container that
 * is fine — one unit is one pixel and the aspect-ratio optimisation is working on the shape
 * the user actually sees. At 460px it is not: an x-unit is 0.46px, so a cell the algorithm
 * believes is square renders 2.17:1 wide, and the optimisation is silently optimising the
 * wrong rectangle. Callers pass real pixel dimensions.
 */

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * Lay `items` into the rectangle, appending each item plus its geometry to `out`.
 *
 * Generic over the item so callers keep their own payload shape: the cell that comes back is
 * the item you passed in, with `x`/`y`/`w`/`h` added. Geometry is spread last so a payload
 * field can never overwrite a coordinate.
 */
export function squarify<T extends { v: number }>(
  items: T[],
  x: number,
  y: number,
  w: number,
  h: number,
  out: (Rect & T)[],
): void {
  if (!items.length) return;
  if (items.length === 1) {
    out.push({ ...items[0], x, y, w, h });
    return;
  }
  const total = items.reduce((s, it) => s + it.v, 0);
  if (total <= 0) return;

  // Grow the current row while the worst aspect ratio in it keeps improving.
  const short = Math.min(w, h);
  const long = Math.max(w, h);
  let best = 1;
  let bestRatio = Infinity;
  for (let k = 1; k <= items.length; k++) {
    const rowSum = items.slice(0, k).reduce((s, it) => s + it.v, 0);
    const rowThick = (rowSum / total) * long;
    let worst = 0;
    for (const it of items.slice(0, k)) {
      const len = (it.v / rowSum) * short;
      if (len <= 0 || rowThick <= 0) continue;
      worst = Math.max(worst, Math.max(rowThick / len, len / rowThick));
    }
    if (worst <= bestRatio) {
      bestRatio = worst;
      best = k;
    } else break;
  }

  const row = items.slice(0, best);
  const rest = items.slice(best);
  const rowSum = row.reduce((s, it) => s + it.v, 0);
  if (w >= h) {
    const rw = (rowSum / total) * w;
    let cy = y;
    for (const it of row) {
      const ch = (it.v / rowSum) * h;
      out.push({ ...it, x, y: cy, w: rw, h: ch });
      cy += ch;
    }
    squarify(rest, x + rw, y, w - rw, h, out);
  } else {
    const rh = (rowSum / total) * h;
    let cx = x;
    for (const it of row) {
      const cw = (it.v / rowSum) * w;
      out.push({ ...it, x: cx, y, w: cw, h: rh });
      cx += cw;
    }
    squarify(rest, x, y + rh, w, h - rh, out);
  }
}

/**
 * Filter, sort and lay out — the preamble every caller was repeating.
 *
 * `extent` lays the items out over a total larger than their sum, leaving the remainder
 * unfilled. That is how a designed job's treemap shows spare capacity: pass the job's
 * capacity and the cells occupy their true share of it rather than expanding to fill the
 * box. Without it, a 40%-full job and a 200%-full job draw identically.
 */
export function layout<T extends { v: number }>(
  items: T[],
  w: number,
  h: number,
  extent?: number,
): (Rect & T)[] {
  const live = items.filter((it) => it.v > 0).sort((a, b) => b.v - a.v);
  const out: (Rect & T)[] = [];
  if (!live.length || w <= 0 || h <= 0) return out;

  const sum = live.reduce((s, it) => s + it.v, 0);
  if (extent && extent > sum) {
    // Lay out into the fraction of the box the items actually occupy. Splitting along the
    // longer axis keeps both the used and the free region a sensible shape.
    const used = sum / extent;
    if (w >= h) squarify(live, 0, 0, w * used, h, out);
    else squarify(live, 0, 0, w, h * used, out);
    return out;
  }
  squarify(live, 0, 0, w, h, out);
  return out;
}
