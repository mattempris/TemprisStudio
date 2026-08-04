import { useEffect, useMemo, useRef, useState } from "react";
import * as d3 from "d3";
import { OPPORTUNITY_CEILING, opportunityColorIn } from "../../lib/heat";
import type { GraphCut, GraphNode } from "../../types/workforce";

/**
 * The work architecture, as a force-directed graph.
 *
 * Renders whatever cut the server returned — a few hundred nodes at most, never the
 * whole graph — so the layout stays legible and the simulation stays cheap at any
 * project size. Zoom, filtering and expansion happen server-side; this only draws.
 *
 * Colours come from the design tokens rather than literals, so the palettes carry
 * through to the graph instead of stopping at the canvas edge.
 *
 * **Layout: relationships, not columns.** An earlier version pinned each hierarchy to
 * its own x column, which read as three separate clouds and put the strongest
 * relationships — a job and the skills it needs — at opposite ends of the canvas with a
 * line between them. Now the link force does the arranging and jobs sit slightly left
 * of centre, so related things end up near each other and the clustering you can see is
 * the clustering in the data.
 *
 * **No labels.** At a few hundred nodes every label drawn overlaps another, and the
 * result is unreadable in a way that also hides the shape underneath it. Identity comes
 * from hovering, and from a tooltip that stays put when you click so it can be read and
 * compared rather than chased with the cursor.
 */

const ENTITY_TOKEN: Record<string, string> = {
  job: "--color-accent",
  skill: "--color-teal",
  task: "--color-purple",
  action: "--color-orange",
  process: "--color-brand",
  unmapped: "--color-warning",
};

/** Which radius scale a node belongs to. Actions share the task scale: an action's
 *  metric is a slice of its cluster's, and on its own scale the largest action of a
 *  tiny cluster would draw the same size as the largest task in the project. */
const SCALE_OF: Record<string, string> = { action: "task", unmapped: "process" };

/** How the fill is chosen. "opportunity" answers "where in this architecture is the
 *  AI opportunity", which is the whole reason step 3's scores reach the graph. */
export type ColorMode = "entity" | "opportunity";

// Deliberately small. The previous 5-24px range meant a few hundred nodes covered most
// of the canvas and the edges vanished underneath them; at this size the topology is
// what you see first and size is a secondary read.
// Roughly half a card's rendered height, used to keep one inside the canvas.
const CARD_HALF_HEIGHT = 60;

const R_MIN = 2.2;
const R_MAX = 9;

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

/** Whether a link endpoint is a node that belongs *inside* another rather than merely
 *  relating to it — an action within its task cluster, an unmapped step within its
 *  process. Those links are short and rigid; relationship links are weighted.
 *
 *  An endpoint is an id before the simulation resolves it and a node object after, so
 *  both forms are handled. */
function isStructural(endpoint: unknown): boolean {
  if (typeof endpoint === "string")
    return endpoint.startsWith("action:") || endpoint.startsWith("unmapped:");
  const e = (endpoint as GraphNode | undefined)?.entity;
  return e === "action" || e === "unmapped";
}

interface Simulated extends GraphNode {
  x?: number;
  y?: number;
  fx?: number | null;
  fy?: number | null;
}

/** An edge as the simulation holds it: endpoints start as ids and become nodes. */
interface GraphEdgeLike {
  source: string | Simulated;
  target: string | Simulated;
  weight: number;
}

function endpointId(e: string | Simulated): string {
  return typeof e === "string" ? e : e.id;
}

/**
 * Node ids within `degrees` hops of `from`, and the edges along the way.
 *
 * Breadth-first over the raw edge list rather than over the simulation's mutated
 * links, so it gives the same answer before the layout has initialised as after.
 * Undirected deliberately: "what is this connected to" does not care which way the
 * fact table happened to store the pair.
 */
function neighbourhood(
  links: GraphEdgeLike[],
  from: string,
  degrees: number,
): { nodes: Set<string>; edges: Set<GraphEdgeLike> } {
  const ends = links.map((l) => [endpointId(l.source), endpointId(l.target)] as const);
  const adjacency = new Map<string, string[]>();
  for (const [s, t] of ends) {
    (adjacency.get(s) ?? adjacency.set(s, []).get(s)!).push(t);
    (adjacency.get(t) ?? adjacency.set(t, []).get(t)!).push(s);
  }
  const nodes = new Set([from]);
  let frontier = [from];
  for (let d = 0; d < degrees; d++) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const other of adjacency.get(id) ?? []) {
        if (!nodes.has(other)) {
          nodes.add(other);
          next.push(other);
        }
      }
    }
    frontier = next;
    if (!frontier.length) break;
  }
  // Lit edges are the link *objects*, not string keys. Keying by `"source target"`
  // computed correctly in isolation and then matched nothing once the simulation was
  // live — the set has to be built from the very array bound to the DOM, or a
  // difference in when endpoints are resolved from ids to nodes breaks the lookup
  // silently. Object identity cannot drift.
  const lit = new Set<GraphEdgeLike>();
  links.forEach((l, i) => {
    const [s, t] = ends[i];
    // Both ends lit, so a 2-hop view shows the structure among the neighbours rather
    // than a star of spokes.
    if (nodes.has(s) && nodes.has(t)) lit.add(l);
  });
  return { nodes, edges: lit };
}

interface Hovered {
  node: GraphNode;
  /** Screen position, so the card can be placed without re-reading the DOM. */
  x: number;
  y: number;
  /** Pinned cards survive the pointer leaving, so they can be read and compared. */
  pinned: boolean;
}

export function ArchitectureGraph({
  cut,
  height = 620,
  onOpen,
  onExpand,
  paletteKey,
  colorMode = "entity",
  opportunitySpan = [0, OPPORTUNITY_CEILING],
  degrees = 1,
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
  /** How many hops from the selected node stay lit. */
  degrees?: number;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [cards, setCards] = useState<Hovered[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  // The d3 selections, kept so highlighting can repaint attributes without rebuilding
  // the simulation. Re-running the build effect on every click would restart the layout
  // and throw away the positions the user is looking at.
  const painted = useRef<{
    nodes: d3.Selection<SVGCircleElement, Simulated, SVGGElement, unknown>;
    links: d3.Selection<SVGLineElement, GraphEdgeLike, SVGGElement, unknown>;
  } | null>(null);
  // Measured, not assumed: the flip threshold for a card near the right edge has to be
  // this canvas's width, and a constant would put cards off-screen at other sizes.
  const [hostWidth, setHostWidth] = useState(900);

  // The simulation mutates its input, so it gets copies — otherwise React's props
  // acquire x/y/vx/vy and a re-render restarts the layout from wherever it was.
  const data = useMemo(
    () => ({
      nodes: cut.nodes.map((n) => ({ ...n })) as Simulated[],
      links: cut.edges.map((e) => ({ ...e })),
    }),
    [cut],
  );

  // A new cut invalidates every card: the nodes behind them may not be in it.
  useEffect(() => setCards([]), [data]);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const width = el.clientWidth || 900;
    setHostWidth(width);

    const colours: Record<string, string> = {
      job: token(ENTITY_TOKEN.job),
      skill: token(ENTITY_TOKEN.skill),
      task: token(ENTITY_TOKEN.task),
      action: token(ENTITY_TOKEN.action),
      edge: token("--color-border"),
      label: token("--color-text"),
      muted: token("--color-text-muted"),
    };

    // In opportunity mode an unassessed node is drawn muted rather than at the bottom
    // of the scale — "not measured" and "no opportunity" look identical otherwise, and
    // only one of them is a finding.
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
    // Pan and zoom are view-only; the server decides what is in the cut. The transform
    // is tracked so pinned cards can follow the nodes they belong to.
    let transform = d3.zoomIdentity;
    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.25, 8])
      .on("zoom", (e) => {
        transform = e.transform;
        root.attr("transform", transform.toString());
        placeCards();
      });
    svg.call(zoom);

    const maxWeight = d3.max(data.links, (l) => l.weight) ?? 1;
    const scaleKey = (n: Simulated) => SCALE_OF[n.entity] ?? n.entity;
    const perEntityMax = new Map<string, number>();
    for (const n of data.nodes) {
      const k = scaleKey(n);
      perEntityMax.set(k, Math.max(perEntityMax.get(k) ?? 0, n.metric));
    }
    const radius = (n: Simulated) =>
      d3.scaleSqrt().domain([0, perEntityMax.get(scaleKey(n)) || 1]).range([R_MIN, R_MAX])(n.metric);
    const stroke = d3.scaleLinear().domain([0, maxWeight]).range([0.3, 2.2]);

    const link = root
      .append("g")
      .attr("stroke", colours.edge)
      .attr("stroke-opacity", 0.45)
      .selectAll("line")
      .data(data.links)
      .join("line")
      .attr("stroke-width", (l) => stroke(l.weight));

    const node = root
      .append("g")
      .selectAll<SVGCircleElement, Simulated>("circle")
      .data(data.nodes)
      .join("circle")
      .attr("r", (n) => radius(n))
      .attr("fill", fill)
      .attr("fill-opacity", 0.9)
      .attr("stroke", (n) => (n.expanded ? colours.label : fill(n)))
      .attr("stroke-width", (n) => (n.expanded ? 1.6 : 0.6))
      .attr("cursor", "pointer");

    painted.current = {
      nodes: node,
      links: link as unknown as d3.Selection<SVGLineElement, GraphEdgeLike, SVGGElement, unknown>,
    };

    // ---- tooltips -------------------------------------------------------
    // Positions are recomputed from the simulation rather than captured on hover, so a
    // pinned card tracks its node while the layout settles, pans and zooms.
    const byId = new Map(data.nodes.map((n) => [n.id, n]));
    function screenOf(id: string): { x: number; y: number } | null {
      const n = byId.get(id);
      if (!n || n.x === undefined || n.y === undefined) return null;
      const [sx, sy] = transform.apply([n.x, n.y]);
      // The svg is drawn in viewBox units and scaled to the element, so convert.
      const k = (el?.clientWidth || width) / width;
      return { x: sx * k, y: sy * k };
    }
    function placeCards() {
      setCards((prev) =>
        prev
          .map((c) => {
            const p = screenOf(c.node.id);
            return p ? { ...c, x: p.x, y: p.y } : null;
          })
          .filter((c): c is Hovered => c !== null),
      );
    }

    node
      .on("pointerenter", (event: PointerEvent, n) => {
        const p = screenOf(n.id) ?? { x: event.offsetX, y: event.offsetY };
        setCards((prev) =>
          prev.some((c) => c.node.id === n.id)
            ? prev
            : [...prev.filter((c) => c.pinned), { node: n, x: p.x, y: p.y, pinned: false }],
        );
      })
      .on("pointerleave", (_e, n) => {
        setCards((prev) => prev.filter((c) => c.pinned || c.node.id !== n.id));
      })
      .on("click", (event: MouseEvent, n) => {
        // Click both pins the card and selects the node for highlighting; the actions
        // that used to be on click are on the card itself, where they are labelled.
        event.stopPropagation();
        const p = screenOf(n.id) ?? { x: event.offsetX, y: event.offsetY };
        setSelected((prev) => (prev === n.id ? null : n.id));
        setCards((prev) => {
          const existing = prev.find((c) => c.node.id === n.id);
          if (existing?.pinned) return prev.filter((c) => c.node.id !== n.id);
          return [
            ...prev.filter((c) => c.node.id !== n.id),
            { node: n, x: p.x, y: p.y, pinned: true },
          ];
        });
      });

    svg.on("click", () => {
      setCards([]);
      setSelected(null);
    });

    const sim = d3
      .forceSimulation<Simulated>(data.nodes)
      .force(
        "link",
        d3
          .forceLink<Simulated, (typeof data.links)[number]>(data.links)
          .id((n) => n.id)
          // An action-to-cluster link is structural, not a weighted relationship: the
          // action *is* part of that cluster. Short and rigid so opening a cluster reads
          // as its contents rather than as unrelated nodes appearing.
          .distance((l) => (isStructural(l.target) ? 18 : 70))
          // Capped well below 1. These cuts are dense — 36 nodes can carry 250 edges —
          // and at full strength every link pulls the whole thing into a single knot
          // that uses a fifth of the canvas. Weak links plus real repulsion is what
          // makes the clustering visible instead of collapsing it.
          .strength((l) =>
            isStructural(l.target) ? 1 : Math.min(0.5, l.weight / maxWeight + 0.04),
          ),
      )
      .force("charge", d3.forceManyBody().strength(-170).distanceMax(420))
      // Full strength, deliberately: this is what keeps the drawing in the middle of the
      // canvas. Weakened, the whole layout drifts to wherever it happened to start.
      .force("centre", d3.forceCenter(width / 2, height / 2))
      .force(
        "collide",
        d3.forceCollide<Simulated>().radius((n) => radius(n) + 1.2),
      )
      // A gentle nudge, not a column: jobs left of centre and their skills or tasks
      // right of it, so the picture has a direction while the link force is still what
      // decides who sits next to whom.
      .force(
        "x",
        d3
          .forceX<Simulated>((n) =>
            n.entity === "job" ? width * 0.34 : n.entity === "process" ? width * 0.86 : width * 0.62,
          )
          // Actions and unmapped steps get no column: their place is beside the thing
          // they belong to, which the structural link above already says.
          .strength((n) => (n.entity === "action" || n.entity === "unmapped" ? 0 : 0.08)),
      )
      .on("tick", () => {
        link
          .attr("x1", (l) => (l.source as unknown as Simulated).x ?? 0)
          .attr("y1", (l) => (l.source as unknown as Simulated).y ?? 0)
          .attr("x2", (l) => (l.target as unknown as Simulated).x ?? 0)
          .attr("y2", (l) => (l.target as unknown as Simulated).y ?? 0);
        node.attr("cx", (n) => n.x ?? 0).attr("cy", (n) => n.y ?? 0);
      })
      .on("end", placeCards);

    node.call(
      d3
        .drag<SVGCircleElement, Simulated>()
        .on("start", (e, n) => {
          if (!e.active) sim.alphaTarget(0.2).restart();
          n.fx = n.x;
          n.fy = n.y;
        })
        .on("drag", (e, n) => {
          n.fx = e.x;
          n.fy = e.y;
          placeCards();
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
    paletteKey,
    colorMode,
    cut.has_opportunity,
    opportunitySpan[0],
    opportunitySpan[1],
  ]);

  // A new cut means the selected node may be gone.
  useEffect(() => setSelected(null), [data]);

  // Highlighting is its own effect, touching only opacity and stroke. It must not depend
  // on anything that rebuilds the simulation, or selecting a node would restart the
  // layout and move everything the user was looking at.
  useEffect(() => {
    const p = painted.current;
    if (!p) return;
    if (!selected) {
      p.nodes.attr("fill-opacity", 0.9).attr("stroke-opacity", 1);
      p.links.attr("stroke-opacity", 0.45);
      return;
    }
    const { nodes: lit, edges: litEdges } = neighbourhood(data.links, selected, degrees);
    // Dimmed rather than hidden: the shape of the whole cut stays as context, so a
    // neighbourhood reads as part of something rather than as the only thing there is.
    p.nodes
      .attr("fill-opacity", (n) => (n.id === selected ? 1 : lit.has(n.id) ? 0.9 : 0.1))
      .attr("stroke-opacity", (n) => (lit.has(n.id) ? 1 : 0.12));
    p.links.attr("stroke-opacity", (l) => (litEdges.has(l) ? 0.8 : 0.04));
  }, [selected, degrees, data]);

  const litCount = useMemo(
    () => (selected ? neighbourhood(data.links, selected, degrees).nodes.size : 0),
    [selected, degrees, data],
  );

  return (
    <div className="relative">
      <div
        ref={host}
        className="w-full overflow-hidden rounded-[10px] border border-border bg-panel"
      />
      {selected && (
        <div className="pointer-events-auto absolute left-2 top-2 z-10 flex items-center gap-2 rounded-[8px] border border-accent-border bg-card px-2 py-1 shadow-[var(--shadow-card)]">
          <span className="text-[11px] text-text-secondary">
            <strong className="text-text">{litCount}</strong> nodes within {degrees}{" "}
            {degrees === 1 ? "hop" : "hops"}
          </span>
          <button
            onClick={() => {
              setSelected(null);
              setCards([]);
            }}
            className="text-[10.5px] font-semibold text-accent hover:underline"
          >
            clear
          </button>
        </div>
      )}
      {cards.map((c) => (
        <NodeCard
          key={c.node.id}
          card={c}
          hostWidth={hostWidth}
          canvasHeight={height}
          hasOpportunity={cut.has_opportunity}
          onExpand={() => onExpand(c.node)}
          onOpen={() => onOpen(c.node)}
          onClose={() => setCards((prev) => prev.filter((x) => x.node.id !== c.node.id))}
        />
      ))}
    </div>
  );
}

/**
 * The tooltip. Pinned ones get controls; hover ones are read-only.
 *
 * Positioned with a translate that flips near the right and bottom edges, so a node in
 * the corner does not get a card hanging off the canvas.
 */
function NodeCard({
  card,
  hostWidth,
  canvasHeight,
  hasOpportunity,
  onExpand,
  onOpen,
  onClose,
}: {
  card: Hovered;
  hostWidth: number;
  canvasHeight: number;
  hasOpportunity: boolean;
  onExpand: () => void;
  onOpen: () => void;
  onClose: () => void;
}) {
  const n = card.node;
  // Card is 14rem wide plus a 12px offset; flip when that would overflow the canvas.
  const flipX = card.x + 236 > hostWidth;
  // Clamped into the canvas. A node near the top edge otherwise put its card above the
  // graph and over the filter chips, which reads as a layout fault rather than a
  // tooltip. Half the card's height is the bound because it is centred on the node.
  const top = Math.min(Math.max(card.y, CARD_HALF_HEIGHT), canvasHeight - CARD_HALF_HEIGHT);
  return (
    <div
      // Class names are written out rather than interpolated: Tailwind generates styles
      // by scanning source text, so `pointer-events-${...}` produces no CSS at all.
      className={`absolute z-20 w-56 rounded-[8px] border bg-card px-2.5 py-2 shadow-[var(--shadow-modal)] ${
        card.pinned ? "pointer-events-auto border-accent" : "pointer-events-none border-border"
      }`}
      style={{
        left: card.x,
        top,
        transform: `translate(${flipX ? "calc(-100% - 12px)" : "12px"}, -50%)`,
      }}
    >
      <p className="text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
        {n.level_title}
      </p>
      <p className="mt-0.5 text-[12px] font-bold leading-snug text-text">{n.name}</p>
      {n.definition && (
        <p className="mt-0.5 text-[10.5px] leading-snug text-text-secondary">{n.definition}</p>
      )}
      <p className="mt-1 text-[11px] tabular-nums text-text-secondary">
        {n.metric} {n.metric_title}
        {n.leaves > 1 && ` · ${n.leaves} beneath`}
        {n.pct_of_task !== undefined && ` · ${n.pct_of_task}% of its cluster`}
      </p>
      {n.automation !== null && n.automation !== undefined ? (
        <p className="mt-0.5 text-[11px] tabular-nums text-text-secondary">
          {n.automation}% automatable · {n.augmentation}% augmentable
          {n.opportunity_coverage < 99 && (
            <span className="text-text-muted"> ({n.opportunity_coverage}% assessed)</span>
          )}
        </p>
      ) : (
        hasOpportunity && <p className="mt-0.5 text-[11px] text-text-muted">Not yet assessed</p>
      )}

      {card.pinned && (
        <div className="mt-1.5 flex items-center gap-1.5 border-t border-border pt-1.5">
          {n.expandable && (
            <button
              onClick={onExpand}
              className="rounded-[5px] border border-accent-border bg-accent-bg px-1.5 py-0.5 text-[10.5px] font-semibold text-accent transition-colors hover:bg-accent hover:text-white"
            >
              {n.expanded ? "Collapse" : "Open"}
            </button>
          )}
          <button
            onClick={onOpen}
            className="rounded-[5px] border border-border bg-card px-1.5 py-0.5 text-[10.5px] font-semibold text-text transition-colors hover:bg-panel"
          >
            Detail
          </button>
          <span className="flex-1" />
          <button
            onClick={onClose}
            aria-label="Close"
            className="px-1 text-[11px] text-text-muted transition-colors hover:text-text"
          >
            ✕
          </button>
        </div>
      )}
    </div>
  );
}
