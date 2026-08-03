import { useEffect, useMemo, useRef } from "react";
import * as d3 from "d3";
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
};

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
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
}: {
  cut: GraphCut;
  height?: number;
  onOpen: (node: GraphNode) => void;
  onExpand: (node: GraphNode) => void;
  /** Changes when the palette changes, so the colours are re-read. */
  paletteKey: string;
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

    const colours = {
      job: token(ENTITY_TOKEN.job),
      skill: token(ENTITY_TOKEN.skill),
      task: token(ENTITY_TOKEN.task),
      edge: token("--color-border"),
      label: token("--color-text"),
      muted: token("--color-text-muted"),
    };

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
    const perEntityMax = new Map<string, number>();
    for (const n of data.nodes) {
      perEntityMax.set(n.entity, Math.max(perEntityMax.get(n.entity) ?? 0, n.metric));
    }
    const radius = (n: Simulated) =>
      d3.scaleSqrt().domain([0, perEntityMax.get(n.entity) || 1]).range([5, 24])(n.metric);
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
      .attr("fill", (n) => colours[n.entity])
      .attr("fill-opacity", 0.85)
      .attr("stroke", (n) => (n.expanded ? colours.label : colours[n.entity]))
      .attr("stroke-width", (n) => (n.expanded ? 2 : 1));

    // Label only the largest few per hierarchy. At a few hundred nodes every label
    // drawn is a label overlapping another, and the tooltip covers the rest.
    const labelled = new Set(
      (["job", "skill", "task"] as const).flatMap((e) =>
        data.nodes
          .filter((n) => n.entity === e)
          .sort((a, b) => b.metric - a.metric)
          .slice(0, 8)
          .map((n) => n.id),
      ),
    );
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
          .distance(90)
          .strength((l) => Math.min(1, l.weight / maxWeight + 0.1)),
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
            n.entity === "job" ? width * 0.16 : n.entity === "task" ? width * 0.84 : width * 0.5,
          )
          .strength(0.3),
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
  }, [data, height, onOpen, onExpand, paletteKey]);

  return <div ref={host} className="w-full overflow-hidden rounded-[10px] border border-border bg-panel" />;
}
