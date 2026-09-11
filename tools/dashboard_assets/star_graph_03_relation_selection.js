// 关系视图、节点选中与详情、重置与指针交互
// 由 star_graph.js 按接缝拆分后改造为独立模块；模块间只通过 globalThis.KBStarGraph 通信。
globalThis.KBStarGraph = globalThis.KBStarGraph || {};
(() => {
  "use strict";
  const G = globalThis.KBStarGraph;
  const { data, core, canvas, ctx, mini, miniCtx, nodes, edges, clusters, nodeById, adjacency, colors, state, basePositions, localManagement, esc, requestRender, resize, updateGraphControls, passesScope, passes, alpha, render, drawGrid, drawOperations, drawClusters, drawEdges, drawNodes, drawMini, fitFocusedNodes, layoutSourceChain } = G;
  function updateRelationView(node) {
    state.located=null;
    const related=G.mainline.edges.filter(e=>state.localSet?.has(e.source)&&state.localSet?.has(e.target));
    const direct=new Set(related.filter(e=>!e.projected&&(e.source===node.id||e.target===node.id)).map(e=>e.source===node.id?e.target:e.source));
    const visible=nodes.filter(passes);
    document.getElementById("relationViewSummary").textContent=`${G.showBackground?'全部':'主线'}关联笔记 ${visible.length-1} 篇 · 直接关联 ${direct.size} 篇 · 路径关系 ${related.length} 条`;
    let list=document.getElementById("relationItems");
    if(!list){list=document.createElement("div");list.id="relationItems";document.getElementById("relationViewSummary").after(list);}
    const names={decomposes_to:"拆解 / 来源",supports:"支撑",validates:"案例验证",applies_to:"适用",governs:"约束",data_output:"数据输出",data_input:"数据输入",wikilink:"笔记链接"};
    const coverage=core.dimensionCoverage(visible,node.dimension_review||{});
    const statusNames={not_in_source:"资料未涉及",pending:"待拆解",unreviewed:"尚未核定"};
    list.innerHTML=`<div class="dimension-coverage" aria-label="九维关联覆盖">${coverage.map(c=>`<span class="${c.count?'covered':''}">${esc(c.dimension.replace('维度',''))} ${c.count||statusNames[c.status]||'尚未核定'}</span>`).join('')}</div><p class="relation-hint">画布单击定位右侧条目，双击或点击条目切换笔记。普通链接仅表示引用。</p><p id="relationLocateStatus" class="relation-locate-status" role="status" aria-live="polite"></p>`;
    for(const group of G.chainColumns){
      list.insertAdjacentHTML('beforeend',`<details class="relation-group"><summary>${esc(group.title)} · ${group.count}</summary>${group.members.map(other=>{
        const incident=related.filter(e=>(e.source===node.id&&e.target===other.id)||(e.target===node.id&&e.source===other.id));
        const descriptors=incident.map(e=>`${e.source===node.id?'本笔记 →':'→ 本笔记'} ${e.projected?'经后台记录关联':names[e.relation]||e.relation}`);
        return `<button type="button" data-related-id="${esc(other.id)}" title="${esc(other.path)}"><span>${esc(other.title)}</span><small class="located-label" hidden>已定位 · 点击查看</small><small>${esc(descriptors.join('；')||'间接关联 · 资料路径')}${other.asset_type==='知识原子'&&!other.dimension?' · 维度待核':''}</small></button>`;
      }).join('')}</details>`);
    }
    const paths=new Set(visible.map(n=>n.path));
    const issues=(data.graph.relationshipDiagnostics?.issues||data.graph.sourceDiagnostics?.unresolved||[]).filter(i=>paths.has(i.path));
    if(issues.length)list.insertAdjacentHTML('beforeend',`<details class="relation-issues"><summary>待核关系 ${issues.length} 条</summary>${issues.map(i=>`<p>${esc(i.path.split('/').pop())}：${esc(i.reference||'')} — ${esc(i.reason)}</p>`).join('')}</details>`);
    if(!related.length)list.insertAdjacentHTML('beforeend','<p>尚无可解析关联；需核对来源及笔记链接。</p>');
    list.scrollTop=0;
    list.onclick=event=>{const b=event.target.closest('[data-related-id]');if(b)selectNode(nodeById.get(b.dataset.relatedId));};
  }

  function locateRelatedNode(node) {
    const list=document.getElementById('relationItems');
    const item=list?.querySelector(`[data-related-id="${node.id}"]`);
    if(!item) return;
    state.located=node.id;
    list.querySelectorAll('[data-related-id]').forEach(button=>{
      const located=button===item;
      button.classList.toggle('is-located',located);
      button.querySelector('.located-label').hidden=!located;
    });
    item.closest('.relation-group').open=true;
    document.getElementById('relationLocateStatus').textContent=`已定位：${node.title}；当前仍在查看 ${nodeById.get(state.selected).title}`;
    const behavior=matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth';
    item.scrollIntoView({block:'nearest',inline:'nearest',behavior});
    item.focus({preventScroll:true});
    requestRender();
  }

  function setRelationView() {
    const node=nodeById.get(state.selected);
    state.relationView="all";
    if(!node){requestRender();return;}
    state.localSet=core.focusNeighborhood(node.id,G.mainline.edges,G.mainline.nodes,state.depth);
    layoutSourceChain();updateRelationView(node);fitFocusedNodes();requestRender();
  }

  function selectNode(node) {
    state.located=null;
    state.selected = node?.id || null;
    if (node) {
      state.relationView = "all";
      state.localSet = core.focusNeighborhood(node.id,G.mainline.edges,G.mainline.nodes,state.depth);
      layoutSourceChain();
      state.camera.x = node.x;
      state.camera.y = node.y;
      state.camera.zoom = Math.max(state.camera.zoom, .9);
      showNode(node);
      fitFocusedNodes();
      G.renderOperationalRail();
    } else {
      state.relationView = "all";
      state.localSet = null;
      layoutSourceChain();
      resetView();
    }
    document.getElementById("resetGraph").hidden = !node;
    requestRender();
  }

  function showNode(node) {
    document.querySelector('#graphLensContent header p').textContent='已锁定笔记：暂不按分类隐藏关联';
    document.body.classList.add("node-focus");
    document.getElementById("nodePanel").hidden = false;
    state.nodeOpen = true;
    state.controlsExpanded = false;
    updateGraphControls();
    document.getElementById("nodeType").textContent = core.displayType(node.asset_type);
    document.getElementById("nodeTitle").textContent = node.title;
    document.getElementById("nodeSummary").textContent = node.summary || "该节点没有可用摘要。";
    updateRelationView(node);
    document.getElementById("nodeMeta").innerHTML = [["状态",node.status],["更新时间",node.modified],["主维度",node.dimension||"—"],["二级分类",node.sub_dimension||"—"],["行业",(node.industries||[]).join("、")||"—"],["证据等级",node.evidence_level||"—"],["连接数",node.degree],["源路径",node.path]].map(([key,value]) => `<div class="node-row"><span>${key}</span><span>${esc(value)}</span></div>`).join("");
    document.getElementById('relationViewSummary').before(document.getElementById('nodeMeta'),document.querySelector('#nodePanel .node-actions'));
    document.getElementById('nodePanel').scrollTop=0;
    document.getElementById("openNode").dataset.openObsidian = node.path;
    document.getElementById("copyNodePath").dataset.copyPath = node.path;
    const editButton = document.getElementById("editNodeInMaintenance");
    editButton.hidden = !localManagement;
    editButton.dataset.maintenancePath = node.path;
  }

  function hideNode() {
    document.querySelector('#graphLensContent header p').textContent='可多选，筛选直接作用于全库星图';
    document.querySelectorAll("[data-global-relation-view]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.globalRelationView===state.relationView)));
    document.body.classList.remove("node-focus");
    document.getElementById("nodePanel").hidden = true;
    state.nodeOpen = false;
    updateGraphControls();
  }

  function resetView() {
    state.located=null;
    for (const n of nodes) Object.assign(n,basePositions.get(n.id));
    G.spatialGrid=core.buildSpatialGrid(G.mainline.nodes,48);
    state.camera.x = 1200;
    state.camera.y = 800;
    state.camera.zoom = Math.max(.18, Math.min(state.camera.width / 2500, state.camera.height / 1700));
    state.selected = null;
    state.relationView = "all";
    state.localSet = null;
    hideNode();
    document.getElementById("resetGraph").hidden = true;
    requestRender();
  }

  function pointer(event) {
    const rect = canvas.getBoundingClientRect();
    return {x: event.clientX - rect.left, y: event.clientY - rect.top};
  }

  canvas.addEventListener("pointerdown", event => {
    if (state.mode === "operations") return;
    state.dragging = true;
    state.moved = false;
    state.last = pointer(event);
    canvas.setPointerCapture(event.pointerId);
    canvas.classList.add("dragging");
  });
  canvas.addEventListener("pointermove", event => {
    if (state.mode === "operations") return;
    const p = pointer(event);
    if (state.dragging) {
      const dx = p.x - state.last.x;
      const dy = p.y - state.last.y;
      if (Math.abs(dx) + Math.abs(dy) > 2) state.moved = true;
      state.camera.x -= dx / state.camera.zoom;
      state.camera.y -= dy / state.camera.zoom;
      state.last = p;
      requestRender();
      return;
    }
    const candidate = core.hitTest(G.spatialGrid, core.screenToWorld(p, state.camera), 8 / state.camera.zoom);
    const hit = passes(candidate) ? candidate : null;
    if (hit?.id !== state.hovered) { state.hovered = hit?.id || null; requestRender(); }
  });
  canvas.addEventListener("pointerup", event => {
    if (state.mode === "operations") return;
    canvas.releasePointerCapture(event.pointerId);
    canvas.classList.remove("dragging");
    if (!state.moved) {
      const candidate = core.hitTest(G.spatialGrid, core.screenToWorld(pointer(event), state.camera), 8 / state.camera.zoom);
      if (passes(candidate)) {
        const action=core.canvasNoteAction(state.selected,candidate.id);
        if(action==='locate')locateRelatedNode(candidate);
        else if(action==='select')selectNode(candidate);
        else if(action==='current')document.getElementById('nodePanel').scrollTo({top:0,behavior:'auto'});
      }
    }
    state.dragging = false;
  });
  canvas.addEventListener("wheel", event => {
    if (state.mode === "operations") return;
    event.preventDefault();
    const p = pointer(event);
    const before = core.screenToWorld(p, state.camera);
    state.camera.zoom = core.clamp(state.camera.zoom * (event.deltaY < 0 ? 1.12 : .89), .18, 3.5);
    const after = core.screenToWorld(p, state.camera);
    state.camera.x += before.x - after.x;
    state.camera.y += before.y - after.y;
    requestRender();
  }, {passive: false});
  canvas.addEventListener("dblclick", event => {
    if (state.mode === "operations") return;
    const candidate = core.hitTest(G.spatialGrid, core.screenToWorld(pointer(event), state.camera), 8 / state.camera.zoom);
    if (passes(candidate) && core.canvasNoteAction(state.selected,candidate.id,true)==='select') selectNode(candidate); else resetView();
  });
  Object.assign(G, { updateRelationView, locateRelatedNode, setRelationView, selectNode, showNode, hideNode, resetView, pointer });
})();
