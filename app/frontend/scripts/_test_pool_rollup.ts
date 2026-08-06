/**
 * The rollup's correctness claims. All of them are silent when broken.
 *
 * A rolled-up pool that sums hours wrongly still draws a plausible treemap — it just
 * misrepresents where the work is, which is the only thing the panel is for. And averaging a
 * rate the wrong way, or counting an unassessed cluster as zero, produces a number that looks
 * fine and is not: the whole app keeps "unknown opportunity" and "no opportunity" apart, and
 * this is the one place a group could quietly collapse them.
 *
 * Run:  npx esbuild scripts/_test_pool_rollup.ts --bundle --platform=node --format=esm
 *         --outfile=node_modules/.cache/pr.mjs && node node_modules/.cache/pr.mjs
 */
import { levelOf, rollup, scopeOf } from "../src/lib/poolRollup";
import type { PoolCluster } from "../src/types/workDesign";

let ok = true;
const check = (label: string, cond: boolean, detail = "") => {
  ok = ok && cond;
  console.log(`  ${cond ? "OK  " : "FAIL"}  ${label}${detail ? " — " + detail : ""}`);
};
const near = (a: number, b: number, eps = 1e-9) => Math.abs(a - b) < eps;

function c(
  cluster_id: number,
  domain_id: number,
  category_id: number,
  hours: number,
  automation: number | null,
  n_roles = 3,
): PoolCluster {
  return {
    cluster_id,
    name: `Cluster ${cluster_id}`,
    category_id,
    category: `Category ${category_id}`,
    domain_id,
    domain: `Domain ${domain_id}`,
    hours_per_week: hours,
    fte: null,
    share_pct: 0,
    hours_per_holder_week: hours / 100,
    assessed: automation !== null,
    automation,
    augmentation: automation === null ? null : automation / 2,
    n_roles,
    roles: [],
  };
}

// Two domains. Domain 1 has two categories; domain 2 has one cluster only.
const POOL: PoolCluster[] = [
  c(10, 1, 100, 300, 40),
  c(11, 1, 100, 100, 80),
  c(12, 1, 101, 200, 20),
  c(20, 2, 200, 400, 60),
];
const TOTAL = 1000;

console.log("\nHours sum up the taxonomy, and nothing is lost or double-counted");
{
  const domains = rollup(POOL, {});
  check("one node per domain", domains.length === 2, `${domains.length}`);
  check(
    "domain hours total the pool",
    near(domains.reduce((s, n) => s + n.hours_per_week, 0), TOTAL),
  );
  check("biggest first", domains[0].hours_per_week >= domains[1].hours_per_week);
  const d1 = domains.find((n) => n.id === 1)!;
  check("domain 1 sums its three clusters", near(d1.hours_per_week, 600), `${d1.hours_per_week}`);
  check("and carries them as leaves", d1.leaves.length === 3);

  const cats = rollup(POOL, { domainId: 1 });
  check(
    "its categories total the same 600",
    near(cats.reduce((s, n) => s + n.hours_per_week, 0), 600),
  );
  const leaves = rollup(POOL, { domainId: 1, categoryId: 100 });
  check("and the deepest level is the real clusters", leaves.length === 2);
  check(
    "which total their category",
    near(leaves.reduce((s, n) => s + n.hours_per_week, 0), 400),
  );
}

console.log("\nShare renormalises on the way in, because that is the useful number there");
{
  const d1 = rollup(POOL, {}).find((n) => n.id === 1)!;
  check("domain 1 is 60% of the pool", near(d1.share_pct, 60), `${d1.share_pct}`);
  const cat100 = rollup(POOL, { domainId: 1 }).find((n) => n.id === 100)!;
  // 400 of domain 1's 600, not 400 of the pool's 1000. Inside a domain, "of this domain" is
  // what a reader is comparing against.
  check("category 100 is 67% of its domain, not 40% of the pool", near(cat100.share_pct, 400 / 6));
  const shares = rollup(POOL, { domainId: 1 }).reduce((s, n) => s + n.share_pct, 0);
  check("shares at a level sum to 100", near(shares, 100));
}

console.log("\nA score is a rate, so it is weighted by hours — never a plain mean");
{
  const d1 = rollup(POOL, {}).find((n) => n.id === 1)!;
  // (300x40 + 100x80 + 200x20) / 600 = 24000/600 = 40. The plain mean would be 46.7, which
  // would credit the small 80%-automatable cluster with the same say as the big 300h one.
  check("hours-weighted, not averaged", near(d1.automation!, 40), `${d1.automation}`);
  check("plain mean would have said 46.7", !near(d1.automation!, (40 + 80 + 20) / 3));
}

console.log("\nUnassessed is unknown, not zero — the distinction the whole app keeps");
{
  const withGap = [c(30, 3, 300, 100, 50), c(31, 3, 300, 900, null)];
  const d3 = rollup(withGap, {})[0];
  check("hours still include the unassessed cluster", near(d3.hours_per_week, 1000));
  // Zero-filling would give 5.0 here, which reads as "barely automatable" about work nobody
  // has looked at.
  check("the score is the assessed part only", near(d3.automation!, 50), `${d3.automation}`);
  check("and the gap is reported", d3.assessed_leaves === 1 && d3.leaves.length === 2);

  const allUnknown = [c(40, 4, 400, 100, null), c(41, 4, 400, 50, null)];
  const d4 = rollup(allUnknown, {})[0];
  check("a group with nothing assessed reports null, never 0", d4.automation === null);
}

console.log("\nA group of one has nothing to drill into");
{
  const d2 = rollup(POOL, {}).find((n) => n.id === 2)!;
  check("domain 2 holds a single cluster", d2.leaves.length === 1);
  check("so it is not drillable — clicking it would show the same tile again", !d2.drillable);
  const d1 = rollup(POOL, {}).find((n) => n.id === 1)!;
  check("domain 1 is", d1.drillable);
  check("and a leaf never is", !rollup(POOL, { domainId: 1, categoryId: 100 })[0].drillable);
}

console.log("\nThe path decides the level and the scope");
{
  check("empty path is the domain level", levelOf({}) === "domain");
  check("a domain opens categories", levelOf({ domainId: 1 }) === "category");
  check("a category opens clusters", levelOf({ domainId: 1, categoryId: 100 }) === "cluster");
  check("scope narrows with the path", scopeOf(POOL, { domainId: 1 }).length === 3);
  check("and narrows again", scopeOf(POOL, { domainId: 1, categoryId: 101 }).length === 1);
  check("a path that matches nothing is empty, not everything", scopeOf(POOL, { domainId: 99 }).length === 0);
}

console.log("\nA holder's share sums the same way hours do");
{
  const d1 = rollup(POOL, {}).find((n) => n.id === 1)!;
  check(
    "so dragging a domain in adds what its clusters would",
    near(d1.hours_per_holder_week, 600 / 100),
    `${d1.hours_per_holder_week}`,
  );
}

console.log("\nDegenerate input does not throw");
{
  check("an empty pool rolls up to nothing", rollup([], {}).length === 0);
  const zero = rollup([c(50, 5, 500, 0, 30)], {})[0];
  check("a zero-hour cluster has no share rather than NaN", zero.share_pct === 0);
  // Weighting by hours leaves nothing to weight with, so fall back rather than report unknown
  // about a cluster that was in fact assessed.
  check("but keeps its score", near(zero.automation!, 30), `${zero.automation}`);
  const missing = rollup([{ ...c(60, -1, -1, 10, null), domain: "", category: "" }], {})[0];
  check("a cluster with no taxonomy row is labelled honestly", missing.name === "Uncategorised domain");
}

console.log(ok ? "\nPASS\n" : "\nFAIL\n");
process.exit(ok ? 0 : 1);
