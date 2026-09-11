// 渲染主循环与各绘制层（网格/运营/星系/连线/节点/小地图）
// 由 star_graph.js 按接缝拆分后改造为独立模块；模块间只通过 globalThis.KBStarGraph 通信。
globalThis.KBStarGraph = globalThis.KBStarGraph || {};
(() => {
  "use strict";
  const G = globalThis.KBStarGraph;
  const { data, core, canvas, ctx, mini, miniCtx, nodes, edges, clusters, nodeById, adjacency, colors, state, basePositions, localManagement, esc } = G;

  function requestRender() {
    if (G.frameRequested) return;
    G.frameRequested = true;
    requestAnimationFrame(() => {
      G.frameRequested = false;
      render();
    });
  }

  function resize() {
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.max(1, Math.floor(rect.width * ratio));
    canvas.height = Math.max(1, Math.floor(rect.height * ratio));
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    state.camera.width = rect.width;
    state.camera.height = rect.height;
    mini.width = 380;
    mini.height = 184;
    updateGraphControls();
    requestRender();
  }

  function updateGraphControls() {
    const controls = document.getElementById("graphControls");
    const toggle = document.getElementById("controlsToggle");
    const visible = core.graphControlsVisible(state.controlsExpanded, state.nodeOpen);
    controls.hidden = !visible;
    toggle.hidden = state.nodeOpen;
    toggle.setAttribute("aria-expanded", String(visible));
    toggle.setAttribute("aria-label", `${visible ? "收起" : "展开"}星图控制`);
  }

  function passesScope(node) {
    if(!G.showBackground&&!core.isMainlineNode(node))return false;
    if (!core.layerAllowsNode(node, state.layers)) return false;
    if (!core.passesFilters(node, state.filters, data.graph.healthByNodeId)) return false;
    if (!core.passesTimeFilter(node, state.filters.time, G.timeReference)) return false;
    return core.withinPlayback(node, state.playback.active ? state.playback.cursor : "");
  }

  function passes(node) {
    if (!node) return false;
    if(!G.showBackground&&!core.isMainlineNode(node))return false;
    if (state.localSet) return state.localSet.has(node.id);
    if (!passesScope(node)) return false;
    if (!core.passesOperationalFilters(node, state.operationalFilters, data.graph.healthByNodeId, G.currentOperationalMetrics.latestUpdated)) return false;
    return !state.localSet || state.localSet.has(node.id);
  }

  function alpha(node) {
    if (!passes(node)) return .02;
    if (node.id === state.located) return 1;
    const focus = state.hovered || state.selected;
    if (!focus) return node.asset_type === "运行记录" ? .2 : .74;
    if (node.id === focus) return 1;
    if (adjacency.get(focus)?.has(node.id)) return .92;
    return state.localSet?.has(node.id) ? .5 : .08;
  }

  function render() {
    const rect = canvas.getBoundingClientRect();
    // 重绘开头显式复位画布状态：避免上一次绘制遗留的 globalAlpha / 线型影响本次结果
    ctx.globalAlpha = 1;
    ctx.setLineDash([]);
    ctx.clearRect(0, 0, rect.width, rect.height);
    drawGrid(rect);
    if (state.mode === "operations") {
      drawOperations(rect);
      return;
    }
    drawClusters();
    drawEdges();
    drawNodes();
    drawMini();
  }

  function drawGrid(rect) {
    ctx.save();
    ctx.strokeStyle = "rgba(67,111,161,.075)";
    const gap = 42;
    for (let x = (rect.width / 2 - state.camera.x * state.camera.zoom) % gap; x < rect.width; x += gap) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, rect.height); ctx.stroke();
    }
    for (let y = (rect.height / 2 - state.camera.y * state.camera.zoom) % gap; y < rect.height; y += gap) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(rect.width, y); ctx.stroke();
    }
    ctx.restore();
  }

  function drawOperations(rect) {
    const layout = core.buildOperationsLayout(state.operationTask, rect.width, rect.height);
    const byId = new Map(layout.nodes.map(node => [node.id, node]));
    ctx.save();
    for (const edge of layout.edges) {
      const source = byId.get(edge.source);
      const target = byId.get(edge.target);
      if (!source || !target) continue;
      ctx.strokeStyle = edge.kind === "source-input" ? "rgba(39,194,209,.55)" : "rgba(76,139,213,.42)";
      ctx.lineWidth = edge.kind === "source-input" ? 1.4 : 2;
      ctx.setLineDash(edge.kind === "source-input" ? [5, 6] : []);
      ctx.beginPath();
      ctx.moveTo(source.x, source.y);
      ctx.lineTo(target.x, target.y);
      ctx.stroke();
    }
    ctx.setLineDash([]);
    for (const node of layout.nodes) {
      if (node.kind !== "stage") {
        const nodeColors = {source:"#27c2d1",candidate:"#8b5cf6",requirement:"#d36ba6",retrieval:"#48b66e",package:"#f2b84b"};
        const nodeColor = nodeColors[node.kind] || "#60748c";
        ctx.fillStyle = nodeColor;
        ctx.shadowColor = nodeColor;
        ctx.shadowBlur = 10;
        ctx.beginPath(); ctx.arc(node.x, node.y, node.kind === "package" ? 10 : 7, 0, Math.PI * 2); ctx.fill();
        ctx.shadowBlur = 0;
        if (node.show_label !== false) {
          ctx.fillStyle = "#cdeff4";
          ctx.font = "600 11px system-ui";
          ctx.textAlign = "left";
          ctx.fillText(core.compactLabel(node.title, 22), node.x + 12, node.y + 4);
        }
        continue;
      }
      const palette = node.status === "complete" ? ["#153d37", "#45bf8b"] : node.status === "active" ? ["#153d75", "#65b9ff"] : ["#0b1a30", "#35567b"];
      ctx.fillStyle = palette[0];
      ctx.strokeStyle = palette[1];
      ctx.lineWidth = node.status === "active" ? 2.5 : 1.2;
      ctx.beginPath(); ctx.arc(node.x, node.y, node.status === "active" ? 16 : 13, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = node.status === "pending" ? "#70859d" : "#e5f4ff";
      ctx.font = `${node.status === "active" ? "700" : "600"} 11px system-ui`;
      ctx.textAlign = "center";
      ctx.fillText(node.title.slice(0, 16), node.x, node.y + 34);
    }
    ctx.fillStyle = "#dff1ff";
    ctx.font = "700 18px system-ui";
    ctx.textAlign = "center";
    ctx.fillText(state.operationTask?.title || "创建任务后开始知识运营", rect.width / 2, 58);
    ctx.fillStyle = "#7e98b4";
    ctx.font = "500 11px system-ui";
    ctx.fillText(state.operationTask ? `当前阶段：${state.operationTask.stage}` : "当前为只读阶段预览", rect.width / 2, 80);
    ctx.restore();
  }

  function drawClusters() {
    if (state.selected) {
      for (const column of G.chainColumns) {
        const p=core.worldToScreen(column,state.camera);
        ctx.fillStyle="#cce7ff";ctx.font="600 13px system-ui";ctx.textAlign="left";
        ctx.fillText(`${column.title} · ${column.count}`,p.x,p.y);
      }
      return;
    }
    for (const cluster of clusters) {
      const visibleCount = nodes.reduce((count, node) => count + Number(node.asset_type === cluster.id && passes(node)), 0);
      if (!visibleCount) continue;
      const p = core.worldToScreen(cluster, state.camera);
      const radius = Math.max(50, Math.sqrt(visibleCount) * 15) * state.camera.zoom;
      const color = colors[cluster.id] || "#60748c";
      const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, radius);
      glow.addColorStop(0, color + "28");
      glow.addColorStop(1, color + "00");
      ctx.fillStyle = glow;
      ctx.beginPath(); ctx.arc(p.x, p.y, radius, 0, Math.PI * 2); ctx.fill();
      if (state.camera.zoom < .9) {
        ctx.fillStyle = "#d9edff";
        ctx.font = "700 15px system-ui";
        ctx.textAlign = "center";
        ctx.fillText(`${core.displayType(cluster.title)} · ${visibleCount}`, p.x, p.y);
      }
    }
  }

  function drawEdges() {
    const focus = state.hovered || state.selected;
    for (const edge of G.mainline.edges) {
      if (!core.relationViewAllowsEdge(state.relationView, edge)) continue;
      const a = nodeById.get(edge.source);
      const b = nodeById.get(edge.target);
      if (!passes(a) || !passes(b)) continue;
      let opacity = state.camera.zoom < .7 ? .014 : .075;
      if (!state.selected && core.relationFamily(edge) === "source") opacity = .11;
      if (state.selected) opacity = .30;
      if (focus && (edge.source === focus || edge.target === focus)) opacity = .55;
      const p1 = core.worldToScreen(a, state.camera);
      const p2 = core.worldToScreen(b, state.camera);
      const family = core.relationFamily(edge);
      const rgb = family === "source" ? "39,194,209" : family === "call" ? "139,92,246" : "104,169,235";
      ctx.strokeStyle = `rgba(${rgb},${opacity})`;
      ctx.lineWidth = opacity > .5 ? family === "source" ? 1.7 : family === "call" ? 1.45 : 1.0 : .5;
      ctx.setLineDash(edge.projected?[4,4]:[]);
      ctx.beginPath(); ctx.moveTo(p1.x, p1.y); ctx.lineTo(p2.x, p2.y); ctx.stroke();
      ctx.setLineDash([]);
      if ((state.selected || state.showArrows) && family !== "unclassified") {
        // 箭头是细小元素，若继承连线的低透明度（zoom<0.7 时仅 .014）等于看不见——
        // 那样开关点了没反应。给箭头单独提高对比度，连线保持克制。
        ctx.strokeStyle = `rgba(${rgb},${Math.min(1, Math.max(opacity * 2.6, .38))})`;
        ctx.lineWidth = 1.1;
        const angle=Math.atan2(p2.y-p1.y,p2.x-p1.x), offset=Math.max(5,b.radius*state.camera.zoom+3);
        const tip={x:p2.x-Math.cos(angle)*offset,y:p2.y-Math.sin(angle)*offset};
        ctx.beginPath();ctx.moveTo(tip.x-7*Math.cos(angle-.45),tip.y-7*Math.sin(angle-.45));ctx.lineTo(tip.x,tip.y);ctx.lineTo(tip.x-7*Math.cos(angle+.45),tip.y-7*Math.sin(angle+.45));ctx.stroke();
      }
    }
  }

  /** 节点配色：分组优先，未命中回落资产类型色（分组是用户显式定义，故优先于默认分类）。 */
  function nodeColor(node) {
    return core.resolveGroupColor(node, state.groups) || colors[node.asset_type] || "#60748c";
  }

  function drawNodes() {
    const drawnLabels = [];
    const drawOrder = state.selected ? [...nodes].sort((a,b) => Number(b.id === state.selected) - Number(a.id === state.selected)) : nodes;
    for (const node of drawOrder) {
      const opacity = alpha(node);
      if (opacity <= .02) continue;
      const p = core.worldToScreen(node, state.camera);
      const r = Math.max(1.5, node.radius * state.camera.zoom);
      if (p.x + 20 < 0 || p.y + 20 < 0 || p.x - 20 > state.camera.width || p.y - 20 > state.camera.height) continue;
      const color = nodeColor(node);
      const health = data.graph.healthByNodeId?.[node.id];
      if (health && core.layerAllowsHealth(state.layers)) {
        ctx.globalAlpha = .9;
        ctx.strokeStyle = health === "blocked" ? "#ef5b68" : "#f2b84b";
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(p.x, p.y, r + 4, 0, Math.PI * 2); ctx.stroke();
      }
      ctx.globalAlpha = opacity;
      ctx.fillStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = node.id === state.selected ? 18 : node.id === state.hovered || node.id === state.located ? 12 : 3;
      ctx.beginPath(); ctx.arc(p.x, p.y, node.id === state.selected ? r + 3 : r, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0;
      if(node.id === state.located){ctx.strokeStyle="#bdeaff";ctx.lineWidth=2;ctx.beginPath();ctx.arc(p.x,p.y,r+6,0,Math.PI*2);ctx.stroke();}
      if (node.id === state.selected || node.id === state.hovered || state.selected || core.visibleLabel(node, state.camera.zoom)) {
        ctx.globalAlpha = Math.min(1, opacity + .2);
        ctx.fillStyle = "#e8f4ff";
        ctx.font = `${node.id === state.selected ? "700 13" : "500 10"}px system-ui`;
        ctx.textAlign = "left";
        const limit=node.id === state.selected ? 30 : 21;
        let text = node.title.length > limit ? `${node.title.slice(0,limit-13)}…${node.title.slice(-12)}` : node.title;
        if(state.selected && node.id!==state.selected){
          const available=Math.max(65,460*state.camera.zoom-20);
          let length=limit;
          while(ctx.measureText(text).width>available && length>8){length--;const tail=Math.min(8,Math.floor(length/2));text=`${node.title.slice(0,length-tail-1)}…${node.title.slice(-tail)}`;}
        }
        const box = {x:p.x+r+5, y:p.y-7, w:ctx.measureText(text).width, h:11};
        if (node.id === state.selected || node.id === state.hovered || !drawnLabels.some(b => box.x < b.x+b.w+3 && box.x+box.w+3 > b.x && box.y < b.y+b.h+1 && box.y+box.h+1 > b.y)) {
          ctx.fillText(text, box.x, p.y+3); drawnLabels.push(box);
        }
      }
    }
    ctx.globalAlpha = 1;
  }

  function drawMini() {
    miniCtx.clearRect(0, 0, mini.width, mini.height);
    miniCtx.fillStyle = "#071427";
    miniCtx.fillRect(0, 0, mini.width, mini.height);
    for (const node of nodes) {
      if (!passes(node)) continue;
      miniCtx.globalAlpha = node.asset_type === "运行记录" ? .22 : .7;
      miniCtx.fillStyle = nodeColor(node);
      miniCtx.fillRect(node.x / 2400 * mini.width, node.y / 1600 * mini.height, 1.5, 1.5);
    }
    miniCtx.globalAlpha = 1;
    const ww = state.camera.width / state.camera.zoom;
    const wh = state.camera.height / state.camera.zoom;
    miniCtx.strokeStyle = "#c2dcff";
    miniCtx.strokeRect((state.camera.x - ww / 2) / 2400 * mini.width, (state.camera.y - wh / 2) / 1600 * mini.height, ww / 2400 * mini.width, wh / 1600 * mini.height);
  }

  function fitFocusedNodes() {
    const visible = nodes.filter(passes);
    if (!visible.length) return;
    const xs = visible.map(n => n.x), ys = [...visible.map(n => n.y),...chainColumns.map(g=>g.y)];
    const left = state.camera.width > 1000 ? 405 : 24;
    const right = state.camera.width > 1000 ? 365 : 24;
    const w = Math.max(180, state.camera.width - left - right);
    const h = Math.max(180, state.camera.height - 150);
    const zoom = core.clamp(Math.min(w / (Math.max(...xs)-Math.min(...xs)+360), h / (Math.max(...ys)-Math.min(...ys)+140)), .18, 1.4);
    state.camera.zoom = zoom;
    state.camera.x = (Math.min(...xs)+Math.max(...xs)+300)/2 - (left-right)/(2*zoom);
    state.camera.y = (Math.min(...ys)+Math.max(...ys))/2;
  }

  function layoutSourceChain() {
    for (const n of nodes) Object.assign(n,basePositions.get(n.id));
    G.chainColumns=[];
    if(state.selected){
      const layout=core.focusLayout(state.selected,nodes.filter(passes));
      layout.positions.forEach(p=>Object.assign(nodeById.get(p.id),{x:p.x,y:p.y}));
      G.chainColumns=layout.groups;
    }
    G.spatialGrid=core.buildSpatialGrid(nodes.filter(passes),48);
  }
  Object.assign(G, { requestRender, resize, updateGraphControls, passesScope, passes, alpha, render, drawGrid, drawOperations, drawClusters, drawEdges, drawNodes, drawMini, fitFocusedNodes, layoutSourceChain });
})();
