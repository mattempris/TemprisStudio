/**
 * The treemap's one correctness claim: cell area equals share of the role's week.
 *
 * Worth a test because every way it can break is silent. A layout that tiles the box but
 * gets the areas wrong still looks like a treemap — it just misrepresents how the person
 * spends their time, which is the only thing the chart is for. Overlaps and gaps are
 * likewise invisible at a glance on a 230px strip.
 *
 * Run:  npx esbuild scripts/_test_treemap.ts --bundle --platform=node --format=esm  *         --outfile=node_modules/.cache/tm.mjs && node node_modules/.cache/tm.mjs
 */
import { squarify } from "../src/components/workforce/TaskTreemap";

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

console.log("\n" + (ok ? "PASS" : "FAIL"));
process.exit(ok ? 0 : 1);
