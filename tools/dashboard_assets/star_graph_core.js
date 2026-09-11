globalThis.KBStarGraphCore = (() => {
  const DEFAULT_TYPE_FILTERS = ["来源资料", "拆解记录", "知识原子", "专题知识", "需求记录", "方案成果", "验证反馈", "治理规则"];
  const OPERATIONS_STAGES = [
    "资料读取",
    "结构解析",
    "分解与查重",
    "知识原子与原子规则",
    "业务规则与关系链接",
    "需求理解与知识召回",
    "输出准备与售前交接",
    "交付反馈与知识反哺",
  ];

  function worldToScreen(point, camera) {
    return {
      x: (point.x - camera.x) * camera.zoom + camera.width / 2,
      y: (point.y - camera.y) * camera.zoom + camera.height / 2,
    };
  }

  function screenToWorld(point, camera) {
    return {
      x: (point.x - camera.width / 2) / camera.zoom + camera.x,
      y: (point.y - camera.height / 2) / camera.zoom + camera.y,
    };
  }

  function visibleLabel(node, zoom) {
    const priority = Number(node.label_priority || 1);
    if (priority >= 4) return zoom >= 0.9;
    if (priority >= 3) return zoom >= 1.25;
    if (priority >= 2) return zoom >= 1.75;
    return zoom >= 2.5;
  }

  // 「要不要自动刷新页面」的纯判断，便于确定性测试。
  // 自动刷新靠服务端 /api/version 的指纹（工具版本 + 页面 mtime）。
  function reloadDecision(previousToken, currentToken, hasPendingEdit) {
    if (!currentToken) return "wait";          // 服务在重启或没给指纹，下一轮再看
    if (previousToken === null || previousToken === undefined) return "record";
    if (currentToken === previousToken) return "wait";
    // 有未提交的差异预览时先不刷，否则用户刚编辑的内容会丢
    if (hasPendingEdit) return "defer";
    return "reload";
  }

  function buildSpatialGrid(nodes, cellSize = 48) {
    const cells = new Map();
    for (const node of nodes) {
      const key = `${Math.floor(node.x / cellSize)},${Math.floor(node.y / cellSize)}`;
      if (!cells.has(key)) cells.set(key, []);
      cells.get(key).push(node);
    }
    return {cells, cellSize};
  }

  function hitTest(grid, point, extraRadius = 5) {
    const baseX = Math.floor(point.x / grid.cellSize);
    const baseY = Math.floor(point.y / grid.cellSize);
    let result = null;
    let best = Infinity;
    for (let dx = -1; dx <= 1; dx += 1) {
      for (let dy = -1; dy <= 1; dy += 1) {
        const candidates = grid.cells.get(`${baseX + dx},${baseY + dy}`) || [];
        for (const node of candidates) {
          const distance = Math.hypot(node.x - point.x, node.y - point.y);
          if (distance <= Number(node.radius || 3) + extraRadius && distance < best) {
            result = node;
            best = distance;
          }
        }
      }
    }
    return result;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function shouldCollapseLens(viewportWidth, hasSelection) {
    return Boolean(hasSelection) && viewportWidth < 900;
  }

  function defaultTypeFilters() {
    return [...DEFAULT_TYPE_FILTERS];
  }

  function displayType(value) {
    return value;
  }

  function selectedValues(selection) {
    if (!selection) return [];
    if (Array.isArray(selection)) return selection;
    if (typeof selection[Symbol.iterator] === "function") return [...selection];
    return [];
  }

  function passesFilters(node, filters = {}, healthByNodeId = {}) {
    if (!node) return false;
    const types = selectedValues(filters.types);
    const industries = selectedValues(filters.industries);
    const evidence = selectedValues(filters.evidence).map(String);
    const health = selectedValues(filters.health);
    if (filters.types != null && !types.includes(node.asset_type)) return false;
    if (industries.length && !industries.some(value => (node.industries || []).includes(value))) return false;
    if (evidence.length && !evidence.includes(String(node.evidence_level || ""))) return false;
    if (health.length && !health.includes(healthByNodeId[node.id] || "healthy")) return false;
    return true;
  }

  function layerAllowsNode(node, layers = {}) {
    if (layers.nodes === false) return false;
    if (node?.dimension === "产品维度" && layers.products === false) return false;
    if (node?.dimension === "规则维度" && layers.rules === false) return false;
    if (node?.asset_type === "运行记录" && layers.updates === false) return false;
    return true;
  }

  function layerAllowsHealth(layers = {}) {
    return layers.health !== false;
  }

  function toggleSelection(selection, value) {
    if (selection.has(value)) selection.delete(value);
    else selection.add(value);
    return selection;
  }

  function selectionSummary(labels, options = {}) {
    if (options.allSelected) return "全部";
    if (labels.length) return labels.join("、");
    return options.emptyMeansAll ? "全部" : "未选择";
  }

  function selectionContainsAll(selection, required = []) {
    const values = new Set(selectedValues(selection));
    return required.every(value => values.has(value));
  }

  function graphControlsVisible(expanded, nodeOpen) {
    return Boolean(expanded) && !nodeOpen;
  }

  function relationFamily(edge) {
    if (edge?.family) return edge.family;
    if (edge?.relation === "decomposes_to") return "source";
    if (edge?.relation && edge.relation !== "wikilink") return "call";
    return "unclassified";
  }

  function relationViewAllowsEdge(view, edge) {
    const family = relationFamily(edge);
    if (view === "source") return family === "source";
    if (view === "call") return family === "call";
    return true;
  }

  function relationViewCounts(nodeId, edges = []) {
    const result = {source: 0, call: 0, all: 0};
    for (const edge of edges) {
      if (edge.source !== nodeId && edge.target !== nodeId) continue;
      result.all += 1;
      const family = relationFamily(edge);
      if (family === "source") result.source += 1;
      if (family === "call") result.call += 1;
    }
    return result;
  }

  function relationNeighborhood(nodeId, depth, edges = [], view = "all") {
    const adjacency = new Map();
    for (const edge of edges) {
      if (!relationViewAllowsEdge(view, edge)) continue;
      if (!adjacency.has(edge.source)) adjacency.set(edge.source, new Set());
      if (!adjacency.has(edge.target)) adjacency.set(edge.target, new Set());
      adjacency.get(edge.source).add(edge.target);
      adjacency.get(edge.target).add(edge.source);
    }
    const seen = new Set([nodeId]);
    let frontier = new Set([nodeId]);
    for (let level = 0; level < depth; level += 1) {
      const next = new Set();
      for (const current of frontier) {
        for (const neighbor of adjacency.get(current) || []) {
          if (!seen.has(neighbor)) {
            seen.add(neighbor);
            next.add(neighbor);
          }
        }
      }
      frontier = next;
    }
    return seen;
  }

  function sourceChain(nodeId, edges = [], nodes = []) {
    const byId = new Map(nodes.map(n => [n.id,n]));
    const allowed = new Set(["来源资料","拆解记录","知识原子","专题知识"]);
    const seen = new Set([nodeId]);
    for (const forward of [true,false]) {
      const visited = new Set([nodeId]), queue = [nodeId];
      while (queue.length) {
        const id = queue.shift(), current = byId.get(id);
        if (forward && id !== nodeId && current?.asset_type === "知识原子") continue;
        for (const edge of edges) {
          if (relationFamily(edge) !== "source" || (forward ? edge.source : edge.target) !== id) continue;
          const next = forward ? edge.target : edge.source;
          if (!allowed.has(byId.get(next)?.asset_type) || visited.has(next)) continue;
          visited.add(next); seen.add(next); queue.push(next);
        }
      }
    }
    return seen;
  }

  const ATOMIC_DIMENSIONS = ["行业维度","工程维度","客户维度","产品维度","管理维度","资源维度","业务维度","案例维度","规则维度"];

  function isMainlineNode(node) {
    if (!node || ["运行记录","专题知识","拆解记录"].includes(node.asset_type)) return false;
    const names=[String(node.title||""),String(node.path||"").split('/').pop().replace(/\.md$/i,'')];
    return !names.some(name=>/^readme(?:$|[-_.\s])/i.test(name.trim()) || /(?:索引|汇总|汇总表|汇总清单|导航)(?:[-_（(].*)?$/.test(name.trim()));
  }

  function mainlineGraph(nodes = [], edges = [], showBackground = false) {
    if(showBackground) return {nodes:[...nodes],edges:[...edges]};
    const visible=nodes.filter(isMainlineNode), ids=new Set(visible.map(n=>n.id));
    const byId=new Map(nodes.map(n=>[n.id,n])), outgoing=new Map();
    const result=edges.filter(e=>ids.has(e.source)&&ids.has(e.target));
    const pairs=new Set(result.filter(e=>relationFamily(e)==='source').map(e=>`${e.source}>${e.target}`));
    for(const edge of edges){
      if(relationFamily(edge)!=='source')continue;
      if(!outgoing.has(edge.source))outgoing.set(edge.source,[]);
      outgoing.get(edge.source).push(edge.target);
    }
    for(const node of visible){
      const queue=[{id:node.id,via:[]}],visited=new Set([node.id]);
      while(queue.length){
        const current=queue.shift();
        for(const next of outgoing.get(current.id)||[]){
          if(next===node.id||!byId.has(next))continue;
          if(ids.has(next)){
            const key=`${node.id}>${next}`;
            if(current.via.length&&!pairs.has(key)){
              pairs.add(key);result.push({id:`projected:${key}`,source:node.id,target:next,family:'source',relation:'decomposes_to',projected:true,via:current.via});
            }
          }else if(!visited.has(next)){
            visited.add(next);queue.push({id:next,via:[...current.via,next]});
          }
        }
      }
    }
    return {nodes:visible,edges:result};
  }

  function canvasNoteAction(selectedId, candidateId, doubleClick = false) {
    if (!candidateId) return "none";
    if (!selectedId || doubleClick) return "select";
    return selectedId === candidateId ? "current" : "locate";
  }

  function focusNeighborhood(nodeId, edges = [], nodes = [], depth = 1) {
    const byId = new Map(nodes.map(n => [n.id,n]));
    const seen = relationNeighborhood(nodeId, depth, edges, "all");
    const forward = ["来源资料","拆解记录"].includes(byId.get(nodeId)?.asset_type);
    const visited = new Set([nodeId]), queue = [nodeId];
    while (queue.length) {
      const id = queue.shift(), current = byId.get(id);
      if (id !== nodeId && (forward ? current?.asset_type === "知识原子" : current?.asset_type === "来源资料")) continue;
      for (const edge of edges) {
        if (relationFamily(edge) !== "source" || (forward ? edge.source : edge.target) !== id) continue;
        const next = forward ? edge.target : edge.source;
        if (!byId.has(next) || visited.has(next)) continue;
        if (forward && !["来源资料","拆解记录","知识原子"].includes(byId.get(next).asset_type)) continue;
        visited.add(next); seen.add(next); queue.push(next);
      }
    }
    return seen;
  }

  function dimensionCoverage(nodes = [], review = {}) {
    return ATOMIC_DIMENSIONS.map(dimension => {
      const count = nodes.filter(n => n.asset_type === "知识原子" && n.dimension === dimension).length;
      return {dimension,count,status:count ? "linked" : review[dimension] || "unreviewed"};
    });
  }

  function focusGroup(node) {
    return node.asset_type === "知识原子" ? (ATOMIC_DIMENSIONS.includes(node.dimension) ? node.dimension : "待定维度") : node.asset_type;
  }

  // 资产类型筛选的默认选集：必须在硬编码清单之外，把数据里实际出现的类型也收进来。
  // 分类规则可由用户自定义（分发包模板的兜底类型是「未分类」），若只认 DEFAULT_TYPE_FILTERS，
  // 这类节点会被 passesFilters 静默过滤干净，首次打开就是一张空图。
  // 「运行记录」维持原设计：默认不选，由"显示后台记录"开关另行加入。
  function effectiveTypeFilters(nodes = []) {
    const present = [];
    for (const node of nodes) {
      const type = String(node?.asset_type ?? "");
      if (type && !present.includes(type)) present.push(type);
    }
    const known = DEFAULT_TYPE_FILTERS.filter(type => present.includes(type));
    const extra = present
      .filter(type => !DEFAULT_TYPE_FILTERS.includes(type) && type !== "运行记录")
      .sort();
    return [...known, ...extra];
  }

  function focusLayout(nodeId, nodes = []) {
    const titles = ["来源资料","拆解记录",...ATOMIC_DIMENSIONS,"待定维度","专题知识","需求记录","方案成果","验证反馈","治理规则","运行记录"];
    const positions = [{id:nodeId,x:920,y:-100}], groups = [], heights = [0,0,0,0,0];
    titles.forEach(title => {
      const members = nodes.filter(n => n.id !== nodeId && focusGroup(n) === title).sort((a,b) => a.title.localeCompare(b.title,"zh-CN"));
      if (!members.length) return;
      const index = ATOMIC_DIMENSIONS.indexOf(title);
      const col = index >= 0 ? 2 + index % 3 : title === "待定维度" ? 4 : heights[0] <= heights[1] ? 0 : 1;
      const x = col * 460, y = heights[col];
      groups.push({title,count:members.length,x,y,members});
      members.forEach((n,i) => positions.push({id:n.id,x,y:y+44+i*42}));
      heights[col] += members.length*42+92;
    });
    return {positions,groups};
  }

  function semanticSourceTypes(nodeId, edges = [], nodes = []) {
    const byId = new Map(nodes.map(node => [node.id, node]));
    const types = new Set();
    for (const edge of edges) {
      if (relationFamily(edge) !== "source" || edge.target !== nodeId) continue;
      const sourceType = byId.get(edge.source)?.asset_type;
      if (sourceType) types.add(sourceType);
    }
    return [...types].sort((a, b) => {
      const ai = DEFAULT_TYPE_FILTERS.indexOf(a);
      const bi = DEFAULT_TYPE_FILTERS.indexOf(b);
      return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi) || a.localeCompare(b);
    });
  }

  function normalizedDate(value) {
    const match = String(value || "").match(/^\d{4}-\d{2}-\d{2}/);
    return match ? match[0] : "";
  }

  function isMissingSource(node) {
    if (Array.isArray(node?.source_ref)) return node.source_ref.length === 0;
    return !String(node?.source_ref || "").trim();
  }

  function isLowEvidence(node) {
    const value = String(node?.evidence_level ?? "").trim();
    if (!value) return true;
    const numeric = Number(value);
    return Number.isFinite(numeric) && numeric <= 1;
  }

  function operationalMetrics(nodes, healthByNodeId = {}) {
    const dates = nodes.map(node => normalizedDate(node.modified)).filter(Boolean).sort();
    return {
      current: nodes.length,
      missingSource: nodes.filter(isMissingSource).length,
      lowEvidence: nodes.filter(isLowEvidence).length,
      governance: nodes.filter(node => Boolean(healthByNodeId[node.id])).length,
      latestUpdated: dates.at(-1) || "—",
    };
  }

  function passesOperationalFilters(node, selected, healthByNodeId = {}, latestUpdated = "") {
    const filters = selectedValues(selected);
    if (!filters.length) return true;
    return filters.some(filter => {
      if (filter === "missingSource") return isMissingSource(node);
      if (filter === "lowEvidence") return isLowEvidence(node);
      if (filter === "governance") return Boolean(healthByNodeId[node.id]);
      if (filter === "latestUpdated") return normalizedDate(node.modified) === latestUpdated;
      return false;
    });
  }

  function buildOperationsLayout(task, width, height) {
    const columns = width < 1000 ? 4 : 8;
    const rows = Math.ceil(OPERATIONS_STAGES.length / columns);
    const marginLeft = width > 560 ? Math.min(360, width * .44) : Math.max(54, width * .1);
    const marginRight = Math.min(150, Math.max(54, width * .08));
    const usableWidth = Math.max(1, width - marginLeft - marginRight);
    const rowGap = rows > 1 ? Math.min(190, height * .24) : 0;
    const activeIndex = Math.max(0, OPERATIONS_STAGES.indexOf(task?.stage || OPERATIONS_STAGES[0]));
    const stageNodes = OPERATIONS_STAGES.map((title, index) => {
      const row = Math.floor(index / columns);
      const column = index % columns;
      const rowCount = Math.min(columns, OPERATIONS_STAGES.length - row * columns);
      const x = rowCount === 1 ? (marginLeft + width - marginRight) / 2 : marginLeft + column * (usableWidth / (rowCount - 1));
      const y = height * .44 + (row - (rows - 1) / 2) * rowGap;
      return {
        id: `stage:${title}`,
        kind: "stage",
        title,
        x,
        y,
        status: index < activeIndex ? "complete" : index === activeIndex ? "active" : "pending",
      };
    });
    const stageEdges = stageNodes.slice(1).map((node, index) => ({
      source: stageNodes[index].id,
      target: node.id,
      kind: "stage-flow",
    }));
    const sourceX = Math.min(width - 60, Math.max(marginLeft, 330));
    const sourceNodes = (task?.sources || []).map((source, index) => ({
      ...source,
      id: source.node_id,
      kind: "source",
      x: sourceX,
      y: Math.max(120, height * .16 + index * 48),
    }));
    const sourceEdges = sourceNodes.map(node => ({
      source: node.id,
      target: "stage:资料读取",
      kind: "source-input",
    }));
    const stageByTitle = new Map(stageNodes.map(node => [node.title, node]));
    const candidateBase = stageByTitle.get("分解与查重");
    const candidateNodes = (task?.candidates || []).slice(0, 12).map((candidate, index) => ({
      ...candidate,
      id: candidate.id,
      title: compactLabel(candidate.title, 20),
      kind: "candidate",
      x: candidateBase.x + ((index % 3) - 1) * 78,
      y: candidateBase.y - 88 - Math.floor(index / 3) * 42,
      show_label: index < 1,
    }));
    const candidateEdges = candidateNodes.map(node => ({source: node.id, target: candidateBase.id, kind: "candidate-output"}));
    const requirementBase = stageByTitle.get("需求理解与知识召回");
    const requirementNodes = (task?.requirements || []).slice(0, 8).map((requirement, index) => ({
      ...requirement,
      id: requirement.id,
      title: compactLabel(requirement.text, 20),
      kind: "requirement",
      x: requirementBase.x + ((index % 2) ? 58 : -58),
      y: requirementBase.y + 78 + Math.floor(index / 2) * 42,
      show_label: index < 1,
    }));
    const requirementEdges = requirementNodes.map(node => ({source: node.id, target: requirementBase.id, kind: "requirement-input"}));
    const retrievalNodes = (task?.retrievals || []).slice(0, 12).map((retrieval, index) => ({
      ...retrieval,
      id: retrieval.node_id,
      title: compactLabel(retrieval.title, 24),
      kind: "retrieval",
      x: requirementBase.x + 130 + (index % 3) * 55,
      y: requirementBase.y + 58 + Math.floor(index / 3) * 38,
      show_label: index < 1,
    }));
    const retrievalTarget = requirementNodes[0]?.id || requirementBase.id;
    const retrievalEdges = retrievalNodes.map(node => ({source: node.id, target: retrievalTarget, kind: "knowledge-recall"}));
    const packageBase = stageByTitle.get("输出准备与售前交接");
    const packageNodes = task?.knowledge_package ? [{
      id: `package:${task.knowledge_package.task_id}`,
      kind: "package",
      title: task.knowledge_package.title || "售前任务知识包",
      x: packageBase.x,
      y: packageBase.y + 120,
      status: task.knowledge_package.status || "draft",
    }] : [];
    const packageEdges = packageNodes.map(node => ({source: packageBase.id, target: node.id, kind: "package-output"}));
    return {
      nodes: [...stageNodes, ...sourceNodes, ...candidateNodes, ...requirementNodes, ...retrievalNodes, ...packageNodes],
      edges: [...stageEdges, ...sourceEdges, ...candidateEdges, ...requirementEdges, ...retrievalEdges, ...packageEdges],
    };
  }

  function operationSourceSummary(task) {
    if (!task) return "尚未创建任务草稿";
    const count = Array.isArray(task.sources) ? task.sources.length : 0;
    return count ? `已加入${count}份来源` : "尚未加入来源";
  }

  function isLocalManagementLocation(protocol, hostname) {
    return protocol === "http:" && ["127.0.0.1", "localhost"].includes(hostname);
  }

  function compactLabel(value, limit = 24) {
    const clean = String(value || "").replace(/\s+/g, " ").trim();
    return clean.length <= limit ? clean : `${clean.slice(0, Math.max(1, limit - 1))}…`;
  }


  // ---------- 变化时间镜头（设计规格 §3.1「哪些区域正在变化」）----------
  // 五个互斥时间桶：多选即取并集，语义与其它镜头一致。
  const TIME_BUCKETS = ["7d", "30d", "90d", "365d", "older"];

  function timeBuckets() {
    return TIME_BUCKETS.slice();
  }

  function timeBucketLabel(bucket) {
    return {
      "7d": "近 7 天",
      "30d": "8–30 天",
      "90d": "31–90 天",
      "365d": "91–365 天",
      "older": "一年以上",
    }[bucket] || String(bucket);
  }

  /** 把节点归入时间桶；日期不可解析返回 null（不臆造归属）。 */
  function timeBucketOf(node, reference) {
    const date = normalizedDate(node?.modified);
    const ref = normalizedDate(reference);
    if (!date || !ref) return null;
    const days = Math.floor((Date.parse(ref) - Date.parse(date)) / 86400000);
    if (!Number.isFinite(days)) return null;
    if (days <= 7) return "7d";      // 含未来日期（负数）与刚刚修改
    if (days <= 30) return "30d";
    if (days <= 90) return "90d";
    if (days <= 365) return "365d";
    return "older";
  }

  /** 未选任何桶 = 全部通过；无法解析日期的节点在启用筛选时被排除，真实缺口不掩饰。 */
  function passesTimeFilter(node, selected, reference) {
    const buckets = selectedValues(selected);
    if (!buckets.length) return true;
    const bucket = timeBucketOf(node, reference);
    return bucket ? buckets.includes(bucket) : false;
  }

  /** 各桶计数：计数本身即"变化分布"，无需另做直方图。 */
  function timeBucketCounts(nodes, reference) {
    const counts = Object.fromEntries(TIME_BUCKETS.map(bucket => [bucket, 0]));
    let unknown = 0;
    for (const node of nodes || []) {
      const bucket = timeBucketOf(node, reference);
      if (bucket) counts[bucket] += 1;
      else unknown += 1;
    }
    return { counts, unknown };
  }


  // ---------- 时间轴回放（参照 Obsidian Graph view 的 Animate）----------
  // 注：本库节点只有 modified、没有 created，故按【修改时间】回放，不冒充创建时间。
  function playbackTimeline(nodes) {
    const dates = [...new Set((nodes || []).map(node => normalizedDate(node?.modified)).filter(Boolean))].sort();
    return { dates, min: dates[0] || "", max: dates.at(-1) || "", total: dates.length };
  }

  /** cursor 为空 = 未启用回放，全部通过；否则只显示“截止该日已存在”的节点。 */
  function withinPlayback(node, cursor) {
    if (!cursor) return true;
    const date = normalizedDate(node?.modified);
    return date ? date <= cursor : false;
  }


  // ---------- 分组着色（参照 Obsidian Graph view 的 Groups）----------
  /** 与既有搜索保持同一字段口径，另补 industries / 九维，便于按维度或行业分组。 */
  function nodeSearchText(node) {
    return [
      node?.title, node?.path, node?.summary, node?.dimension, node?.sub_dimension,
      ...(node?.tags || []), ...(node?.aliases || []), ...(node?.industries || []),
    ].filter(Boolean).join(" ").toLowerCase();
  }

  /** 空格分隔视为「与」。空查询不匹配任何节点——否则一个空分组会吞掉全库。 */
  function matchQuery(node, query) {
    const terms = String(query || "").toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) return false;
    const text = nodeSearchText(node);
    return terms.every(term => text.includes(term));
  }

  /** 先定义的分组优先；无命中返回 null，交由调用方回落到资产类型配色。 */
  function resolveGroupColor(node, groups) {
    for (const group of groups || []) {
      if (matchQuery(node, group?.query)) return group.color || null;
    }
    return null;
  }

  return {
    worldToScreen,
    screenToWorld,
    visibleLabel,
    buildSpatialGrid,
    hitTest,
    clamp,
    shouldCollapseLens,
    defaultTypeFilters,
    effectiveTypeFilters,
    displayType,
    passesFilters,
    layerAllowsNode,
    layerAllowsHealth,
    toggleSelection,
    selectionSummary,
    selectionContainsAll,
    graphControlsVisible,
    relationFamily,
    relationViewAllowsEdge,
    relationViewCounts,
    relationNeighborhood,
    sourceChain,
    focusNeighborhood,
    canvasNoteAction,
    isMainlineNode,
    mainlineGraph,
    focusLayout,
    focusGroup,
    dimensionCoverage,
    semanticSourceTypes,
    operationalMetrics,
    passesOperationalFilters,
    buildOperationsLayout,
    operationSourceSummary,
    isLocalManagementLocation,
    compactLabel,
    timeBuckets,
    timeBucketLabel,
    timeBucketOf,
    passesTimeFilter,
    timeBucketCounts,
    playbackTimeline,
    withinPlayback,
    matchQuery,
    resolveGroupColor,
    reloadDecision,
  };
})();
