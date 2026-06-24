import { useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import ForceGraph3D from "react-force-graph-3d";
import type { ForceGraphMethods, LinkObject, NodeObject } from "react-force-graph-3d";
import * as THREE from "three";
import { fetchDag, openDag } from "../api";
import type { DagEdge, DagNode, DagNodeReadingStatus, DagNodeStatus, DagPayload } from "../api";

const GENERATION_STATUS_ORDER: DagNodeStatus[] = ["locked", "ready", "running", "complete", "failed"];
const READING_STATUS_ORDER: DagNodeReadingStatus[] = ["unread", "recommended", "reading", "read"];
type VisualStatus = DagNodeStatus | DagNodeReadingStatus;
type DagFilter = VisualStatus | "all";

const VISUAL_META: Record<
  VisualStatus,
  { label: string; color: string; glow: string; description: string }
> = {
  locked: {
    label: "Locked",
    color: "#8b8b83",
    glow: "#4a4a44",
    description: "Waiting for prerequisites",
  },
  ready: {
    label: "Ready",
    color: "#b7dca5",
    glow: "#8fbd78",
    description: "Ready to grow",
  },
  running: {
    label: "Running",
    color: "#2f6f44",
    glow: "#5fa774",
    description: "Active NodeRun",
  },
  complete: {
    label: "Complete",
    color: "#8a5a32",
    glow: "#c08a52",
    description: "Covered by output",
  },
  failed: {
    label: "Failed",
    color: "#b23b3b",
    glow: "#e08585",
    description: "Needs attention",
  },
  unread: {
    label: "Unread",
    color: "#b7dca5",
    glow: "#8fbd78",
    description: "Generated and ready to read",
  },
  recommended: {
    label: "Recommended",
    color: "#2f6f44",
    glow: "#5fa774",
    description: "Suggested by prerequisite order",
  },
  reading: {
    label: "Reading",
    color: "#2f6f44",
    glow: "#5fa774",
    description: "Opened for study",
  },
  read: {
    label: "Read",
    color: "#8a5a32",
    glow: "#c08a52",
    description: "Marked complete by learner",
  },
};

type DagGraphNode = NodeObject<DagNode> & DagNode & {
  name: string;
  val: number;
  color: string;
  visualStatus: VisualStatus;
};

type DagGraphLink = LinkObject<DagGraphNode, DagEdge> & {
  source: string;
  target: string;
  visualStatus: VisualStatus;
  required_defines: string[];
};

type GraphRef = ForceGraphMethods<DagGraphNode, DagGraphLink> | undefined;
type PositionKey = "x" | "y" | "z" | "vx" | "vy" | "vz";
type DagNodePosition = Partial<Record<PositionKey, number>>;

const POSITION_KEYS: PositionKey[] = ["x", "y", "z", "vx", "vy", "vz"];

interface DagWorkbenchProps {
  selectedNodeId?: string;
  onSelectedNodeChange?: (id: string) => void;
  onReadOutput?: (name: string, nodeId: string) => void;
}

export function DagWorkbench({
  selectedNodeId = "",
  onSelectedNodeChange = () => undefined,
  onReadOutput,
}: DagWorkbenchProps) {
  const [dag, setDag] = useState<DagPayload | null>(null);
  const [filter, setFilter] = useState<DagFilter>("all");
  const [error, setError] = useState<string>("");
  const [svgMessage, setSvgMessage] = useState<string>("");
  const graphRef = useRef<GraphRef>(undefined);
  const graphSignatureRef = useRef<string>("");
  const initialFitDoneRef = useRef<boolean>(false);
  const positionsRef = useRef<Map<string, DagNodePosition>>(new Map());
  const stageRef = useRef<HTMLDivElement | null>(null);
  const size = useElementSize(stageRef);
  const reducedMotion = usePrefersReducedMotion();
  const selectedId = selectedNodeId;
  const learningReady = Boolean(dag?.learning_ready);
  const activeOrder = useMemo<VisualStatus[]>(
    () => (learningReady ? READING_STATUS_ORDER : GENERATION_STATUS_ORDER),
    [learningReady],
  );
  const filters = useMemo<DagFilter[]>(() => ["all", ...activeOrder], [activeOrder]);

  useEffect(() => {
    let active = true;
    const load = (): void => {
      fetchDag()
        .then((data) => {
          if (!active) return;
          const signature = dagSignature(data);
          if (signature !== graphSignatureRef.current) {
            graphSignatureRef.current = signature;
            setDag(data);
          }
          setError("");
        })
        .catch((err: unknown) => {
          if (active) setError(err instanceof Error ? err.message : String(err));
        });
    };
    load();
    const timer = window.setInterval(load, 2500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const graph = useMemo(() => buildGraph(dag, positionsRef.current), [dag]);
  const visibleIds = useMemo(() => {
    if (filter === "all") return new Set(graph.nodes.map((node) => String(node.id)));
    return new Set(
      graph.nodes.filter((node) => node.visualStatus === filter).map((node) => String(node.id)),
    );
  }, [filter, graph.nodes]);
  const selected = selectedId ? graph.byId.get(selectedId) : undefined;

  useEffect(() => {
    if (filter !== "all" && !activeOrder.includes(filter)) setFilter("all");
  }, [activeOrder, filter]);

  useEffect(() => {
    if (selectedId && !graph.byId.has(selectedId)) onSelectedNodeChange("");
  }, [graph.byId, onSelectedNodeChange, selectedId]);

  useEffect(() => {
    if (graph.nodes.length === 0) {
      initialFitDoneRef.current = false;
      positionsRef.current.clear();
    }
  }, [graph.nodes.length]);

  const focusNode = (node: DagGraphNode): void => {
    onSelectedNodeChange(String(node.id));
    setSvgMessage("");
    const distance = 72;
    const x = Number(node.x ?? 0);
    const y = Number(node.y ?? 0);
    const z = Number(node.z ?? 0);
    const length = Math.hypot(x, y, z) || 1;
    const ratio = 1 + distance / length;
    graphRef.current?.cameraPosition(
      { x: x * ratio, y: y * ratio, z: z * ratio },
      { x, y, z },
      reducedMotion ? 0 : 900,
    );
  };

  const focusNodeId = (id: string): void => {
    const node = graph.byId.get(id);
    if (node) focusNode(node);
  };

  const runOpenSvg = async (): Promise<void> => {
    setSvgMessage("");
    try {
      setSvgMessage(await openDag());
    } catch (err) {
      setSvgMessage(err instanceof Error ? err.message : String(err));
    }
  };

  const resetView = (): void => {
    onSelectedNodeChange("");
    graphRef.current?.cameraPosition(
      { x: 0, y: 0, z: 420 },
      { x: 0, y: 0, z: 0 },
      reducedMotion ? 0 : 700,
    );
  };

  const fitView = (): void => {
    onSelectedNodeChange("");
    graphRef.current?.zoomToFit(reducedMotion ? 0 : 650, 48, (node) =>
      visibleIds.has(String(node.id)),
    );
  };

  const handleEngineTick = (): void => {
    cacheNodePositions(graph.nodes, positionsRef.current);
  };

  const handleEngineStop = (): void => {
    cacheNodePositions(graph.nodes, positionsRef.current);
    if (initialFitDoneRef.current || graph.nodes.length === 0) return;
    initialFitDoneRef.current = true;
    graphRef.current?.zoomToFit(reducedMotion ? 0 : 650, 56, (node) =>
      visibleIds.has(String(node.id)),
    );
  };

  return (
    <section className="dag-workbench" aria-label="Knowledge graph">
      <aside className="dag-rail" aria-label="Knowledge graph filters">
        <div>
          <h2>知识图谱</h2>
          <p className="muted">{learningReady ? "Learning progress" : "Knowledge generation"}</p>
        </div>
        <div className="dag-stats">
          <span>
            <b>{graph.nodes.length}</b>
            nodes
          </span>
          <span>
            <b>{graph.links.length}</b>
            edges
          </span>
        </div>
        <div className="dag-filter-list">
          {filters.map((item) => (
            <button
              key={item}
              type="button"
              className={`dag-filter ${filter === item ? "active" : ""}`}
              onClick={() => setFilter(item)}
            >
              <span
                className={`dag-dot ${item === "all" ? "dag-dot-all" : `dag-dot-${item}`}`}
              />
              <span>{item === "all" ? "All" : VISUAL_META[item].label}</span>
              <b>{item === "all" ? graph.nodes.length : graph.counts[item]}</b>
            </button>
          ))}
        </div>
        {dag?.updated_at && <p className="muted">Updated {dag.updated_at}</p>}
      </aside>

      <div ref={stageRef} className="dag-stage">
        {error && <div className="dag-alert">{error}</div>}
        {graph.nodes.length === 0 ? (
          <div className="dag-empty">
            <h2>No DAG yet</h2>
            <p className="muted">Run the pipeline until planner nodes are generated.</p>
          </div>
        ) : (
          <ForceGraph3D<DagGraphNode, DagGraphLink>
            ref={graphRef}
            width={size.width}
            height={size.height}
            graphData={{ nodes: graph.nodes, links: graph.links }}
            nodeId="id"
            linkSource="source"
            linkTarget="target"
            dagMode="bu"
            dagLevelDistance={82}
            dagNodeFilter={(node) => visibleIds.has(String(node.id))}
            onDagError={() => undefined}
            backgroundColor="rgba(0,0,0,0)"
            showNavInfo={false}
            controlType="orbit"
            enableNodeDrag={false}
            nodeVal={(node) => node.val}
            nodeLabel={(node) => nodeTooltip(node)}
            nodeColor={(node) => node.color}
            nodeOpacity={0.92}
            nodeResolution={22}
            nodeVisibility={(node) => visibleIds.has(String(node.id))}
            nodeThreeObject={(node: DagGraphNode) => makeNodeObject(node, node.id === selectedId)}
            nodeThreeObjectExtend
            linkVisibility={(link) => visibleIds.has(endpointId(link.source)) && visibleIds.has(endpointId(link.target))}
            linkColor={(link) => VISUAL_META[link.visualStatus].glow}
            linkWidth={(link) => (isActiveVisual(link.visualStatus) ? 3.3 : 1.6)}
            linkOpacity={0.42}
            linkDirectionalParticles={(link) => {
              if (reducedMotion) return 0;
              if (isActiveVisual(link.visualStatus)) return 5;
              if (isPositiveVisual(link.visualStatus)) return 2;
              return 0;
            }}
            linkDirectionalParticleSpeed={(link) => (isActiveVisual(link.visualStatus) ? 0.012 : 0.006)}
            linkDirectionalParticleWidth={(link) => (isActiveVisual(link.visualStatus) ? 3.6 : 2.2)}
            linkDirectionalParticleColor={(link) => VISUAL_META[link.visualStatus].glow}
            linkDirectionalArrowLength={4.5}
            linkDirectionalArrowRelPos={0.96}
            linkDirectionalArrowColor={(link) => VISUAL_META[link.visualStatus].glow}
            cooldownTicks={80}
            onEngineTick={handleEngineTick}
            onEngineStop={handleEngineStop}
            onNodeClick={(node) => focusNode(node)}
            onBackgroundClick={() => onSelectedNodeChange("")}
          />
        )}

        <div className="dag-canvas-hint">Scroll to zoom · drag to rotate · right-drag to pan</div>
        <div className="dag-view-controls">
          <button type="button" className="ghost" onClick={fitView}>
            Fit
          </button>
          <button type="button" className="ghost" onClick={resetView}>
            Reset
          </button>
          <button type="button" className="ghost" onClick={() => void runOpenSvg()}>
            Open SVG
          </button>
        </div>
        {svgMessage && (
          <span className="dag-svg-message" dangerouslySetInnerHTML={{ __html: svgMessage }} />
        )}
      </div>

      <NodeInspector
        node={selected}
        graph={graph}
        onFocus={focusNodeId}
        onReadOutput={onReadOutput}
      />
    </section>
  );
}

function NodeInspector({
  node,
  graph,
  onFocus,
  onReadOutput,
}: {
  node?: DagGraphNode;
  graph: DagGraph;
  onFocus: (id: string) => void;
  onReadOutput?: (name: string, nodeId: string) => void;
}) {
  const prerequisites = node
    ? node.prerequisites.map((id) => graph.byId.get(id)).filter((item): item is DagGraphNode => Boolean(item))
    : [];
  const dependents = node
    ? node.dependents.map((id) => graph.byId.get(id)).filter((item): item is DagGraphNode => Boolean(item))
    : [];

  return (
    <aside className="dag-inspector" aria-label="Selected DAG node">
      {node ? (
        <>
          <div className="inspector-head">
            <span className={`dag-state state-${node.visualStatus}`}>{VISUAL_META[node.visualStatus].label}</span>
            <span className="muted">{node.label}</span>
          </div>
          <h2>{node.title}</h2>
          <p className="muted">{VISUAL_META[node.visualStatus].description}</p>
          {node.recommended && node.recommendation_reason && (
            <p className="ok">{node.recommendation_reason}</p>
          )}
          {node.affected_by_feedback && (
            <p className="hint">A prerequisite was revised after this node was marked read.</p>
          )}
          {node.last_feedback_error && <p className="errors">{node.last_feedback_error}</p>}
          {node.summary && <p>{node.summary}</p>}
          <InspectorList title="Prerequisites" nodes={prerequisites} onFocus={onFocus} />
          <InspectorList title="Dependents" nodes={dependents} onFocus={onFocus} />
          <TagList title="Defines" items={node.defines} />
          <TagList title="Collections" items={node.collections} />
          <OutputActions node={node} onReadOutput={onReadOutput} />
        </>
      ) : (
        <>
          <h2>Select a node</h2>
          <p className="muted">
            Click a bud in the tree to fly closer and inspect prerequisites, defines, and outputs.
          </p>
          <div className="dag-legend">
            {GENERATION_STATUS_ORDER.map((status) => (
              <span key={status}>
                <i className={`dag-dot dag-dot-${status}`} />
                {VISUAL_META[status].label}
              </span>
            ))}
            {READING_STATUS_ORDER.map((status) => (
              <span key={status}>
                <i className={`dag-dot dag-dot-${status}`} />
                {VISUAL_META[status].label}
              </span>
            ))}
          </div>
        </>
      )}
    </aside>
  );
}

function OutputActions({
  node,
  onReadOutput,
}: {
  node: DagGraphNode;
  onReadOutput?: (name: string, nodeId: string) => void;
}) {
  const outputs = node.output_paths.map(outputNameFromPath).filter(Boolean);
  const disabledMessage =
    node.generation_status !== "complete"
      ? "Output not ready"
      : outputs.length === 0
        ? "No output file found"
        : "";

  return (
    <div className="dag-inspector-section">
      <h3>Outputs</h3>
      {disabledMessage ? (
        <button type="button" className="ghost output-action" disabled>
          {disabledMessage}
        </button>
      ) : (
        <div className="output-action-list">
          {outputs.map((name, index) => (
            <button
              key={`${name}-${index}`}
              type="button"
              className={index === 0 ? "" : "ghost"}
              onClick={() => onReadOutput?.(name, String(node.id))}
            >
              {outputs.length === 1 ? "Start Learning" : `Read ${name}`}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function InspectorList({
  title,
  nodes,
  onFocus,
}: {
  title: string;
  nodes: DagGraphNode[];
  onFocus: (id: string) => void;
}) {
  return (
    <div className="dag-inspector-section">
      <h3>{title}</h3>
      {nodes.length ? (
        <div className="node-link-list">
          {nodes.map((node) => (
            <button key={node.id} type="button" className="node-link" onClick={() => onFocus(String(node.id))}>
              <span className={`dag-dot dag-dot-${node.visualStatus}`} />
              {node.label}
            </button>
          ))}
        </div>
      ) : (
        <p className="muted">None</p>
      )}
    </div>
  );
}

function TagList({ title, items, empty = "None" }: { title: string; items: string[]; empty?: string }) {
  return (
    <div className="dag-inspector-section">
      <h3>{title}</h3>
      {items.length ? (
        <div className="tag-list">
          {items.map((item) => (
            <span key={item} className="tag">
              {item}
            </span>
          ))}
        </div>
      ) : (
        <p className="muted">{empty}</p>
      )}
    </div>
  );
}

interface DagGraph {
  nodes: DagGraphNode[];
  links: DagGraphLink[];
  byId: Map<string, DagGraphNode>;
  counts: Record<VisualStatus, number>;
}

function buildGraph(
  dag: DagPayload | null,
  positions: Map<string, DagNodePosition>,
): DagGraph {
  const counts: Record<VisualStatus, number> = {
    locked: 0,
    ready: 0,
    running: 0,
    complete: 0,
    failed: 0,
    unread: 0,
    recommended: 0,
    reading: 0,
    read: 0,
  };
  const learningReady = Boolean(dag?.learning_ready);
  const nodes = (dag?.nodes ?? []).map((node) => {
    const visualStatus = visualStatusForNode(node, learningReady);
    counts[visualStatus] += 1;
    const graphNode: DagGraphNode = {
      ...node,
      id: node.id,
      name: node.title,
      val: isActiveVisual(visualStatus) ? 7 : isPositiveVisual(visualStatus) ? 5 : 4,
      color: VISUAL_META[visualStatus].color,
      visualStatus,
    };
    restoreNodePosition(graphNode, positions);
    return graphNode;
  });
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const links = (dag?.edges ?? [])
    .filter((edge) => edge.relation === "prerequisite" && byId.has(edge.from) && byId.has(edge.to))
    .map((edge) => ({
      ...edge,
      source: edge.from,
      target: edge.to,
      visualStatus: byId.get(edge.to)?.visualStatus ?? "locked",
      required_defines: edge.required_defines,
    }));
  return { nodes, links, byId, counts };
}

function visualStatusForNode(node: DagNode, learningReady: boolean): VisualStatus {
  const generation = node.generation_status ?? node.status;
  if (!learningReady) return generation;
  if (generation === "failed" || generation === "locked") return generation;
  if (generation !== "complete") return generation;
  return node.reading_status || "unread";
}

function isActiveVisual(status: VisualStatus): boolean {
  return status === "running" || status === "recommended" || status === "reading";
}

function isPositiveVisual(status: VisualStatus): boolean {
  return status === "ready" || status === "complete" || status === "read";
}

function cacheNodePositions(
  nodes: DagGraphNode[],
  positions: Map<string, DagNodePosition>,
): void {
  for (const node of nodes) {
    const position: DagNodePosition = {};
    for (const key of POSITION_KEYS) {
      const value = node[key];
      if (typeof value === "number" && Number.isFinite(value)) {
        position[key] = value;
      }
    }
    if (Object.keys(position).length > 0) {
      positions.set(String(node.id), position);
    }
  }
}

function restoreNodePosition(
  node: DagGraphNode,
  positions: Map<string, DagNodePosition>,
): void {
  const position = positions.get(String(node.id));
  if (!position) return;
  for (const key of POSITION_KEYS) {
    const value = position[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      node[key] = value;
    }
  }
}

function dagSignature(dag: DagPayload): string {
  const nodes = dag.nodes
    .map((node) => ({
      id: node.id,
      title: node.title,
      label: node.label,
      status: node.status,
      generation_status: node.generation_status,
      reading_status: node.reading_status,
      recommended: node.recommended,
      affected_by_feedback: node.affected_by_feedback,
      learning_ready: node.learning_ready,
      summary: node.summary,
      source_order_index: node.source_order_index,
      defines: sortedStrings(node.defines),
      collections: sortedStrings(node.collections),
      prerequisites: sortedStrings(node.prerequisites),
      dependents: sortedStrings(node.dependents),
      output_paths: sortedStrings(node.output_paths),
    }))
    .sort((left, right) => left.id.localeCompare(right.id));
  const edges = dag.edges
    .filter((edge) => edge.relation === "prerequisite")
    .map((edge) => ({
      from: edge.from,
      to: edge.to,
      relation: edge.relation,
      confidence: edge.confidence,
      required_defines: sortedStrings(edge.required_defines),
    }))
    .sort((left, right) => `${left.from}:${left.to}`.localeCompare(`${right.from}:${right.to}`));
  return JSON.stringify({ nodes, edges, roots: sortedStrings(dag.roots), learning_ready: dag.learning_ready });
}

function sortedStrings(items: string[]): string[] {
  return [...items].sort((left, right) => left.localeCompare(right));
}

function makeNodeObject(node: DagGraphNode, selected: boolean): THREE.Object3D {
  const meta = VISUAL_META[node.visualStatus];
  const group = new THREE.Group();
  const core = new THREE.Mesh(
    new THREE.SphereGeometry(selected ? 8 : 6, 24, 16),
    new THREE.MeshStandardMaterial({
      color: meta.color,
      emissive: meta.glow,
      emissiveIntensity: isActiveVisual(node.visualStatus) || selected ? 0.55 : 0.18,
      roughness: 0.46,
      metalness: 0.02,
      transparent: true,
      opacity: node.visualStatus === "locked" ? 0.72 : 0.96,
    }),
  );
  group.add(core);

  if (node.visualStatus !== "locked") {
    const leaf = new THREE.Mesh(
      new THREE.SphereGeometry(2.7, 16, 8),
      new THREE.MeshStandardMaterial({
        color: node.visualStatus === "failed" ? "#c96c6c" : meta.glow,
        roughness: 0.52,
        transparent: true,
        opacity: 0.88,
      }),
    );
    leaf.scale.set(1.65, 0.55, 0.34);
    leaf.position.set(5.2, 3.8, 0);
    leaf.rotation.set(0.28, 0.3, -0.62);
    group.add(leaf);
  }

  if (selected || isActiveVisual(node.visualStatus)) {
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(selected ? 12 : 9, 0.55, 8, 42),
      new THREE.MeshBasicMaterial({
        color: meta.glow,
        transparent: true,
        opacity: selected ? 0.62 : 0.42,
      }),
    );
    ring.rotation.x = Math.PI / 2;
    group.add(ring);
  }

  return group;
}

function nodeTooltip(node: DagGraphNode): string {
  const meta = VISUAL_META[node.visualStatus];
  return `${node.label}<br/><b>${node.title}</b><br/>${meta.label}`;
}

function outputNameFromPath(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const parts = normalized.split("/").filter(Boolean);
  return parts[parts.length - 1] ?? "";
}

function endpointId(value: string | number | DagGraphNode | undefined): string {
  if (typeof value === "object" && value) return String(value.id);
  return String(value ?? "");
}

function useElementSize(ref: RefObject<HTMLElement>) {
  const [size, setSize] = useState({ width: 800, height: 560 });
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const update = (): void => {
      const rect = element.getBoundingClientRect();
      setSize({
        width: Math.max(320, Math.round(rect.width)),
        height: Math.max(360, Math.round(rect.height)),
      });
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return size;
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState<boolean>(false);
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = (): void => setReduced(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return reduced;
}
