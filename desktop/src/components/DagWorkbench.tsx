import { useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import ForceGraph3D from "react-force-graph-3d";
import type { ForceGraphMethods, LinkObject, NodeObject } from "react-force-graph-3d";
import * as THREE from "three";
import { fetchDag, openDag } from "../api";
import type { DagEdge, DagNode, DagPayload } from "../api";
import type { Status } from "../types";
import { useT } from "../i18n";
import type { Translate } from "../i18n";
import { AppleTreeStage } from "./illustrations";
import type { StageKey } from "./illustrations";

// One unified ripeness lifecycle merges the growing DAG and the read DAG.
type Ripeness = "set" | "unripe" | "turning" | "almost" | "ripe" | "picked" | "blighted";

const RIPENESS_ORDER: Ripeness[] = [
  "set",
  "unripe",
  "turning",
  "almost",
  "ripe",
  "picked",
  "blighted",
];

type DagFilter = Ripeness | "all";

// Kept in sync with the CSS ripeness tokens (WebGL needs literal colors).
const RIPENESS_META: Record<Ripeness, { color: string; glow: string }> = {
  set: { color: "#2f6f44", glow: "#3f8a56" }, // tiny dark-green fruit
  unripe: { color: "#5f9e44", glow: "#84c25f" }, // green
  turning: { color: "#a9bf45", glow: "#c6d76a" }, // yellow-green
  almost: { color: "#e0a838", glow: "#f0c468" }, // amber
  ripe: { color: "#d8453d", glow: "#ef8378" }, // red — recommended
  picked: { color: "#7c2f2b", glow: "#a55149" }, // dark maroon — read
  blighted: { color: "#8a6a4a", glow: "#a98a63" }, // withered — failed
};

const TRUNK_COLOR = "#7a5733";
const TRUNK_GLOW = "#9c7a4d";

const STAGE_KEYS: StageKey[] = ["ocr", "clean", "cut", "embed", "cluster", "link", "noderun"];

type DagGraphNode = NodeObject<DagNode> & DagNode & {
  name: string;
  val: number;
  color: string;
  ripeness: Ripeness;
};

type DagGraphLink = LinkObject<DagGraphNode, DagEdge> & {
  source: string;
  target: string;
  ripeness: Ripeness;
  required_defines: string[];
};

type GraphRef = ForceGraphMethods<DagGraphNode, DagGraphLink> | undefined;
type PositionKey = "x" | "y" | "z" | "vx" | "vy" | "vz";
type DagNodePosition = Partial<Record<PositionKey, number>>;

const POSITION_KEYS: PositionKey[] = ["x", "y", "z", "vx", "vy", "vz"];

interface DagWorkbenchProps {
  status?: Status | null;
  selectedNodeId?: string;
  onSelectedNodeChange?: (id: string) => void;
  onReadOutput?: (name: string, nodeId: string) => void;
}

export function DagWorkbench({
  status = null,
  selectedNodeId = "",
  onSelectedNodeChange = () => undefined,
  onReadOutput,
}: DagWorkbenchProps) {
  const t = useT();
  const [dag, setDag] = useState<DagPayload | null>(null);
  const [filter, setFilter] = useState<DagFilter>("all");
  const [error, setError] = useState<string>("");
  const [svgMessage, setSvgMessage] = useState<string>("");
  const graphRef = useRef<GraphRef>(undefined);
  const graphSignatureRef = useRef<string>("");
  const initialFitDoneRef = useRef<boolean>(false);
  const positionsRef = useRef<Map<string, DagNodePosition>>(new Map());
  const trunkRef = useRef<THREE.Object3D | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const size = useElementSize(stageRef);
  const reducedMotion = usePrefersReducedMotion();
  const selectedId = selectedNodeId;
  const filters = useMemo<DagFilter[]>(() => ["all", ...RIPENESS_ORDER], []);

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
  const fruiting = useMemo(() => isFruiting(status, graph.nodes), [status, graph.nodes]);
  const sceneStage = useMemo<StageKey | null>(
    () => (fruiting ? null : activeStageKey(status)),
    [fruiting, status],
  );
  const showScene = !fruiting || graph.nodes.length === 0;

  const visibleIds = useMemo(() => {
    if (filter === "all") return new Set(graph.nodes.map((node) => String(node.id)));
    return new Set(
      graph.nodes.filter((node) => node.ripeness === filter).map((node) => String(node.id)),
    );
  }, [filter, graph.nodes]);
  const selected = selectedId ? graph.byId.get(selectedId) : undefined;

  useEffect(() => {
    if (selectedId && !graph.byId.has(selectedId)) onSelectedNodeChange("");
  }, [graph.byId, onSelectedNodeChange, selectedId]);

  useEffect(() => {
    if (graph.nodes.length === 0) {
      initialFitDoneRef.current = false;
      positionsRef.current.clear();
      removeTrunk(graphRef.current, trunkRef);
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
    refreshTrunk(graphRef.current, graph.nodes, trunkRef);
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
          <h2>{t("harvest.title")}</h2>
          <p className="muted">{t("harvest.subtitle")}</p>
        </div>
        <div className="dag-stats">
          <span>
            <b>{graph.nodes.length}</b>
            {t("harvest.nodes")}
          </span>
          <span>
            <b>{graph.links.length}</b>
            {t("harvest.edges")}
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
              <span className={`dag-dot ${item === "all" ? "dag-dot-all" : `dag-dot-${item}`}`} />
              <span>{item === "all" ? t("harvest.all") : t(`ripe.${item}`)}</span>
              <b>{item === "all" ? graph.nodes.length : graph.counts[item]}</b>
            </button>
          ))}
        </div>
        {dag?.updated_at && (
          <p className="muted">
            {t("common.updated")} {dag.updated_at}
          </p>
        )}
      </aside>

      <div ref={stageRef} className="dag-stage">
        {error && <div className="dag-alert">{error}</div>}
        {showScene ? (
          <div className="dag-scene">
            <AppleTreeStage stage={sceneStage} />
            <p className="dag-scene-caption">
              {sceneStage ? t(`scene.${sceneStage}`) : t("scene.idle")}
            </p>
            {graph.nodes.length === 0 && <p className="muted">{t("harvest.emptyHint")}</p>}
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
            nodeLabel={(node) => nodeTooltip(node, t)}
            nodeColor={(node) => node.color}
            nodeOpacity={0.95}
            nodeResolution={22}
            nodeVisibility={(node) => visibleIds.has(String(node.id))}
            nodeThreeObject={(node: DagGraphNode) => makeAppleObject(node, node.id === selectedId)}
            nodeThreeObjectExtend={false}
            linkVisibility={(link) =>
              visibleIds.has(endpointId(link.source)) && visibleIds.has(endpointId(link.target))
            }
            linkColor={() => TRUNK_COLOR}
            linkWidth={(link) => (link.ripeness === "ripe" ? 3.4 : 2.1)}
            linkOpacity={0.62}
            linkDirectionalParticles={(link) => {
              if (reducedMotion) return 0;
              return link.ripeness === "ripe" ? 4 : 0;
            }}
            linkDirectionalParticleSpeed={0.01}
            linkDirectionalParticleWidth={2.6}
            linkDirectionalParticleColor={() => TRUNK_GLOW}
            cooldownTicks={80}
            onEngineTick={handleEngineTick}
            onEngineStop={handleEngineStop}
            onNodeClick={(node) => focusNode(node)}
            onBackgroundClick={() => onSelectedNodeChange("")}
          />
        )}

        {!showScene && (
          <>
            <div className="dag-canvas-hint">{t("harvest.canvasHint")}</div>
            <div className="dag-view-controls">
              <button type="button" className="ghost" onClick={fitView}>
                {t("harvest.fit")}
              </button>
              <button type="button" className="ghost" onClick={resetView}>
                {t("harvest.reset")}
              </button>
              <button type="button" className="ghost" onClick={() => void runOpenSvg()}>
                {t("harvest.openSvg")}
              </button>
            </div>
            {svgMessage && (
              <span className="dag-svg-message" dangerouslySetInnerHTML={{ __html: svgMessage }} />
            )}
          </>
        )}
      </div>

      <NodeInspector
        node={selected}
        graph={graph}
        t={t}
        onFocus={focusNodeId}
        onReadOutput={onReadOutput}
      />
    </section>
  );
}

function NodeInspector({
  node,
  graph,
  t,
  onFocus,
  onReadOutput,
}: {
  node?: DagGraphNode;
  graph: DagGraph;
  t: Translate;
  onFocus: (id: string) => void;
  onReadOutput?: (name: string, nodeId: string) => void;
}) {
  const prerequisites = node
    ? node.prerequisites
        .map((id) => graph.byId.get(id))
        .filter((item): item is DagGraphNode => Boolean(item))
    : [];
  const dependents = node
    ? node.dependents
        .map((id) => graph.byId.get(id))
        .filter((item): item is DagGraphNode => Boolean(item))
    : [];

  return (
    <aside className="dag-inspector" aria-label="Selected DAG node">
      {node ? (
        <>
          <div className="inspector-head">
            <span className={`dag-state state-${node.ripeness}`}>{t(`ripe.${node.ripeness}`)}</span>
            <span className="muted">{node.label}</span>
          </div>
          <h2>{node.title}</h2>
          <p className="muted">{t(`ripe.${node.ripeness}.desc`)}</p>
          {node.recommended && node.recommendation_reason && (
            <p className="ok">{node.recommendation_reason}</p>
          )}
          {node.affected_by_feedback && <p className="hint">{t("harvest.affected")}</p>}
          {node.last_feedback_error && <p className="errors">{node.last_feedback_error}</p>}
          {node.summary && <p>{node.summary}</p>}
          <InspectorList title={t("harvest.prerequisites")} nodes={prerequisites} onFocus={onFocus} />
          <InspectorList title={t("harvest.dependents")} nodes={dependents} onFocus={onFocus} />
          <TagList title={t("harvest.defines")} items={node.defines} empty={t("common.none")} />
          <TagList title={t("harvest.collections")} items={node.collections} empty={t("common.none")} />
          <OutputActions node={node} t={t} onReadOutput={onReadOutput} />
        </>
      ) : (
        <>
          <h2>{t("harvest.selectNode")}</h2>
          <p className="muted">{t("harvest.selectHint")}</p>
          <div className="dag-legend">
            {RIPENESS_ORDER.map((ripeness) => (
              <span key={ripeness}>
                <i className={`dag-dot dag-dot-${ripeness}`} />
                {t(`ripe.${ripeness}`)}
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
  t,
  onReadOutput,
}: {
  node: DagGraphNode;
  t: Translate;
  onReadOutput?: (name: string, nodeId: string) => void;
}) {
  const outputs = node.output_paths.map(outputNameFromPath).filter(Boolean);
  const disabledMessage =
    node.generation_status !== "complete"
      ? t("harvest.outputNotReady")
      : outputs.length === 0
        ? t("harvest.noOutput")
        : "";

  return (
    <div className="dag-inspector-section">
      <h3>{t("harvest.outputs")}</h3>
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
              {outputs.length === 1 ? t("harvest.startLearning") : `${t("harvest.read")} ${name}`}
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
            <button
              key={node.id}
              type="button"
              className="node-link"
              onClick={() => onFocus(String(node.id))}
            >
              <span className={`dag-dot dag-dot-${node.ripeness}`} />
              {node.label}
            </button>
          ))}
        </div>
      ) : (
        <p className="muted">—</p>
      )}
    </div>
  );
}

function TagList({ title, items, empty }: { title: string; items: string[]; empty: string }) {
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
  counts: Record<Ripeness, number>;
}

function buildGraph(dag: DagPayload | null, positions: Map<string, DagNodePosition>): DagGraph {
  const counts: Record<Ripeness, number> = {
    set: 0,
    unripe: 0,
    turning: 0,
    almost: 0,
    ripe: 0,
    picked: 0,
    blighted: 0,
  };
  const learningReady = Boolean(dag?.learning_ready);
  const nodes = (dag?.nodes ?? []).map((node) => {
    const ripeness = ripenessForNode(node, learningReady);
    counts[ripeness] += 1;
    const graphNode: DagGraphNode = {
      ...node,
      id: node.id,
      name: node.title,
      val: ripeness === "ripe" ? 7.5 : ripeness === "set" ? 3.5 : 5.5,
      color: RIPENESS_META[ripeness].color,
      ripeness,
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
      ripeness: byId.get(edge.to)?.ripeness ?? "set",
      required_defines: edge.required_defines,
    }));
  return { nodes, links, byId, counts };
}

function ripenessForNode(node: DagNode, learningReady: boolean): Ripeness {
  const generation = node.generation_status ?? node.status;
  if (generation === "failed") return "blighted";
  if (generation === "locked") return "set";
  if (generation === "ready") return "unripe";
  if (generation === "running") return "turning";
  // generation === "complete"
  if (!learningReady) return "almost";
  const reading = node.reading_status || "unread";
  if (reading === "read") return "picked";
  if (reading === "recommended" || reading === "reading") return "ripe";
  return "almost";
}

function isFruiting(status: Status | null, nodes: DagGraphNode[]): boolean {
  if (nodes.some((node) => (node.generation_status ?? node.status) === "complete")) return true;
  const noderun = status?.rows?.find((row) => row.key === "noderun");
  if (noderun && (noderun.badge === "running" || noderun.done > 0)) return true;
  return false;
}

function activeStageKey(status: Status | null): StageKey | null {
  const rows = status?.rows ?? [];
  const running = rows.find((row) => row.badge === "running" && isStageKey(row.key));
  if (running?.key) return running.key as StageKey;
  let last: StageKey | null = null;
  for (const row of rows) {
    if (isStageKey(row.key) && (row.done > 0 || row.badge === "done")) last = row.key as StageKey;
  }
  return last;
}

function isStageKey(value: string | undefined): value is StageKey {
  return Boolean(value) && STAGE_KEYS.includes(value as StageKey);
}

function cacheNodePositions(nodes: DagGraphNode[], positions: Map<string, DagNodePosition>): void {
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

function restoreNodePosition(node: DagGraphNode, positions: Map<string, DagNodePosition>): void {
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

function makeAppleObject(node: DagGraphNode, selected: boolean): THREE.Object3D {
  const meta = RIPENESS_META[node.ripeness];
  const group = new THREE.Group();
  const baseR = node.ripeness === "set" ? 4.4 : selected ? 7.4 : 6;

  // Apple body — a slightly flattened sphere.
  const body = new THREE.Mesh(
    new THREE.SphereGeometry(baseR, 26, 18),
    new THREE.MeshStandardMaterial({
      color: meta.color,
      emissive: meta.glow,
      emissiveIntensity: node.ripeness === "ripe" || selected ? 0.5 : 0.16,
      roughness: 0.42,
      metalness: 0.02,
      transparent: true,
      opacity: node.ripeness === "picked" ? 0.78 : 0.97,
    }),
  );
  body.scale.set(1, 0.92, 1);
  group.add(body);

  // Brown stalk on top.
  const stalk = new THREE.Mesh(
    new THREE.SphereGeometry(0.55, 8, 6),
    new THREE.MeshStandardMaterial({ color: TRUNK_COLOR, roughness: 0.8 }),
  );
  stalk.scale.set(0.7, 2.4, 0.7);
  stalk.position.set(0, baseR + 1.4, 0);
  group.add(stalk);

  // A small leaf, greener while unripe.
  if (node.ripeness !== "blighted") {
    const leaf = new THREE.Mesh(
      new THREE.SphereGeometry(2.2, 14, 8),
      new THREE.MeshStandardMaterial({
        color: node.ripeness === "picked" ? "#6f8a4a" : "#8fbf5c",
        roughness: 0.55,
        transparent: true,
        opacity: 0.9,
      }),
    );
    leaf.scale.set(1.7, 0.5, 0.32);
    leaf.position.set(baseR * 0.7, baseR + 1.2, 0);
    leaf.rotation.set(0.2, 0.3, -0.7);
    group.add(leaf);
  }

  // Ripe (recommended) fruit gets a halo so it shouts "pick me next".
  if (node.ripeness === "ripe" || selected) {
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(selected ? baseR + 5 : baseR + 3, 0.55, 8, 42),
      new THREE.MeshBasicMaterial({
        color: node.ripeness === "ripe" ? "#ef8378" : meta.glow,
        transparent: true,
        opacity: selected ? 0.6 : 0.5,
      }),
    );
    ring.rotation.x = Math.PI / 2;
    group.add(ring);
  }

  return group;
}

// A decorative central trunk so the canopy of fruit reads as one tree.
function refreshTrunk(
  graph: GraphRef,
  nodes: DagGraphNode[],
  trunkRef: { current: THREE.Object3D | null },
): void {
  if (!graph || nodes.length === 0) return;
  const scene = graph.scene();
  if (!scene) return;
  removeTrunk(graph, trunkRef);

  const ys = nodes.map((node) => Number(node.y ?? 0)).filter((value) => Number.isFinite(value));
  if (ys.length === 0) return;
  const bottom = Math.min(...ys);
  const top = Math.max(...ys);
  const trunkTop = bottom + (top - bottom) * 0.55;
  const height = Math.max(40, trunkTop - bottom + 30);
  const center = bottom - 14 + height / 2;

  const trunk = new THREE.Mesh(
    new THREE.SphereGeometry(1, 16, 12),
    new THREE.MeshStandardMaterial({
      color: TRUNK_COLOR,
      emissive: "#3a2916",
      emissiveIntensity: 0.12,
      roughness: 0.92,
    }),
  );
  trunk.scale.set(6, height / 2, 6);
  trunk.position.set(0, center, 0);
  scene.add(trunk);
  trunkRef.current = trunk;
}

type Removable = { remove: (object: THREE.Object3D) => void };

function removeTrunk(graph: GraphRef, trunkRef: { current: THREE.Object3D | null }): void {
  if (trunkRef.current && graph) {
    const scene = graph.scene() as unknown as Removable | null;
    scene?.remove(trunkRef.current);
  }
  trunkRef.current = null;
}

function nodeTooltip(node: DagGraphNode, t: Translate): string {
  return `${node.label}<br/><b>${node.title}</b><br/>${t(`ripe.${node.ripeness}`)}`;
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
