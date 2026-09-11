// 运营任务面板：任务、候选、关系、需求召回、知识包
// 由 star_graph.js 按接缝拆分后改造为独立模块；模块间只通过 globalThis.KBStarGraph 通信。
globalThis.KBStarGraph = globalThis.KBStarGraph || {};
(() => {
  "use strict";
  const G = globalThis.KBStarGraph;
  const { data, core, canvas, ctx, mini, miniCtx, nodes, edges, clusters, nodeById, adjacency, colors, state, basePositions, localManagement, esc, requestRender, resize, updateGraphControls, passesScope, passes, alpha, render, drawGrid, drawOperations, drawClusters, drawEdges, drawNodes, drawMini, fitFocusedNodes, layoutSourceChain, updateRelationView, locateRelatedNode, setRelationView, selectNode, showNode, hideNode, resetView, pointer, copyText, toast, openInObsidian, requestJson, maintenanceTypeFolders, maintenanceSuggestedPath, composeNewNote, resetMaintenancePreview, updateMaintenanceClassificationFields, setMaintenanceMode, setLeftWorkspaceTab, openMaintenance, previewMaintenance, commitMaintenance } = G;
  function updateOperationsPanel() {
    const task = state.operationTask;
    const status = document.getElementById("operationStatus");
    const sourceBox = document.getElementById("operationSources");
    const exportButton = document.getElementById("exportOperationTask");
    document.getElementById("operationTitle").value = task?.title || "";
    status.textContent = task ? `${task.status === "draft" ? "草稿" : task.status} · ${task.stage}` : localManagement ? "尚未创建任务草稿" : "分享模式仅查看阶段结构";
    sourceBox.innerHTML = task ? `<strong>${esc(core.operationSourceSummary(task))}</strong>${(task.sources || []).slice(-4).map(source => `<small>${esc(source.title || source.original_name || source.path)}</small>`).join("")}` : "创建任务后可加入Vault来源笔记";
    exportButton.disabled = !task;
    const candidates = task?.candidates || [];
    document.getElementById("candidateList").innerHTML = candidates.length ? candidates.slice(0, 20).map(candidate => `<div class="candidate-card"><strong>${esc(candidate.title)}</strong><small>${esc(candidate.asset_type)} · ${esc(candidate.excerpt)}</small><select data-candidate-id="${candidate.id}" aria-label="${esc(candidate.title)}处置"><option value="new"${candidate.disposition === "new" ? " selected" : ""}>新建</option><option value="merge"${candidate.disposition === "merge" ? " selected" : ""}>合并</option><option value="update"${candidate.disposition === "update" ? " selected" : ""}>更新</option><option value="conflict"${candidate.disposition === "conflict" ? " selected" : ""}>冲突</option><option value="ignore"${candidate.disposition === "ignore" ? " selected" : ""}>忽略</option></select></div>`).join("") : "尚未生成候选";
    const relationNodes = [];
    for (const source of task?.sources || []) relationNodes.push({id: source.source_id || source.node_id, title: source.title || source.original_name});
    for (const candidate of candidates) relationNodes.push({id: candidate.id, title: candidate.title});
    for (const retrieval of task?.retrievals || []) relationNodes.push({id: retrieval.node_id, title: retrieval.title});
    const uniqueRelationNodes = [...new Map(relationNodes.filter(item => item.id).map(item => [item.id, item])).values()];
    const relationOptions = uniqueRelationNodes.map(item => `<option value="${esc(item.id)}">${esc(core.compactLabel(item.title, 32))}</option>`).join("");
    document.getElementById("relationSource").innerHTML = relationOptions;
    document.getElementById("relationTarget").innerHTML = relationOptions;
    const requirements = task?.requirements || [];
    const retrievals = task?.retrievals || [];
    document.getElementById("requirementResults").innerHTML = requirements.length ? `<div class="result-chip">事实/目标/约束/待确认：${requirements.length}项</div><div class="result-chip">召回知识：${retrievals.length}项</div>${retrievals.slice(0, 5).map(item => `<div class="result-chip" title="${esc(item.title)}">${esc(core.compactLabel(item.title, 32))} · ${Math.round(item.score * 100)}%</div>`).join("")}` : "尚未分析需求";
    const knowledgePackage = task?.knowledge_package;
    document.getElementById("packageStatus").innerHTML = knowledgePackage ? `<div class="result-chip">知识包草稿已生成</div><div class="result-chip">候选${knowledgePackage.candidates.length} · 召回${knowledgePackage.retrievals.length} · 缺口${knowledgePackage.gaps.length}</div>${knowledgePackage.warnings.map(item => `<div class="result-chip">${esc(item)}</div>`).join("")}` : "尚未生成知识包";
  }

  async function setMode(mode) {
    state.mode = mode;
    document.body.classList.toggle("operation-mode", mode === "operations");
    document.querySelectorAll("[data-mode]").forEach(button => {
      const active = button.dataset.mode === mode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    // 该面板是早期运营模式的遗留，现模板里已无此元素；加空值保护避免整页中断。
    const operationsPanel = document.getElementById("operationsPanel");
    if (operationsPanel) operationsPanel.hidden = mode !== "operations";
    document.getElementById("lensPanel").hidden = mode === "operations";
    if (mode === "operations") {
      selectNode(null);
      if (localManagement) {
        try {
          state.operationTask = (await requestJson("/api/operations/current")).task;
        } catch (error) {
          toast(error.message);
        }
      }
    } else {
      document.getElementById("lensPanel").hidden = false;
    }
    document.getElementById("modeState").textContent = mode === "operations" ? state.operationTask ? "运营草稿" : "运营待创建" : localManagement ? "本地管理" : "分享快照";
    updateOperationsPanel();
    resize();
  }

  async function createOperationTask() {
    if (!localManagement) {
      toast("请使用本地管理模式创建运营任务");
      return;
    }
    const title = document.getElementById("operationTitle").value.trim();
    const result = await requestJson("/api/operations/tasks", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({title}),
    });
    state.operationTask = result.task;
    document.getElementById("operationTitle").value = state.operationTask.title;
    document.getElementById("modeState").textContent = "运营草稿";
    updateOperationsPanel();
    if (state.selected) showNode(nodeById.get(state.selected));
    requestRender();
    toast("运营任务草稿已创建");
  }

  async function addSelectedNodeToOperation() {
    if (!state.operationTask) throw new Error("请先创建运营任务草稿");
    const node = nodeById.get(state.selected);
    if (!node) throw new Error("请先选择一个知识节点");
    const result = await requestJson(`/api/operations/tasks/${state.operationTask.id}/sources`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({path: node.path}),
    });
    state.operationTask = result.task;
    updateOperationsPanel();
    requestRender();
    toast(result.added ? "已加入运营任务" : "该来源已在任务中");
  }

  async function exportOperationTask() {
    if (!state.operationTask) throw new Error("尚未创建运营任务草稿");
    const link = document.createElement("a");
    link.href = `/api/operations/tasks/${state.operationTask.id}/export`;
    link.download = `${state.operationTask.title}-${state.operationTask.id.slice(0, 8)}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  async function postOperation(action, payload = {}) {
    if (!state.operationTask) throw new Error("请先创建运营任务草稿");
    const result = await requestJson(`/api/operations/tasks/${state.operationTask.id}/${action}`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify(payload),
    });
    state.operationTask = result.task;
    updateOperationsPanel();
    document.getElementById("modeState").textContent = "运营草稿";
    requestRender();
    return result;
  }

  function fileAsBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error(`无法读取资料：${file.name}`));
      reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
      reader.readAsDataURL(file);
    });
  }

  async function uploadOperationFiles() {
    const files = [...document.getElementById("operationFiles").files];
    if (!files.length) throw new Error("请选择需要解析的资料");
    for (const file of files) {
      await postOperation("files", {filename: file.name, content_base64: await fileAsBase64(file)});
    }
    document.getElementById("operationFiles").value = "";
    toast(`已解析${files.length}份资料`);
  }

  async function decomposeOperation() {
    await postOperation("decompose");
    toast("分解查重候选已生成");
  }

  async function updateOperationCandidate(candidateId, disposition) {
    await postOperation("candidates", {candidate_id: candidateId, patch: {disposition}});
    toast("候选处置已保存");
  }

  async function addOperationRelation() {
    await postOperation("relations", {
      source_id: document.getElementById("relationSource").value,
      target_id: document.getElementById("relationTarget").value,
      relation_type: document.getElementById("relationType").value,
      rationale: document.getElementById("relationRationale").value,
    });
    document.getElementById("relationRationale").value = "";
    toast("候选关系已保存");
  }

  async function analyzeOperationRequirement() {
    await postOperation("requirements", {text: document.getElementById("requirementText").value});
    toast("需求理解与知识召回已完成");
  }

  async function buildOperationPackage() {
    await postOperation("package");
    toast("售前任务知识包草稿已生成");
  }

  function unique(values) {
    return [...new Set(values.filter(Boolean))];
  }
  Object.assign(G, { updateOperationsPanel, setMode, createOperationTask, addSelectedNodeToOperation, exportOperationTask, postOperation, fileAsBase64, uploadOperationFiles, decomposeOperation, updateOperationCandidate, addOperationRelation, analyzeOperationRequirement, buildOperationPackage, unique });
})();
