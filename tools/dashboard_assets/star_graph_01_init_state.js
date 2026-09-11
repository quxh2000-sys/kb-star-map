// 数据装载、画布上下文、全局状态与基础几何
// 由 star_graph.js 按接缝拆分后改造为独立模块；模块间只通过 globalThis.KBStarGraph 通信。
globalThis.KBStarGraph = globalThis.KBStarGraph || {};
(() => {
  "use strict";
  const G = globalThis.KBStarGraph;

  const data = JSON.parse(document.getElementById("dashboard-data").textContent);
  const core = globalThis.KBStarGraphCore;
  const canvas = document.getElementById("starGraphCanvas");
  const ctx = canvas.getContext("2d");
  const mini = document.getElementById("miniMapCanvas");
  const miniCtx = mini.getContext("2d");
  const nodes = data.graph.nodes || [];
  const edges = data.graph.edges || [];
  G.showBackground=false;
  G.mainline=core.mainlineGraph(nodes,edges,G.showBackground);
  const clusters = data.graph.clusters || [];
  const nodeById = new Map(nodes.map(node => [node.id, node]));
  const adjacency = new Map(nodes.map(node => [node.id, new Set()]));
  edges.forEach(edge => {
    adjacency.get(edge.source)?.add(edge.target);
    adjacency.get(edge.target)?.add(edge.source);
  });

  const colors = {"来源资料":"#27c2d1","拆解记录":"#4c9ab7","知识原子":"#2f6bff","专题知识":"#8b5cf6","需求记录":"#d36ba6","方案成果":"#1fb8d0","验证反馈":"#48b66e","治理规则":"#f2b84b","运行记录":"#51657d","未分类":"#8a94a6"};
  const state = {
    mode: "global",
    operationTask: null,
    camera: {x: 1200, y: 800, zoom: .48, width: 1, height: 1},
    hovered: null,
    selected: null,
    located: null,
    relationView: "all",
    depth: 1,
    dragging: false,
    moved: false,
    last: {x: 0, y: 0},
    filters: {
      // 用数据里实际出现的类型做默认选集，而不是硬编码清单；
      // 否则自定义/兜底类型（如「未分类」）会被默认过滤掉，首次打开是空图。
      types: new Set(core.effectiveTypeFilters(nodes)),
      industries: new Set(),
      evidence: new Set(),
      health: new Set(),
      time: new Set(),
    },
    layers: {nodes: true, products: true, rules: true, health: true, updates: true},
    playback: {active: false, playing: false, cursor: "", index: 0},
    showArrows: false,
    groups: [],
    operationalFilters: new Set(),
    controlsExpanded: false,
    nodeOpen: false,
    maintenance: {mode: "edit", note: null, preview: null},
    localSet: null,
  };
  G.currentOperationalMetrics = core.operationalMetrics(
    nodes.filter(node => core.passesFilters(node, state.filters, data.graph.healthByNodeId)),
    data.graph.healthByNodeId,
  );
  G.spatialGrid = core.buildSpatialGrid(nodes, 48);
  // 时间镜头参照点取【快照内最新修改日】而非墙钟：分享出去的静态 HTML 永久可用。
  G.timeReference = core.operationalMetrics(nodes, data.graph.healthByNodeId).latestUpdated;
  G.playbackTimeline = core.playbackTimeline(nodes);
  const basePositions = new Map(nodes.map(n => [n.id,{x:n.x,y:n.y}]));
  G.chainColumns = [];
  const localManagement = core.isLocalManagementLocation(location.protocol, location.hostname);
  document.body.classList.toggle("share-mode", !localManagement);
  G.frameRequested = false;
  const esc = value => String(value ?? "").replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
  Object.assign(G, { data, core, canvas, ctx, mini, miniCtx, nodes, edges, clusters, nodeById, adjacency, colors, state, basePositions, localManagement, esc });
})();
