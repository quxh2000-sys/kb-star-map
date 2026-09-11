// 观察镜头、图例、空态、筛选、搜索与事件绑定
// 由 star_graph.js 按接缝拆分后改造为独立模块；模块间只通过 globalThis.KBStarGraph 通信。
globalThis.KBStarGraph = globalThis.KBStarGraph || {};
(() => {
  "use strict";
  const G = globalThis.KBStarGraph;
  const { data, core, canvas, ctx, mini, miniCtx, nodes, edges, clusters, nodeById, adjacency, colors, state, basePositions, localManagement, esc, requestRender, resize, updateGraphControls, passesScope, passes, alpha, render, drawGrid, drawOperations, drawClusters, drawEdges, drawNodes, drawMini, fitFocusedNodes, layoutSourceChain, updateRelationView, locateRelatedNode, setRelationView, selectNode, showNode, hideNode, resetView, pointer, copyText, toast, openInObsidian, requestJson, maintenanceTypeFolders, maintenanceSuggestedPath, composeNewNote, resetMaintenancePreview, updateMaintenanceClassificationFields, setMaintenanceMode, setLeftWorkspaceTab, openMaintenance, previewMaintenance, commitMaintenance, updateOperationsPanel, setMode, createOperationTask, addSelectedNodeToOperation, exportOperationTask, postOperation, fileAsBase64, uploadOperationFiles, decomposeOperation, updateOperationCandidate, addOperationRelation, analyzeOperationRequirement, buildOperationPackage, unique } = G;
  const lensDefinitions = {
    type: {
      id: "typeLens",
      filterKey: "types",
      // 与默认选集同源：只列硬编码清单会漏掉自定义/兜底类型，用户既看不到也点不到。
      values: core.effectiveTypeFilters(G.mainline.nodes),
      label: core.displayType,
    },
    industry: {id: "industryLens", filterKey: "industries", values: unique(nodes.flatMap(node => node.industries || [])).sort(), label: value => value},
    evidence: {id: "evidenceLens", filterKey: "evidence", values: unique(nodes.map(node => String(node.evidence_level || ""))).sort(), label: value => value},
    health: {id: "healthLens", filterKey: "health", values: ["healthy", "warning", "blocked"], label: value => ({healthy:"正常",warning:"待治理",blocked:"阻塞"}[value] || value)},
    // 变化时间：选项标签直接带计数，计数即"变化分布"（设计规格 §3.1）
    time: {
      id: "timeLens",
      filterKey: "time",
      values: core.timeBuckets(),
      label: value => {
        // 计数按当前可见集缓存，避免每次标签渲染重算 5 遍
        if (G.__timeCountsFor !== G.mainline) {
          G.__timeCounts = core.timeBucketCounts(G.mainline.nodes, G.timeReference).counts;
          G.__timeCountsFor = G.mainline;
        }
        return `${core.timeBucketLabel(value)} · ${G.__timeCounts[value] || 0}`;
      },
    },
  };

  function renderLens(name) {
    const definition = lensDefinitions[name];
    const details = document.getElementById(definition.id);
    const selected = state.filters[definition.filterKey];
    const options = details.querySelector("[data-lens-options]");
    options.innerHTML = definition.values.map(value => `<label class="lens-option"><input type="checkbox" value="${esc(value)}"${selected.has(value) ? " checked" : ""}><span>${esc(definition.label(value))}</span></label>`).join("");
    const labels = definition.values.filter(value => selected.has(value)).map(definition.label);
    const summary = details.querySelector("[data-lens-summary]");
    const allTypesSelected = definition.filterKey === "types" && core.selectionContainsAll(selected, definition.values);
    summary.textContent = core.selectionSummary(labels, {allSelected: allTypesSelected, emptyMeansAll: definition.filterKey !== "types"});
    summary.title = summary.textContent;
  }

  // ---------- 时间回放控件 ----------
  let playbackTimer = null;
  function stopPlaybackTimer() {
    if (playbackTimer) { clearInterval(playbackTimer); playbackTimer = null; }
  }
  function renderPlayback() {
    const timeline = G.playbackTimeline;
    const range = document.getElementById("playbackRange");
    const label = document.getElementById("playbackCursor");
    const toggle = document.getElementById("playbackToggle");
    range.max = String(Math.max(0, timeline.total - 1));
    range.value = String(state.playback.index);
    range.disabled = timeline.total === 0;
    label.textContent = state.playback.active ? (state.playback.cursor || timeline.min) : "未启用";
    toggle.textContent = state.playback.playing ? "暂停" : "回放";
    toggle.setAttribute("aria-pressed", String(state.playback.playing));
  }
  function setPlaybackIndex(index) {
    const timeline = G.playbackTimeline;
    if (!timeline.total) return;
    state.playback.index = Math.max(0, Math.min(timeline.total - 1, index));
    state.playback.cursor = timeline.dates[state.playback.index];
    state.playback.active = true;
    renderPlayback();
    applyFilters();
  }
  function startPlayback() {
    const timeline = G.playbackTimeline;
    if (!timeline.total) return;
    stopPlaybackTimer();
    if (!state.playback.active || state.playback.index >= timeline.total - 1) setPlaybackIndex(0);
    state.playback.playing = true;
    renderPlayback();
    playbackTimer = setInterval(() => {
      if (state.playback.index >= timeline.total - 1) { stopPlaybackTimer(); state.playback.playing = false; renderPlayback(); return; }
      setPlaybackIndex(state.playback.index + 1);
    }, 90);
  }
  function togglePlayback() {
    if (state.playback.playing) { stopPlaybackTimer(); state.playback.playing = false; renderPlayback(); }
    else startPlayback();
  }
  function clearPlayback() {
    stopPlaybackTimer();
    state.playback = {active: false, playing: false, cursor: "", index: 0};
    renderPlayback();
  }


  // ---------- 分组着色 UI ----------
  const GROUP_PALETTE = ["#ff8a4c", "#f2d24b", "#7ee081", "#4fd1c5", "#8b5cf6", "#ef5b68", "#38bdf8", "#f472b6"];
  function renderGroups() {
    const list = document.getElementById("groupList");
    list.innerHTML = state.groups.length
      ? state.groups.map((group, index) => `<span class="group-chip"><i class="legend-dot" style="background:${esc(group.color)}"></i><span class="group-query">${esc(group.query)}</span><button type="button" data-group-remove="${index}" aria-label="移除分组 ${esc(group.query)}">×</button></span>`).join("")
      : '<small class="group-empty">未定义分组；定义后命中节点改用该色</small>';
    renderLegend();
  }
  function addGroup() {
    const input = document.getElementById("groupQuery");
    const query = input.value.trim();
    if (!query) return;
    state.groups.push({query, color: document.getElementById("groupColor").value});
    input.value = "";
    renderGroups();
    requestRender();
  }

  function renderLegend() {
    const legend = document.getElementById("graphLegend");
    const groupChips = state.groups.length
      ? `<span class="legend-groups">${state.groups.map(group => `<span class="legend-group-chip"><i class="legend-dot" style="background:${esc(group.color)}"></i>${esc(group.query)}</span>`).join("")}</span>`
      : "";
    // 图例以「实际出现的类型」为准，而不是 colors 的键：分类类型可由用户自定义，
    // 不在色彩表里的类型同样要能在图例里开关（作色走 paint 层的灰色回退）。
    legend.innerHTML = core.effectiveTypeFilters(G.mainline.nodes)
      .map(key => {
        const selected = state.filters.types.has(key);
        const label = core.displayType(key);
        const color = colors[key] || "#60748c";
        return `<button type="button" class="legend-item" data-legend-type="${esc(key)}" aria-pressed="${selected}" aria-label="${selected ? "隐藏" : "显示"}${esc(label)}星系"><i class="legend-dot" style="background:${color}"></i>${esc(label)}</button>`;
      })
      .join("") + groupChips;
  }

  function updateEmptyState() {
    const empty = document.getElementById("graphEmpty");
    const hasVisibleNodes = nodes.some(node => passes(node));
    empty.textContent = state.operationalFilters.size ? "当前运营标签没有匹配节点，请取消底栏筛选。" : "当前观察镜头没有匹配节点，请清除筛选。";
    empty.hidden = hasVisibleNodes;
  }

  function renderOperationalRail() {
    const scopedNodes = nodes.filter(passesScope);
    G.currentOperationalMetrics = core.operationalMetrics(scopedNodes, data.graph.healthByNodeId);
    document.getElementById("railCurrent").textContent = G.currentOperationalMetrics.current.toLocaleString("zh-CN");
    document.getElementById("railMissingSource").textContent = G.currentOperationalMetrics.missingSource.toLocaleString("zh-CN");
    document.getElementById("railLowEvidence").textContent = G.currentOperationalMetrics.lowEvidence.toLocaleString("zh-CN");
    document.getElementById("railGovernance").textContent = G.currentOperationalMetrics.governance.toLocaleString("zh-CN");
    document.getElementById("railUpdated").textContent = G.currentOperationalMetrics.latestUpdated;
    document.querySelectorAll("[data-operational-filter]").forEach(button => {
      const filter = button.dataset.operationalFilter;
      const active = filter === "current" ? state.operationalFilters.size === 0 : state.operationalFilters.has(filter);
      const label = button.querySelector("span").textContent;
      button.setAttribute("aria-pressed", String(active));
      button.setAttribute("aria-label", filter === "current" ? "显示全部当前节点" : `${active ? "取消" : "启用"}${label}筛选`);
      button.title = button.getAttribute("aria-label");
    });
  }

  function applyFilters() {
    renderOperationalRail();
    if (state.selected && !passesScope(nodeById.get(state.selected))) selectNode(null);
    updateEmptyState();
    requestRender();
  }

  function revealNode(node) {
    state.layers.nodes = true;
    if (node.dimension === "产品维度") state.layers.products = true;
    if (node.dimension === "规则维度") state.layers.rules = true;
    if (node.asset_type === "运行记录") state.layers.updates = true;
    state.filters.types.add(node.asset_type);
    if (state.filters.industries.size && ![...state.filters.industries].some(value => (node.industries || []).includes(value))) state.filters.industries.clear();
    if (state.filters.evidence.size && !state.filters.evidence.has(String(node.evidence_level || ""))) state.filters.evidence.clear();
    const nodeHealth = data.graph.healthByNodeId?.[node.id] || "healthy";
    if (state.filters.health.size && !state.filters.health.has(nodeHealth)) state.filters.health.clear();
    state.operationalFilters.clear();
    Object.keys(lensDefinitions).forEach(renderLens);
    renderLegend();
    renderOperationalRail();
  }

  function renderSearch() {
    const q = document.getElementById("globalSearch").value.trim().toLowerCase();
    const box = document.getElementById("searchResults");
    if (!q) { box.hidden = true; return; }
    const found = G.mainline.nodes.filter(node => [node.title,node.path,node.summary,...(node.tags||[]),...(node.aliases||[])].join(" ").toLowerCase().includes(q)).slice(0, 12);
    box.innerHTML = found.length ? found.map(node => `<button class="search-result" data-node-id="${node.id}"><strong>${esc(node.title)}</strong><small>${esc(core.displayType(node.asset_type))} · ${esc(node.path)}</small></button>`).join("") : `<div class="search-result">没有匹配节点</div>`;
    box.hidden = false;
  }

  Object.entries(lensDefinitions).forEach(([name, definition]) => {
    renderLens(name);
    const details = document.getElementById(definition.id);
    details.addEventListener("toggle", () => {
      if (details.open) document.querySelectorAll(".lens-multi").forEach(other => { if (other !== details) other.open = false; });
    });
    details.querySelector("[data-lens-options]").addEventListener("change", event => {
      const input = event.target.closest('input[type="checkbox"]');
      if (!input) return;
      const selected = state.filters[definition.filterKey];
      if (input.checked) selected.add(input.value); else selected.delete(input.value);
      renderLens(name);
      if (definition.filterKey === "types") renderLegend();
      applyFilters();
    });
  });

  document.getElementById("globalSearch").addEventListener("input", renderSearch);
  const displaySettings=document.createElement('label');displaySettings.className='background-display-setting';
  displaySettings.innerHTML='<input id="showBackgroundRecords" type="checkbox"><span>显示后台记录</span><small>默认隐藏运行、专题、拆解及索引／汇总／README</small>';
  document.querySelector('#graphLensContent header').after(displaySettings);
  document.getElementById('showBackgroundRecords').addEventListener('change',event=>{
    G.showBackground=event.target.checked;
    G.mainline=core.mainlineGraph(nodes,edges,G.showBackground);
    lensDefinitions.type.values=unique(G.mainline.nodes.map(n=>n.asset_type));
    if(G.showBackground)for(const type of ['运行记录','专题知识','拆解记录'])state.filters.types.add(type);
    renderLens('type');renderLegend();
    const selected=nodeById.get(state.selected);
    if(selected&&(G.showBackground||core.isMainlineNode(selected)))selectNode(selected);else resetView();
    const sourceIndex=document.getElementById('sourceChainIndex');
    if(sourceIndex)sourceIndex.hidden=!G.showBackground;
    renderOperationalRail();renderSearch();updateEmptyState();requestRender();
  });
  const arrowSettings=document.createElement('label');arrowSettings.className='background-display-setting';
  arrowSettings.innerHTML='<input id="showArrows" type="checkbox"><span>方向箭头</span><small>显示链接方向，如「来源 → 原子」</small>';
  displaySettings.after(arrowSettings);
  document.getElementById('showArrows').addEventListener('change',event=>{
    state.showArrows=event.target.checked;
    requestRender();
  });
  const diagnostics=data.graph.sourceDiagnostics;
  if (diagnostics) {
    const entry=document.createElement('details');entry.id='sourceChainIndex';entry.className='source-chain-index';entry.hidden=!G.showBackground;
    entry.innerHTML=`<summary>资料链入口 · ${diagnostics.with_atoms}/${diagnostics.source_count}篇已连到原子</summary><div class="source-chain-filters"><button type="button" data-chain-status="linked">已贯通 ${diagnostics.with_atoms}</button><button type="button" data-chain-status="pending">待核 ${diagnostics.source_count-diagnostics.with_atoms}</button></div><div class="source-chain-items"></div>`;
    document.querySelector('#graphLensContent header').after(entry);
    const renderIndex=status=>{
      entry.querySelectorAll('[data-chain-status]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.chainStatus===status)));
      entry.querySelector('.source-chain-items').innerHTML=diagnostics.chains.filter(c=>status==='linked'?c.atom_count>0:!c.atom_count).map(c=>`<button type="button" data-chain-id="${c.id}"><span>${esc(nodeById.get(c.id)?.title || c.path)}</span><small>${c.atom_count ? `${c.atom_count}篇知识原子` : '尚无可追踪原子，需核对'}</small></button>`).join('');
    };
    renderIndex('linked');
    entry.addEventListener('click',event=>{
      const filter=event.target.closest('[data-chain-status]'); if(filter){renderIndex(filter.dataset.chainStatus);return;}
      const target=event.target.closest('[data-chain-id]');if(target){const n=nodeById.get(target.dataset.chainId);revealNode(n);selectNode(n);entry.open=false;}
    });
  }
  document.getElementById("searchResults").addEventListener("click", event => {
    const button = event.target.closest("[data-node-id]");
    if (!button) return;
    const node = nodeById.get(button.dataset.nodeId);
    revealNode(node);
    selectNode(node);
    document.getElementById("searchResults").hidden = true;
  });
  document.getElementById("clearLenses").addEventListener("click", () => {
    Object.values(state.filters).forEach(selected => selected.clear());
    lensDefinitions.type.values.forEach(value => state.filters.types.add(value));
    clearPlayback();
    Object.keys(lensDefinitions).forEach(renderLens);
    renderLegend();
    applyFilters();
  });
  document.getElementById("graphLegend").addEventListener("click", event => {
    const button = event.target.closest("[data-legend-type]");
    if (!button) return;
    core.toggleSelection(state.filters.types, button.dataset.legendType);
    renderLens("type");
    renderLegend();
    applyFilters();
  });
  document.querySelectorAll("[data-operational-filter]").forEach(button => {
    button.addEventListener("click", () => {
      const filter = button.dataset.operationalFilter;
      if (filter === "current") state.operationalFilters.clear();
      else core.toggleSelection(state.operationalFilters, filter);
      applyFilters();
    });
  });
  // ---------- 版本与更新（仅本地管理模式）----------
  // 分享快照是被转发出去的单文件，绝不能让它在别人机器上触发联网或写入。
  function renderUpdateState(data) {
    const hint = document.getElementById("updateHint");
    if (!hint) return;
    if (data.error) hint.textContent = `检查失败：${data.error}`;
    else if (!data.configured) hint.textContent = "未配置更新源";
    else if (data.update_available) hint.textContent = `v${data.current} → v${data.latest}`;
    else hint.textContent = `v${data.current} 已是最新`;
  }

  async function checkUpdate() {
    const button = document.getElementById("checkUpdate");
    if (button) { button.disabled = true; button.textContent = "检查中…"; }
    try {
      const result = await requestJson("/api/update", {method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({action: "check"})});
      renderUpdateState(result);
      if (!result.configured) { toast(result.message); return; }
      if (!result.update_available) { toast(`已是最新版本 v${result.current}`); return; }
      const lines = String(result.changelog || "").split("\n").slice(0, 6).join("\n");
      if (!window.confirm(`发现新版本 v${result.latest}（当前 v${result.current}）。\n\n${lines}\n\n现在更新？更新前会自动备份当前版本，你的笔记与分类规则不会被改动。`)) return;
      if (button) button.textContent = "更新中…";
      const applied = await requestJson("/api/update", {method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({action: "apply"})});
      window.alert(`${applied.message || "更新完成"}。页面将刷新。`);
      window.location.reload();
    } catch (error) {
      toast(error.message);
    } finally {
      if (button) { button.disabled = false; button.textContent = "检查更新"; }
    }
  }

  document.getElementById("closeNode").addEventListener("click", () => selectNode(null));
  document.querySelectorAll("[data-relation-view]").forEach(button => button.addEventListener("click", () => setRelationView(button.dataset.relationView)));
  document.getElementById("openNode").addEventListener("click", event => openInObsidian(event.currentTarget.dataset.openObsidian).catch(error => toast(error.message)));
  document.getElementById("copyNodePath").addEventListener("click", event => copyText(event.currentTarget.dataset.copyPath).then(() => toast("路径已复制")));
  document.getElementById("editNodeInMaintenance").addEventListener("click", event => openMaintenance("edit", event.currentTarget.dataset.maintenancePath).catch(error => {
    document.getElementById("maintenanceResult").textContent = error.message;
    toast(error.message);
  }));
  document.getElementById("controlsToggle").addEventListener("click", () => {
    state.controlsExpanded = !state.controlsExpanded;
    updateGraphControls();
  });
  document.getElementById("zoomIn").addEventListener("click", () => { state.camera.zoom = core.clamp(state.camera.zoom * 1.25, .18, 3.5); requestRender(); });
  document.getElementById("zoomOut").addEventListener("click", () => { state.camera.zoom = core.clamp(state.camera.zoom * .8, .18, 3.5); requestRender(); });
  document.getElementById("resetGraph").addEventListener("click", resetView);
  if (G.localManagement) {
    // 防御式：模板若缺这两个元素（例如模板与脚本版本不一致），
    // 只跳过更新入口，绝不能让异常打断整个星图初始化。
    const updateState = document.getElementById("updateState");
    const checkUpdateButton = document.getElementById("checkUpdate");
    if (updateState) updateState.hidden = false;
    if (checkUpdateButton) checkUpdateButton.addEventListener("click", () => checkUpdate().catch(error => toast(error.message)));
    requestJson("/api/update", {method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({action: "check"})})
      .then(renderUpdateState)
      .catch(() => { const hint = document.getElementById("updateHint"); if (hint) hint.textContent = "未检查更新"; });
  }
  document.getElementById("graphDepth").addEventListener("change", event => { state.depth = Number(event.target.value); if (state.selected) setRelationView(state.relationView); else requestRender(); });
  document.getElementById("createOperationTask").addEventListener("click", () => createOperationTask().catch(error => toast(error.message)));
  document.getElementById("exportOperationTask").addEventListener("click", () => exportOperationTask().catch(error => toast(error.message)));
  document.getElementById("uploadOperationFiles").addEventListener("click", () => uploadOperationFiles().catch(error => toast(error.message)));
  document.getElementById("decomposeOperation").addEventListener("click", () => decomposeOperation().catch(error => toast(error.message)));
  document.getElementById("candidateList").addEventListener("change", event => {
    const select = event.target.closest("[data-candidate-id]");
    if (select) updateOperationCandidate(select.dataset.candidateId, select.value).catch(error => toast(error.message));
  });
  document.getElementById("addOperationRelation").addEventListener("click", () => addOperationRelation().catch(error => toast(error.message)));
  document.getElementById("analyzeRequirement").addEventListener("click", () => analyzeOperationRequirement().catch(error => toast(error.message)));
  document.getElementById("buildKnowledgePackage").addEventListener("click", () => buildOperationPackage().catch(error => toast(error.message)));
  document.getElementById("leftTabGraph").addEventListener("click", () => setLeftWorkspaceTab("graph"));
  document.getElementById("leftTabMaintenance").disabled = !localManagement;
  document.getElementById("leftTabMaintenance").addEventListener("click", () => {
    const selected = nodeById.get(state.selected);
    openMaintenance(selected ? "edit" : "create", selected?.path || "").catch(error => toast(error.message));
  });
  document.querySelectorAll("[data-maintenance-mode]").forEach(button => button.addEventListener("click", () => {
    const mode = button.dataset.maintenanceMode;
    const selected = nodeById.get(state.selected);
    openMaintenance(mode, mode === "edit" ? selected?.path || "" : "").catch(error => toast(error.message));
  }));
  ["maintenanceNewTitle", "maintenanceNewType", "maintenanceNewDimension", "maintenanceNewSubDimension"].forEach(id => document.getElementById(id).addEventListener("input", () => {
    updateMaintenanceClassificationFields();
    document.getElementById("maintenanceNewPath").value = maintenanceSuggestedPath();
    resetMaintenancePreview();
  }));
  ["maintenanceEditContent", "maintenanceNewPath", "maintenanceNewSource", "maintenanceNewEvidence", "maintenanceNewUseFor", "maintenanceNewContent"].forEach(id => document.getElementById(id).addEventListener("input", resetMaintenancePreview));
  document.getElementById("previewMaintenance").addEventListener("click", () => previewMaintenance().catch(error => {
    document.getElementById("maintenanceResult").textContent = error.message;
    toast(error.message);
  }));
  document.getElementById("commitMaintenance").addEventListener("click", () => commitMaintenance().catch(error => {
    document.getElementById("maintenanceResult").textContent = error.message;
    toast(error.message);
  }));
  window.addEventListener("resize", resize);
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      document.getElementById("searchResults").hidden = true;
      document.querySelectorAll(".lens-multi").forEach(details => details.open = false);
      if (state.selected) selectNode(null);
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      document.getElementById("globalSearch").focus();
    }
  });

  document.getElementById("snapshotTime").textContent = new Date(data.meta.generated_at).toLocaleString("zh-CN");
  document.getElementById("modeState").textContent = localManagement ? "本地管理" : "分享快照";
  document.getElementById("modeBanner").hidden = localManagement;
  document.getElementById("staticFallback").hidden = true;
  renderOperationalRail();
  renderLegend();
  updateOperationsPanel();
  updateEmptyState();
  resize();
  resetView();
  document.getElementById("playbackToggle").addEventListener("click", togglePlayback);
  document.getElementById("playbackRange").addEventListener("input", event => {
    stopPlaybackTimer();
    state.playback.playing = false;
    setPlaybackIndex(Number(event.target.value));
  });
  renderPlayback();
  document.getElementById("groupColor").innerHTML = GROUP_PALETTE.map(color => `<option value="${color}">${color}</option>`).join("");
  document.getElementById("groupAdd").addEventListener("click", addGroup);
  document.getElementById("groupQuery").addEventListener("keydown", event => { if (event.key === "Enter") addGroup(); });
  document.getElementById("groupList").addEventListener("click", event => {
    const button = event.target.closest("[data-group-remove]");
    if (!button) return;
    state.groups.splice(Number(button.dataset.groupRemove), 1);
    renderGroups();
    requestRender();
  });
  renderGroups();
  Object.assign(G, { lensDefinitions, renderLens, renderLegend, updateEmptyState, renderOperationalRail, applyFilters, revealNode, renderSearch, displaySettings, diagnostics, togglePlayback, clearPlayback, setPlaybackIndex, renderGroups, addGroup, checkUpdate, renderUpdateState });
})();
