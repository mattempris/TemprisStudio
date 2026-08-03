import { useEffect, useMemo, useRef } from "react";
import * as d3 from "d3";
import { OPPORTUNITY_CEILING, opportunityColorIn } from "../../lib/heat";
import type { GraphCut, GraphNode } from "../../types/workforce";

/**
 * The work architecture, as a force-directed graph.
 *
 * Renders whatever cut the server returned — a few hundred nodes at most, never the
 * whole graph — so the layout stays legible and the simulation stays cheap at any
 * project size. Zoom and expansion happen server-side; this only draws.
 *
 * Colours come from the design tokens rather than literals, so the palettes carry
 * through to the graph instead of stopping at the canvas edge.
 */

const ENTITY_TOKEN: Record<string, string> = {
  job: "--color-accent",
  skill: "--color-teal",
  task: "--color-purple",
  action: "--color-orange",
};

/** Which radius scale a node belongs to. Actions share the task scale: an action's
 *  metric is a slice of its cluster's, and on its own scale the largest action of a
 *  tiny cluster would draw the same size as the largest task in the project. */
const SCALE_OF: Record<string, string> = { action: "task" };

/** How the fill is chosen. "opportunity" answers "where in this architecture is the
 *  AI opportunity", which is the whole reason step 3's scores reach the graph. */
export type ColorMode = "entity" | "opportunity";

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

/** A link endpoint is an id before the simulation resolves it and a node after. */
function isAction(endpoint: unknown): boolean {
  if (typeof endpoint === "string") return endpoint.startsWith("action:");
  return (endpoint as GraphNode | undefined)?.entity === "action";
}

interface Simulated extends GraphNode {
  x?: number;
  y?: number;
  fx?: number | null;
  fy?: number | null;
}

export function ArchitectureGraph({
  cut,
  height = 620,
  onOpen,
  onExpand,
  paletteKey,
  colorMode = "entity",
  opportunitySpan = [0, OPPORTUNITY_CEILING],
}: {
  cut: GraphCut;
  height?: number;
  onOpen: (node: GraphNode) => void;
  onExpand: (node: GraphNode) => void;
  /** Changes when the palette changes, so the colours are re-read. */
  paletteKey: string;
  colorMode?: ColorMode;
  /** Range the opportunity ramp covers. The legend must show these endpoints. */
  opportunitySpan?: [number, number];
}) {
  const host = useRef<HTMLDivElement>(null);

  // The simulation mutates its input, so it gets copies — otherwise React's props
  // acquire x/y/vx/vy and a re-render restarts the layout from wherever it was.
  const data = useMemo(
    () => ({
      nodes: cut.nodes.map((n) => ({ ...n })) as Simulated[],
      links: cut.edges.map((e) => ({ ...e })),
    }),
    [cut],
  );

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const width = el.clientWidth || 900;

    const colours: Record<string, string> = {
      job: token(ENTITY_TOKEN.job),
      skill: token(ENTITY_TOKEN.skill),
      task: token(ENTITY_TOKEN.task),
      action: token(ENTITY_TOKEN.action),
      edge: token("--color-border"),
      label: token("--color-text"),
      muted: token("--color-text-muted"),
    };

    // In opportunity mode an unassessed node is drawn muted rather than at the
    // bottom of the scale — "not measured" and "no opportunity" look identical
    // otherwise, and only one of them is a finding.
    const fill = (n: Simulated) =>
      colorMode === "opportunity"
        ? n.automation === null || n.automation === undefined
          ? colours.muted
          : opportunityColorIn(n.automation, opportunitySpan[0], opportunitySpan[1])
        : colours[n.entity];

    d3.select(el).selectAll("*").remove();
    const svg = d3
      .select(el)
      .append("svg")
      .attr("width", "100%")
      .attr("height", height)
      .attr("viewBox", `0 0 ${width} ${height}`);

    const root = svg.append("g");
    // Pan and zoom are view-only; the server decides what is in the cut.
    svg.call(
      d3
        .zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.25, 6])
        .on("zoom", (e) => root.attr("transform", e.transform.toString())),
    );

    const maxWeight = d3.max(data.links, (l) => l.weight) ?? 1;
    // One radius scale per hierarchy. The three metrics are different quantities —
    // 159 jobs against 1,036 skills against 54 FTE — so a shared scale renders the
    // job side as dots and says nothing true in the process. Within a hierarchy the
    // comparison is meaningful, which is the only place size should be compared.
    const scaleKey = (n: Simulated) => SCALE_OF[n.entity] ?? n.entity;
    const perEntityMax = new Map<string, number>();
    for (const n of data.nodes) {
      const k = scaleKey(n);
      perEntityMax.set(k, Math.max(perEntityMax.get(k) ?? 0, n.metric));
    }
    const radius = (n: Simulated) =>
      d3.scaleSqrt().domain([0, perEntityMax.get(scaleKey(n)) || 1]).range([5, 24])(n.metric);
    const stroke = d3.scaleLinear().domain([0, maxWeight]).range([0.4, 4]);

    const link = root
      .append("g")
      .attr("stroke", colours.edge)
      .attr("stroke-opacity", 0.55)
      .selectAll("line")
      .data(data.links)
      .join("line")
      .attr("stroke-width", (l) => stroke(l.weight));

    const node = root
      .append("g")
      .selectAll<SVGGElement, Simulated>("g")
      .data(data.nodes)
      .join("g")
      .attr("cursor", "pointer");

    node
      .append("circle")
      .attr("r", (n) => radius(n))
      .attr("fill", fill)
      .attr("fill-opacity", 0.85)
      .attr("stroke", (n) => (n.expanded ? colours.label : fill(n)))
      .attr("stroke-width", (n) => (n.expanded ? 2 : 1));

    // Label only the largest few per hierarchy. At a few hundred nodes every label
    // drawn is a label overlapping another, and the tooltip covers the rest. Actions
    // are always labelled: there are at most a handful, they only exist because
    // someone opened that cluster, and their names are the answer to why.
    const labelled = new Set([
      ...(["job", "skill", "task"] as const).flatMap((e) =>
        data.nodes
          .filter((n) => n.entity === e)
          .sort((a, b) => b.metric - a.metric)
          .slice(0, 8)
          .map((n) => n.id),
      ),
      ...data.nodes.filter((n) => n.entity === "action").map((n) => n.id),
    ]);
    node
      .filter((n) => labelled.has(n.id))
      .append("text")
      .text((n) => (n.name.length > 24 ? `${n.name.slice(0, 23)}…` : n.name))
      .attr("x", (n) => radius(n) + 4)
      .attr("y", 4)
      .attr("font-size", 10)
      .attr("font-weight", 600)
      .attr("fill", colours.label)
      .attr("pointer-events", "none");

    node.append("title").text(
      (n) =>
        `${n.level_title}: ${n.name}\n${n.metric} ${n.metric_title}` +
        (n.leaves > 1 ? ` · ${n.leaves} beneath` : "") +
        (n.pct_of_task !== undefined ? ` · ${n.pct_of_task}% of its cluster` : "") +
        (n.automation !== null && n.automation !== undefined
          ? `\n${n.automation}% automatable · ${n.augmentation}% augmentable` +
            (n.opportunity_coverage < 99 ? ` (${n.opportunity_coverage}% assessed)` : "")
          : cut.has_opportunity
            ? "\nnot yet assessed"
            : "") +
        (n.expandable ? "\n\nclick to open · shift-click for detail" : "\n\nclick for detail"),
    );

    node.on("click", (event: MouseEvent, n) => {
      // Expanding is the common action on a group, so it is the plain click; detail
      // is a modifier. On a leaf there is nothing to expand, so click opens detail.
      if (n.expandable && !event.shiftKey) onExpand(n);
      else onOpen(n);
    });

    const sim = d3
      .forceSimulation<Simulated>(data.nodes)
      .force(
        "link",
        d3
          .forceLink<Simulated, (typeof data.links)[number]>(data.links)
          .id((n) => n.id)
          // An action-to-cluster link is structural, not a weighted relationship: the
          // action *is* part of that cluster. Weighting it like the rest left actions
          // adrift in open space — at the finest cut there are 10,000 other links, so
          // a 40%-of-task weight resolved to near the minimum strength and the column
          // force won. Short and rigid instead, so opening a cluster reads as its
          // contents rather than as unrelated nodes appearing.
          .distance((l) => (isAction(l.target) ? 34 : 90))
          .strength((l) =>
            isAction(l.target) ? 1 : Math.min(1, l.weight / maxWeight + 0.1),
          ),
      )
      .force("charge", d3.forceManyBody().strength(-320).distanceMax(420))
      // No forceCenter: with a per-entity x force it fights the columns and pulls
      // all three hierarchies into one overlapping ball in the middle.
      .force("y", d3.forceY(height / 2).strength(0.13))
      .force(
        "collide",
        d3.forceCollide<Simulated>().radius((n) => radius(n) + 4),
      )
      // Pull each hierarchy towards its own column, so the picture reads as
      // jobs-need-skills-and-tasks rather than one undifferentiated cloud.
      .force(
        "x",
        d3
          .forceX<Simulated>((n) =>
            n.entity === "job" ? width * 0.16 : n.entity === "task" ? width * 0.82 : width * 0.5,
          )
          // Actions are exempt from the columns. Their place in the picture is "next to
          // my cluster", which the link above already says; a column force would only
          // fight it.
          .strength((n) => (n.entity === "action" ? 0 : 0.3)),
      )
      .on("tick", () => {
        link
          .attr("x1", (l) => (l.source as unknown as Simulated).x ?? 0)
          .attr("y1", (l) => (l.source as unknown as Simulated).y ?? 0)
          .attr("x2", (l) => (l.target as unknown as Simulated).x ?? 0)
          .attr("y2", (l) => (l.target as unknown as Simulated).y ?? 0);
        node.attr("transform", (n) => `translate(${n.x ?? 0},${n.y ?? 0})`);
      });

    node.call(
      d3
        .drag<SVGGElement, Simulated>()
        .on("start", (e, n) => {
          if (!e.active) sim.alphaTarget(0.2).restart();
          n.fx = n.x;
          n.fy = n.y;
        })
        .on("drag", (e, n) => {
          n.fx = e.x;
          n.fy = e.y;
        })
        .on("end", (e, n) => {
          if (!e.active) sim.alphaTarget(0);
          n.fx = null;
          n.fy = null;
        }),
    );

    return () => {
      sim.stop();
      d3.select(el).selectAll("*").remove();
    };
  }, [
    data,
    height,
    onOpen,
    onExpand,
    paletteKey,
    colorMode,
    cut.has_opportunity,
    opportunitySpan[0],
    opportunitySpan[1],
  ]);

  return <div ref={host} className="w-full overflow-hidden rounded-[10px] border border-border bg-panel" />;
}
