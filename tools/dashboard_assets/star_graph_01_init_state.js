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

  // 知识类型配色是有语义的，图例与画布必须用同一份，所以两套主题各给一份，
  // 由 G.setPalette 统一切换——不能只在 CSS 里反相画布，那样图例会撒谎。
  const NODE_COLORS = {
    dark: {"来源资料":"#27c2d1","拆解记录":"#4c9ab7","知识原子":"#2f6bff","专题知识":"#8b5cf6","需求记录":"#d36ba6","方案成果":"#1fb8d0","验证反馈":"#48b66e","治理规则":"#f2b84b","运行记录":"#51657d","未分类":"#8a94a6"},
    // 浅色版同色相压暗，保证在白底上够对比（不是简单反相）
    light: {"来源资料":"#0e8fa4","拆解记录":"#2b6f8c","知识原子":"#1f4fd8","专题知识":"#6d3fd4","需求记录":"#b34a85","方案成果":"#0f8ba3","验证反馈":"#2c8f52","治理规则":"#a8760a","运行记录":"#5b6b7e","未分类":"#6b7688"},
  };
  const PAINT = {
    dark: {grid:"rgba(67,111,161,.075)", edgeSource:"rgba(39,194,209,.55)", edgeDefault:"rgba(76,139,213,.42)",
           clusterLabel:"#d9edff", clusterHalo:"#cce7ff", nodeGlyph:"#dff1ff", nodeGlyphDim:"#7e98b4",
           nodeFill:"#e8f4ff", locatedRing:"#bdeaff", miniBg:"#071427", miniStroke:"#c2dcff",
           healthBlocked:"#ef5b68", healthWarn:"#f2b84b", fallback:"#60748c"},
    light: {grid:"rgba(90,120,160,.14)", edgeSource:"rgba(14,143,164,.5)", edgeDefault:"rgba(60,110,180,.32)",
            clusterLabel:"#1d3a5c", clusterHalo:"#2a4a6b", nodeGlyph:"#12314f", nodeGlyphDim:"#5c7186",
            nodeFill:"#12314f", locatedRing:"#1f4fd8", miniBg:"#eef2f7", miniStroke:"#5c7186",
            healthBlocked:"#c0392b", healthWarn:"#a8760a", fallback:"#6b7688"},
  };
  const colors = Object.assign({}, NODE_COLORS.dark);
  // 必须是同一个对象被原地更新：02 模块在加载时就解构了 paint，
  // 换对象会让它一直用旧引用。
  const paint = Object.assign({}, PAINT.dark);
  G.setPalette = name => {
    const key = name === "light" ? "light" : "dark";
    Object.assign(colors, NODE_COLORS[key]);
    Object.assign(paint, PAINT[key]);
  };
  G.paint = paint;
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
