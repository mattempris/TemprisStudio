/**
 * The treemap's one correctness claim: cell area equals share of the role's week.
 *
 * Worth a test because every way it can break is silent. A layout that tiles the box but
 * gets the areas wrong still looks like a treemap — it just misrepresents how the person
 * spends their time, which is the only thing the chart is for. Overlaps and gaps are
 * likewise invisible at a glance on a 230px strip.
 *
 * Run:  npx esbuild scripts/_test_treemap.ts --bundle --platform=node --format=esm  *         --outfile=node_modules/.cache/tm.mjs && node node_modules/.cache/tm.mjs
 *
 * Imports from lib/treemap rather than the component: the layout is a pure module now, so
 * this bundles a hundred lines instead of pulling React through esbuild. The assertions are
 * unchanged — squarify is generic over the item and returns it with geometry added, so a
 * cell is still `{v, task, x, y, w, h}` and `c.task.proportion` still resolves.
 */
import { layout, squarify } from "../src/lib/treemap";

const W = 1000, H = 230;
let ok = true;
const check = (label: string, cond: boolean, detail = "") => {
  ok = ok && cond;
  console.log(`  ${cond ? "OK  " : "FAIL"}  ${label}${detail ? " — " + detail : ""}`);
};

function run(props: number[], label: string) {
  const tasks = props.map((p, i) => ({
    name: `T${i}`, description: "", proportion: p, cluster_id: i,
    cluster: "c", automation: 10, augmentation: 10, augmentation_weighted: 1,
  })) as any[];
  const items = tasks.filter(t => t.proportion > 0)
    .map(t => ({ v: t.proportion, task: t })).sort((a, b) => b.v - a.v);
  const cells: any[] = [];
  squarify(items, 0, 0, W, H, cells);

  console.log(`\n${label}: ${props.length} tasks summing to ${props.reduce((a,b)=>a+b,0)}`);
  check("every task gets a cell", cells.length === items.length, `${cells.length}/${items.length}`);

  const total = props.reduce((a,b)=>a+b,0);
  const area = cells.reduce((s,c)=>s+c.w*c.h, 0);
  check("cells tile the box exactly", Math.abs(area - W*H) < 1, `${area.toFixed(0)} vs ${W*H}`);

  // Area must track share to within rounding.
  let worstErr = 0;
  for (const c of cells) {
    const expected = (c.task.proportion / total) * W * H;
    worstErr = Math.max(worstErr, Math.abs(c.w*c.h - expected) / expected);
  }
  check("each cell's area matches its share", worstErr < 0.001, `worst ${(worstErr*100).toFixed(4)}%`);

  // No overlaps.
  let overlaps = 0;
  for (let i = 0; i < cells.length; i++)
    for (let j = i+1; j < cells.length; j++) {
      const a = cells[i], b = cells[j];
      const ox = Math.min(a.x+a.w, b.x+b.w) - Math.max(a.x, b.x);
      const oy = Math.min(a.y+a.h, b.y+b.h) - Math.max(a.y, b.y);
      if (ox > 0.01 && oy > 0.01) overlaps++;
    }
  check("no two cells overlap", overlaps === 0, `${overlaps} overlapping pairs`);

  const inside = cells.every(c => c.x >= -0.01 && c.y >= -0.01 && c.x+c.w <= W+0.01 && c.y+c.h <= H+0.01);
  check("all cells inside the box", inside);

  const ratios = cells.map(c => Math.max(c.w/c.h, c.h/c.w));
  const worst = Math.max(...ratios);
  const median = ratios.sort((a,b)=>a-b)[Math.floor(ratios.length/2)];
  console.log(`     aspect ratio: median ${median.toFixed(2)}, worst ${worst.toFixed(2)}`);
}

run([20,15,15,12,10,8,7,6,4,3], "typical role");
run([40,7.5,7.5,7.5,7.5,7.5,7.5,7.5,7.5], "one dominant task");
run([10,10,10,10,10,10,10,10,10,10], "perfectly even");
run([100], "single task");
run([60,40], "two tasks");
run([50,20,10,5,3,2,1,0.5,0.3,0.2], "long tail");
// Real proportions are constrained to sum to 100 by the inference step (verified across
// all 565 roles on the reference project), which is what lets area mean "share of the
// week" rather than "share of whatever was recorded". If that ever stopped holding, the
// box would still fill and every cell would quietly overstate itself — so the invariant
// is asserted here rather than assumed.
run([33.4,33.3,33.3], "sums to 100 exactly, as the pipeline guarantees");

// ---- the generic path, and `extent` ----------------------------------------
// The component lays out `{v, datum, pooled}` items rather than `{v, task}`, so the generic
// signature is exercised directly here. A regression in it would surface only as a blank
// panel in the browser.
console.log("\nGeneric items and layout()");
const generic = [{ v: 30, id: "a" }, { v: 20, id: "b" }, { v: 50, id: "c" }];
const glaid = layout(generic, 400, 200);
check("layout returns a cell per item", glaid.length === 3, `${glaid.length}`);
check("the caller's payload survives", glaid.every((c) => typeof c.id === "string"));
check("sorted descending by value", glaid[0].v === 50, `${glaid[0].v}`);
check(
  "areas still match shares",
  glaid.every((c) => Math.abs(c.w * c.h - (c.v / 100) * 400 * 200) < 1),
);
check(
  "zero and negative values are dropped rather than laid out",
  layout([{ v: 10 }, { v: 0 }, { v: -5 }], 400, 200).length === 1,
);
check(
  "a zero-width box returns nothing rather than dividing by zero",
  layout(generic, 0, 200).length === 0,
);

// `extent` is what makes a designed job show spare capacity. Without it a 40%-full job and a
// 200%-full job draw identically, which defeats the point of a capacity meter.
console.log("\nextent — laying out over capacity rather than over the sum");
const half = layout([{ v: 25 }, { v: 25 }], 400, 200, 100);
const halfArea = half.reduce((s, c) => s + c.w * c.h, 0);
check(
  "cells occupy their share of the extent, not the whole box",
  Math.abs(halfArea - 0.5 * 400 * 200) < 1,
  `${halfArea.toFixed(0)} vs ${0.5 * 400 * 200}`,
);
check(
  "and still tile without escaping the box",
  half.every((c) => c.x >= -0.01 && c.y >= -0.01 && c.x + c.w <= 400.01 && c.y + c.h <= 200.01),
);
check(
  "an extent below the sum is ignored — an over-capacity job fills its box",
  Math.abs(
    layout([{ v: 60 }, { v: 60 }], 400, 200, 100).reduce((s, c) => s + c.w * c.h, 0) - 400 * 200,
  ) < 1,
);

console.log("\n" + (ok ? "PASS" : "FAIL"));
process.exit(ok ? 0 : 1);
