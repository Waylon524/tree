import { useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import ForceGraph3D from "react-force-graph-3d";
import type { ForceGraphMethods, LinkObject, NodeObject } from "react-force-graph-3d";
import * as THREE from "three";
import { fetchDag, openDag, regrowNode } from "../api";
import type { DagEdge, DagNode, DagPayload } from "../api";
import type { Status } from "../types";
import { useT } from "../i18n";
import type { Translate } from "../i18n";
import { formatDateTime } from "../lib/format";
import { AppleTreeStage } from "./illustrations";
import type { StageKey } from "./illustrations";
import { Button } from "./ui/Button";
import { Message } from "./ui/Message";
import { Toggle } from "./ui/Toggle";

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
  set: { color: "#2f6f44", glow: "#3f8a56" },
  unripe: { color: "#5f9e44", glow: "#84c25f" },
  turning: { color: "#a9bf45", glow: "#c6d76a" },
  almost: { color: "#e0a838", glow: "#f0c468" },
  ripe: { color: "#d8453d", glow: "#ef8378" },
  picked: { color: "#7c2f2b", glow: "#a55149" },
  blighted: { color: "#8a6a4a", glow: "#a98a63" },
};

const TRUNK_COLOR = "#7a5733";
const OUTLINE_COLOR = "#241a0e";
// THREE.BackSide is missing from this project's reduced three typings; value 1.
const BACK_SIDE = 1;

// Radial canopy layout. Depth = longest prerequisite path. Each node inherits a
// spoke (azimuth) from its prerequisite, fanning outward, so a node and its
// dependents stay on the same / adjacent spoke instead of jumping across the dome.
const ROOT_R = 30; // innermost shell radius
const LAYER_GAP = 56;
const THETA_MAX = 1.42; // ~81° — every layer is a shell spanning the upper hemisphere
const CANOPY_LIFT = 1; // hemisphere is already round; no extra stretch
// Every node is shifted out by one shell (layer-0 shell left empty), and the
// inner gaps are widened so the dense bottom of the tree opens up.
const SHELL_GAP_MULT = [2, 1.8, 1.6, 1.4, 1.2];

function shellRadius(layer: number): number {
  let cumulative = 0;
  for (let i = 0; i <= layer; i += 1) {
    cumulative += i < SHELL_GAP_MULT.length ? SHELL_GAP_MULT[i] : 1;
  }
  return ROOT_R + LAYER_GAP * cumulative;
}

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
  isPrimary?: boolean;
};

type GraphRef = ForceGraphMethods<DagGraphNode, DagGraphLink> | undefined;

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
  const [canopyScale, setCanopyScale] = useState<number>(1);
  const [showDownstream, setShowDownstream] = useState<boolean>(false);
  const [regrowBusy, setRegrowBusy] = useState<boolean>(false);
  const [actionMsg, setActionMsg] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [svgMessage, setSvgMessage] = useState<string>("");
  const graphRef = useRef<GraphRef>(undefined);
  const graphSignatureRef = useRef<string>("");
  const initialFitDoneRef = useRef<boolean>(false);
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

  const graph = useMemo(() => buildGraph(dag, canopyScale), [dag, canopyScale]);
  const fruiting = useMemo(() => isFruiting(status, graph.nodes), [status, graph.nodes]);
  const sceneStage = useMemo<StageKey | null>(
    () => (fruiting ? null : activeStageKey(status)),
    [fruiting, status],
  );
  const showScene = !fruiting || graph.nodes.length === 0;
  const selected = selectedId ? graph.byId.get(selectedId) : undefined;
  const downstreamActive = showDownstream && Boolean(selected);

  // Unread prerequisite fruit of the selected node — highlighted in 3D and listed
  // in the inspector so the reader knows what to read first.
  const unreadAncestorIds = useMemo(() => {
    if (!selected || downstreamActive) return new Set<string>();
    const ids = new Set<string>();
    for (const aid of ancestorsOf(selected.id, graph.byId)) {
      const node = graph.byId.get(aid);
      if (node && node.reading_status !== "read") ids.add(aid);
    }
    return ids;
  }, [selected, downstreamActive, graph.byId]);
  const unreadAncestors = useMemo(
    () =>
      [...unreadAncestorIds]
        .map((id) => graph.byId.get(id))
        .filter((node): node is DagGraphNode => Boolean(node)),
    [unreadAncestorIds, graph.byId],
  );

  const visibleIds = useMemo(() => {
    if (downstreamActive && selected) return descendantsOf(selected.id, graph.byId);
    if (filter === "all") return new Set(graph.nodes.map((node) => String(node.id)));
    return new Set(
      graph.nodes.filter((node) => node.ripeness === filter).map((node) => String(node.id)),
    );
  }, [downstreamActive, selected, filter, graph.nodes, graph.byId]);

  useEffect(() => {
    if (selectedId && !graph.byId.has(selectedId)) onSelectedNodeChange("");
  }, [graph.byId, onSelectedNodeChange, selectedId]);

  useEffect(() => {
    if (!selected) setShowDownstream(false);
  }, [selected]);

  useEffect(() => {
    if (graph.nodes.length === 0) {
      initialFitDoneRef.current = false;
      removeTrunk(graphRef.current, trunkRef);
    }
  }, [graph.nodes.length]);

  const focusNode = (node: DagGraphNode): void => {
    onSelectedNodeChange(String(node.id));
    setSvgMessage("");
    const graph = graphRef.current;
    if (!graph) return;
    const nx = Number(node.x ?? 0);
    const ny = Number(node.y ?? 0);
    const nz = Number(node.z ?? 0);
    const getCamera = graph.cameraPosition as unknown as () => { x: number; y: number; z: number };
    const cam = getCamera();
    // Keep the current viewing direction (camera stays on the same side of the
    // node); just dolly gently toward it so it centers without flipping to a
    // top-down close-up.
    const ox = cam.x - nx;
    const oy = cam.y - ny;
    const oz = cam.z - nz;
    const current = Math.hypot(ox, oy, oz) || 1;
    const target = Math.max(220, current * 0.6); // gentle zoom, not too close
    const k = target / current;
    graph.cameraPosition(
      { x: nx + ox * k, y: ny + oy * k, z: nz + oz * k },
      { x: nx, y: ny, z: nz },
      reducedMotion ? 0 : 700,
    );
  };

  const focusNodeId = (id: string): void => {
    const node = graph.byId.get(id);
    if (node) focusNode(node);
  };

  const regrow = async (id: string): Promise<void> => {
    setRegrowBusy(true);
    setActionMsg("");
    try {
      await regrowNode(id);
      setActionMsg(t("harvest.regrowQueued"));
    } catch (err) {
      setActionMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setRegrowBusy(false);
    }
  };

  const runOpenSvg = async (): Promise<void> => {
    setSvgMessage("");
    try {
      setSvgMessage(await openDag());
    } catch (err) {
      setSvgMessage(err instanceof Error ? err.message : String(err));
    }
  };

  const frontView = (): void => {
    let maxY = 0;
    let maxR = 0;
    for (const node of graph.nodes) {
      maxY = Math.max(maxY, Number(node.y ?? 0));
      maxR = Math.max(maxR, Math.hypot(Number(node.x ?? 0), Number(node.z ?? 0)));
    }
    const span = Math.max(maxR * 2, maxY) || 240;
    const dist = span * 1.5 + 160;
    graphRef.current?.cameraPosition(
      { x: dist * 0.3, y: maxY * 0.2, z: dist },
      { x: 0, y: maxY * 0.4, z: 0 },
      reducedMotion ? 0 : 700,
    );
  };

  const resetView = (): void => {
    onSelectedNodeChange("");
    frontView();
  };

  const fitView = (): void => {
    graphRef.current?.zoomToFit(reducedMotion ? 0 : 650, 60, (node) =>
      visibleIds.has(String(node.id)),
    );
  };

  const handleEngineStop = (): void => {
    refreshTrunk(graphRef.current, graph.nodes, trunkRef);
    if (initialFitDoneRef.current || graph.nodes.length === 0) return;
    initialFitDoneRef.current = true;
    frontView();
  };

  return (
    <section className="dag-workbench" aria-label={t("harvest.aria.graph")}>
      <aside className="dag-rail" aria-label={t("harvest.aria.filters")}>
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
        <div className="dag-canopy-control">
          <label htmlFor="canopy-size">
            {t("harvest.canopy")} <b>{canopyScale.toFixed(1)}×</b>
          </label>
          <input
            id="canopy-size"
            type="range"
            min="0.6"
            max="1.8"
            step="0.1"
            value={canopyScale}
            onChange={(event) => setCanopyScale(parseFloat(event.target.value))}
          />
        </div>
        {dag?.updated_at && (
          <p className="muted">
            {t("common.updated")} {formatDateTime(dag.updated_at)}
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
            backgroundColor="rgba(0,0,0,0)"
            showNavInfo={false}
            controlType="orbit"
            enableNodeDrag={false}
            cooldownTicks={0}
            nodeVal={(node) => node.val}
            nodeLabel={(node) => nodeTooltip(node, t)}
            nodeColor={(node) => node.color}
            nodeVisibility={(node) => visibleIds.has(String(node.id))}
            nodeThreeObject={(node: DagGraphNode) =>
              makeAppleObject(node, node.id === selectedId, unreadAncestorIds.has(String(node.id)))
            }
            nodeThreeObjectExtend={false}
            linkVisibility={(link) =>
              visibleIds.has(endpointId(link.source)) && visibleIds.has(endpointId(link.target))
            }
            linkColor={(link) => (link.isPrimary ? "rgba(122,87,51,0.82)" : "rgba(122,87,51,0.12)")}
            linkWidth={(link) => (link.isPrimary ? 2.2 : 0.8)}
            linkOpacity={1}
            onEngineStop={handleEngineStop}
            onNodeClick={(node) => focusNode(node)}
            onBackgroundClick={() => onSelectedNodeChange("")}
          />
        )}

        {!showScene && (
          <>
            <div className="dag-canvas-hint">{t("harvest.canvasHint")}</div>
            <div className="dag-view-controls">
              <Button variant="ghost" onClick={fitView}>
                {t("harvest.fit")}
              </Button>
              <Button variant="ghost" onClick={resetView}>
                {t("harvest.reset")}
              </Button>
              <Button variant="ghost" onClick={() => void runOpenSvg()}>
                {t("harvest.openSvg")}
              </Button>
            </div>
            {svgMessage && <span className="dag-svg-message">{svgMessage}</span>}
          </>
        )}
      </div>

      <NodeInspector
        node={selected}
        graph={graph}
        t={t}
        showDownstream={showDownstream}
        onToggleDownstream={setShowDownstream}
        unreadAncestors={unreadAncestors}
        onRegrow={regrow}
        regrowBusy={regrowBusy}
        actionMsg={actionMsg}
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
  showDownstream,
  onToggleDownstream,
  unreadAncestors,
  onRegrow,
  regrowBusy,
  actionMsg,
  onFocus,
  onReadOutput,
}: {
  node?: DagGraphNode;
  graph: DagGraph;
  t: Translate;
  showDownstream: boolean;
  onToggleDownstream: (value: boolean) => void;
  unreadAncestors: DagGraphNode[];
  onRegrow: (id: string) => void;
  regrowBusy: boolean;
  actionMsg: string;
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
  const downstreamCount = node ? descendantsOf(node.id, graph.byId).size - 1 : 0;

  return (
    <aside className="dag-inspector" aria-label={t("harvest.aria.inspector")}>
      {node ? (
        <>
          <div className="inspector-head">
            <span className={`dag-state state-${node.ripeness}`}>{t(`ripe.${node.ripeness}`)}</span>
            <span className="muted">{node.label}</span>
          </div>
          <h2>{node.title}</h2>
          <p className="muted">{t(`ripe.${node.ripeness}.desc`)}</p>
          {node.ripeness === "blighted" && (
            <div className="dag-regrow">
              <Button onClick={() => onRegrow(String(node.id))} disabled={regrowBusy}>
                {regrowBusy ? t("harvest.regrowing") : t("harvest.regrow")}
              </Button>
              {actionMsg && <Message kind="hint">{actionMsg}</Message>}
            </div>
          )}
          <Toggle
            className="downstream-toggle"
            checked={showDownstream}
            onChange={onToggleDownstream}
            label={
              <>
                {t("harvest.downstream")} <b>{downstreamCount}</b>
              </>
            }
          />
          {unreadAncestors.length > 0 && (
            <div className="dag-inspector-section prereq-reminder">
              <p className="hint">{t("harvest.prereqReminder", { n: unreadAncestors.length })}</p>
              <div className="node-link-list">
                {unreadAncestors.map((ancestor) => (
                  <button
                    key={ancestor.id}
                    type="button"
                    className="node-link"
                    onClick={() => onFocus(String(ancestor.id))}
                  >
                    <span className={`dag-dot dag-dot-${ancestor.ripeness}`} />
                    {ancestor.label}
                  </button>
                ))}
              </div>
            </div>
          )}
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
        <Button variant="ghost" className="output-action" disabled>
          {disabledMessage}
        </Button>
      ) : (
        <div className="output-action-list">
          {outputs.map((name, index) => (
            <Button
              key={`${name}-${index}`}
              variant={index === 0 ? "primary" : "ghost"}
              onClick={() => onReadOutput?.(name, String(node.id))}
            >
              {outputs.length === 1 ? t("harvest.startLearning") : `${t("harvest.read")} ${name}`}
            </Button>
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

function buildGraph(dag: DagPayload | null, canopyScale: number): DagGraph {
  const counts: Record<Ripeness, number> = {
    set: 0,
    unripe: 0,
    turning: 0,
    almost: 0,
    ripe: 0,
    picked: 0,
    blighted: 0,
  };
  const nodes = (dag?.nodes ?? []).map((node) => {
    const ripeness = ripenessForNode(node);
    counts[ripeness] += 1;
    const graphNode: DagGraphNode = {
      ...node,
      id: node.id,
      name: node.title,
      val: ripeness === "ripe" ? 8 : ripeness === "set" ? 4 : 6,
      color: RIPENESS_META[ripeness].color,
      ripeness,
    };
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
  applyCanopyLayout(nodes, byId, links, canopyScale);
  return { nodes, links, byId, counts };
}

function pushTo(map: Map<string, string[]>, key: string, value: string): void {
  const list = map.get(key);
  if (list) list.push(value);
  else map.set(key, [value]);
}

function descendantsOf(id: string, byId: Map<string, DagGraphNode>): Set<string> {
  const out = new Set<string>([id]);
  const stack = [id];
  while (stack.length) {
    const current = stack.pop() as string;
    const node = byId.get(current);
    for (const dep of node?.dependents ?? []) {
      if (byId.has(dep) && !out.has(dep)) {
        out.add(dep);
        stack.push(dep);
      }
    }
  }
  return out;
}

// All transitive prerequisites of a node (its ancestors; excludes the node).
function ancestorsOf(id: string, byId: Map<string, DagGraphNode>): Set<string> {
  const out = new Set<string>();
  const stack = [id];
  while (stack.length) {
    const current = stack.pop() as string;
    const node = byId.get(current);
    for (const prereq of node?.prerequisites ?? []) {
      if (byId.has(prereq) && !out.has(prereq)) {
        out.add(prereq);
        stack.push(prereq);
      }
    }
  }
  return out;
}

// Radial canopy: depth = longest prerequisite path → shell radius (prereqs inner,
// dependents outer). Azimuth comes from a leaf-weighted radial tidy tree so each
// node sits on / near its prerequisite's spoke, keeping branches short.
function applyCanopyLayout(
  nodes: DagGraphNode[],
  byId: Map<string, DagGraphNode>,
  links: DagGraphLink[],
  scale: number,
): void {
  const parents = new Map<string, string[]>();
  for (const link of links) pushTo(parents, link.target, link.source);

  const layer = new Map<string, number>();
  const visiting = new Set<string>();
  const depth = (id: string): number => {
    const cached = layer.get(id);
    if (cached !== undefined) return cached;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    let value = 0;
    for (const parent of parents.get(id) ?? []) {
      if (byId.has(parent)) value = Math.max(value, depth(parent) + 1);
    }
    visiting.delete(id);
    layer.set(id, value);
    return value;
  };
  for (const node of nodes) depth(node.id);

  const primaryParent = new Map<string, string>();
  const primaryChildren = new Map<string, string[]>();
  const roots: string[] = [];
  for (const node of nodes) {
    const ps = (parents.get(node.id) ?? []).filter((p) => byId.has(p));
    if (ps.length === 0) {
      roots.push(node.id);
      continue;
    }
    let best = ps[0];
    for (const p of ps) if ((layer.get(p) ?? 0) > (layer.get(best) ?? 0)) best = p;
    primaryParent.set(node.id, best);
    pushTo(primaryChildren, best, node.id);
  }
  roots.sort();
  // Mark the spanning-tree (primary) branches so cross edges can be ghosted.
  for (const link of links) link.isPrimary = primaryParent.get(link.target) === link.source;

  // Subtree leaf weight drives how much of a shell each branch occupies.
  const weight = new Map<string, number>();
  const weightOf = (id: string): number => {
    const cached = weight.get(id);
    if (cached !== undefined) return cached;
    const kids = primaryChildren.get(id) ?? [];
    let value = kids.length === 0 ? 1 : 0;
    for (const kid of kids) value += weightOf(kid);
    weight.set(id, value);
    return value;
  };
  roots.forEach(weightOf);

  // Nested 2D sunburst over the upper hemisphere: every node owns a cell in
  // (azimuth × polar) space and its children subdivide that cell (alternating the
  // split axis). A subtree therefore stays inside one patch of the dome — short,
  // readable spokes — while the layer of a node lands on a shared spherical shell
  // (radius = depth) instead of a single flat ring.
  const angle = new Map<string, { phi: number; theta: number }>();
  const subdivide = (
    id: string,
    a0: number,
    a1: number,
    t0: number,
    t1: number,
    splitAz: boolean,
  ): void => {
    angle.set(id, { phi: (a0 + a1) / 2, theta: ((t0 + t1) / 2) * THETA_MAX });
    const kids = (primaryChildren.get(id) ?? []).slice().sort();
    if (kids.length === 0) return;
    const totalWeight = kids.reduce((sum, kid) => sum + (weight.get(kid) ?? 1), 0) || 1;
    if (splitAz) {
      let a = a0;
      for (const kid of kids) {
        const span = (a1 - a0) * ((weight.get(kid) ?? 1) / totalWeight);
        subdivide(kid, a, a + span, t0, t1, false);
        a += span;
      }
    } else {
      let t = t0;
      for (const kid of kids) {
        const span = (t1 - t0) * ((weight.get(kid) ?? 1) / totalWeight);
        subdivide(kid, a0, a1, t, t + span, true);
        t += span;
      }
    }
  };
  const totalRootWeight = roots.reduce((sum, r) => sum + (weight.get(r) ?? 1), 0) || 1;
  let cursor = 0;
  for (const root of roots) {
    const span = 2 * Math.PI * ((weight.get(root) ?? 1) / totalRootWeight);
    subdivide(root, cursor, cursor + span, 0, 1, false);
    cursor += span;
  }
  let fallback = 0;
  for (const node of nodes) {
    if (!angle.has(node.id)) {
      angle.set(node.id, {
        phi: (2 * Math.PI * fallback++) / Math.max(1, nodes.length),
        theta: THETA_MAX * 0.6,
      });
    }
  }

  for (const node of nodes) {
    const value = layer.get(node.id) ?? 0;
    const radius = shellRadius(value) * scale;
    const { phi, theta } = angle.get(node.id) ?? { phi: 0, theta: THETA_MAX * 0.5 };
    node.x = node.fx = radius * Math.sin(theta) * Math.cos(phi);
    node.y = node.fy = radius * Math.cos(theta) * CANOPY_LIFT;
    node.z = node.fz = radius * Math.sin(theta) * Math.sin(phi);
  }
}

function ripenessForNode(node: DagNode): Ripeness {
  const generation = node.generation_status ?? node.status;
  if (generation === "failed") return "blighted";
  if (generation === "locked") return "set";
  if (generation === "ready") return "unripe";
  if (generation === "running") return "turning";
  // generation === "complete": ripeness follows the per-node reading status, so a
  // base fruit ripens (recommended) even while the upper canopy is still growing.
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

function makeAppleObject(
  node: DagGraphNode,
  selected: boolean,
  highlighted: boolean,
): THREE.Object3D {
  const meta = RIPENESS_META[node.ripeness];
  const group = new THREE.Group();
  const boost = (Math.min(node.dependents?.length ?? 0, 8) / 8) * 2.4;
  const baseR =
    (node.ripeness === "set" ? 5.5 : node.ripeness === "ripe" ? 9.5 : 7.5) +
    boost +
    (selected ? 1.6 : 0);

  const outline = new THREE.Mesh(
    new THREE.SphereGeometry(baseR * 1.16, 22, 14),
    new THREE.MeshBasicMaterial({
      color: OUTLINE_COLOR,
      side: BACK_SIDE,
      transparent: true,
      opacity: 0.85,
    }),
  );
  outline.scale.set(1, 0.92, 1);
  group.add(outline);

  const body = new THREE.Mesh(
    new THREE.SphereGeometry(baseR, 26, 18),
    new THREE.MeshStandardMaterial({
      color: meta.color,
      emissive: meta.glow,
      emissiveIntensity: node.ripeness === "ripe" || selected ? 0.5 : 0.2,
      roughness: 0.4,
      metalness: 0.02,
      transparent: true,
      opacity: node.ripeness === "picked" ? 0.82 : 1,
    }),
  );
  body.scale.set(1, 0.92, 1);
  group.add(body);

  const stalk = new THREE.Mesh(
    new THREE.SphereGeometry(0.6, 8, 6),
    new THREE.MeshStandardMaterial({ color: TRUNK_COLOR, roughness: 0.8 }),
  );
  stalk.scale.set(0.7, 2.6, 0.7);
  stalk.position.set(0, baseR + 1.6, 0);
  group.add(stalk);

  if (node.ripeness !== "blighted") {
    const leaf = new THREE.Mesh(
      new THREE.SphereGeometry(2.4, 14, 8),
      new THREE.MeshStandardMaterial({
        color: node.ripeness === "picked" ? "#6f8a4a" : "#8fbf5c",
        roughness: 0.55,
        transparent: true,
        opacity: 0.9,
      }),
    );
    leaf.scale.set(1.8, 0.5, 0.32);
    leaf.position.set(baseR * 0.72, baseR + 1.3, 0);
    leaf.rotation.set(0.2, 0.3, -0.7);
    group.add(leaf);
  }

  if (node.ripeness === "ripe" || selected) {
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(selected ? baseR + 5 : baseR + 3, 0.6, 8, 44),
      new THREE.MeshBasicMaterial({
        color: node.ripeness === "ripe" ? "#ef8378" : meta.glow,
        transparent: true,
        opacity: selected ? 0.6 : 0.5,
      }),
    );
    ring.rotation.x = Math.PI / 2;
    group.add(ring);
  }

  if (highlighted) {
    // Unread prerequisite: an amber halo flags "read me before that node".
    const halo = new THREE.Mesh(
      new THREE.TorusGeometry(baseR + 5.5, 0.8, 8, 44),
      new THREE.MeshBasicMaterial({ color: "#f0c468", transparent: true, opacity: 0.9 }),
    );
    halo.rotation.x = Math.PI / 2;
    group.add(halo);
  }

  return group;
}

// three 0.184 ships no bundled types and this project resolves only a partial
// three typing, so CylinderGeometry / Vector3 / Mesh.quaternion are cast at runtime.
type CylinderCtor = new (
  radiusTop: number,
  radiusBottom: number,
  height: number,
  radialSegments?: number,
) => THREE.SphereGeometry;
const CylinderGeometry = (THREE as unknown as { CylinderGeometry: CylinderCtor }).CylinderGeometry;
const Vector3 = (THREE as unknown as { Vector3: new (x: number, y: number, z: number) => object }).Vector3;
type Orientable = { quaternion: { setFromUnitVectors: (a: object, b: object) => void } };

// A tapered cylinder oriented between two points — used for branches and roots.
function branchMesh(
  from: [number, number, number],
  to: [number, number, number],
  rTop: number,
  rBottom: number,
  material: THREE.MeshStandardMaterial,
): THREE.Mesh {
  const dx = to[0] - from[0];
  const dy = to[1] - from[1];
  const dz = to[2] - from[2];
  const length = Math.hypot(dx, dy, dz) || 1;
  const mesh = new THREE.Mesh(new CylinderGeometry(rTop, rBottom, length, 8), material);
  mesh.position.set((from[0] + to[0]) / 2, (from[1] + to[1]) / 2, (from[2] + to[2]) / 2);
  (mesh as unknown as Orientable).quaternion.setFromUnitVectors(
    new Vector3(0, 1, 0),
    new Vector3(dx / length, dy / length, dz / length),
  );
  return mesh;
}

// A clean, minimalist trunk: one tapered column with a few root flares, plus
// "fake" branches bridging the empty layer-0 gap up to the root nodes.
function refreshTrunk(
  graph: GraphRef,
  nodes: DagGraphNode[],
  trunkRef: { current: THREE.Object3D | null },
): void {
  if (!graph || nodes.length === 0) return;
  const scene = graph.scene();
  if (!scene) return;
  removeTrunk(graph, trunkRef);

  let maxY = 0;
  let maxR = 0;
  for (const node of nodes) {
    maxY = Math.max(maxY, Number(node.y ?? 0));
    maxR = Math.max(maxR, Math.hypot(Number(node.x ?? 0), Number(node.z ?? 0)));
  }
  const trunkTop = shellRadius(0) * 0.42;
  const bottomY = -(0.45 * maxY + 70);

  const material = new THREE.MeshStandardMaterial({
    color: TRUNK_COLOR,
    emissive: "#3a2916",
    emissiveIntensity: 0.1,
    roughness: 0.9,
    metalness: 0,
  });
  const group = new THREE.Group();

  const trunk = new THREE.Mesh(new CylinderGeometry(6, 15, trunkTop - bottomY, 12), material);
  trunk.position.set(0, (trunkTop + bottomY) / 2, 0);
  group.add(trunk);

  for (let i = 0; i < 4; i += 1) {
    const a = (Math.PI / 2) * i + 0.5;
    group.add(
      branchMesh([0, bottomY + 34, 0], [Math.cos(a) * 22, bottomY - 4, Math.sin(a) * 22], 1.6, 6.5, material),
    );
  }

  // Fake branches: crown -> each root node (the empty layer 0 -> layer 1 bridge).
  for (const node of nodes) {
    if ((node.prerequisites?.length ?? 0) !== 0) continue;
    group.add(
      branchMesh(
        [0, trunkTop - 6, 0],
        [Number(node.x ?? 0), Number(node.y ?? 0), Number(node.z ?? 0)],
        1.1,
        3.4,
        material,
      ),
    );
  }

  // --- orchard ground + simple bushes (the sky is a CSS backdrop) ---
  const groundY = bottomY - 2;
  const groundRadius = Math.max(1200, maxR * 4.5);
  const grass = new THREE.Mesh(
    new CylinderGeometry(groundRadius, groundRadius, 14, 48),
    new THREE.MeshStandardMaterial({ color: "#83b357", roughness: 1, metalness: 0 }),
  );
  grass.position.set(0, groundY - 7, 0); // top face sits at groundY
  group.add(grass);
  // a thin mowed patch resting on the grass under the tree (avoids z-fighting)
  const patch = new THREE.Mesh(
    new CylinderGeometry(maxR * 0.85 + 60, maxR * 0.85 + 60, 2, 36),
    new THREE.MeshStandardMaterial({ color: "#6fa049", roughness: 1 }),
  );
  patch.position.set(0, groundY + 1, 0);
  group.add(patch);

  const bushMaterials = [
    new THREE.MeshStandardMaterial({ color: "#4f8f43", roughness: 0.9 }),
    new THREE.MeshStandardMaterial({ color: "#67a851", roughness: 0.9 }),
  ];
  const bushCount = 12;
  for (let i = 0; i < bushCount; i += 1) {
    const a = (Math.PI * 2 * i) / bushCount + 0.5;
    const dist = maxR * (0.95 + (i % 4) * 0.26) + 60;
    const r = 16 + (i % 4) * 7;
    const material = bushMaterials[i % bushMaterials.length];
    const bush = new THREE.Group();
    const lobes: Array<[number, number, number, number]> = [
      [0, r * 0.7, 0, r],
      [r * 0.85, r * 0.5, 0, r * 0.7],
      [-r * 0.75, r * 0.45, r * 0.3, r * 0.62],
    ];
    for (const [lx, ly, lz, lr] of lobes) {
      const lobe = new THREE.Mesh(new THREE.SphereGeometry(lr, 10, 8), material);
      lobe.position.set(lx, ly, lz);
      bush.add(lobe);
    }
    bush.position.set(Math.cos(a) * dist, groundY, Math.sin(a) * dist);
    group.add(bush);
  }

  scene.add(group);
  trunkRef.current = group;
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
