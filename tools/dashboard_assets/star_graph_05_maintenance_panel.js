// 知识维护面板：调改/新增、差异预览、确认写入
// 由 star_graph.js 按接缝拆分后改造为独立模块；模块间只通过 globalThis.KBStarGraph 通信。
globalThis.KBStarGraph = globalThis.KBStarGraph || {};
(() => {
  "use strict";
  const G = globalThis.KBStarGraph;
  const { data, core, canvas, ctx, mini, miniCtx, nodes, edges, clusters, nodeById, adjacency, colors, state, basePositions, localManagement, esc, requestRender, resize, updateGraphControls, passesScope, passes, alpha, render, drawGrid, drawOperations, drawClusters, drawEdges, drawNodes, drawMini, fitFocusedNodes, layoutSourceChain, updateRelationView, locateRelatedNode, setRelationView, selectNode, showNode, hideNode, resetView, pointer, copyText, toast, openInObsidian, requestJson } = G;
  const maintenanceTypeFolders = {
    "来源资料": "知识库/00_原始资料库/维护新增",
    "拆解记录": "知识库/03_存量方案拆解区/维护新增",
    "知识原子": "知识库/01_原子知识组件库/99_待归类",
    "专题知识": "知识库/96_专题综述",
    "需求记录": "知识库/05_客户需求库",
    "方案成果": "知识库/解决方案",
    "验证反馈": "知识库/04_需求与规则引擎/B流程反哺",
    "治理规则": "知识库/04_需求与规则引擎/维护新增",
  };

  function maintenanceSuggestedPath() {
    const type = document.getElementById("maintenanceNewType").value;
    const dimension = document.getElementById("maintenanceNewDimension").value;
    const title = document.getElementById("maintenanceNewTitle").value.trim();
    const safeTitle = title.replace(/[\\/:*?"<>|]+/g, "-").replace(/^[ .-]+|[ .-]+$/g, "");
    const folder = type === "知识原子" && dimension === "产品维度"
      ? "知识库/01_原子知识组件库/98_产品与工具"
      : maintenanceTypeFolders[type] || "收件箱/知识维护新增";
    return safeTitle ? `${folder}/${safeTitle}.md` : "";
  }

  function composeNewNote() {
    const title = document.getElementById("maintenanceNewTitle").value.trim();
    const type = document.getElementById("maintenanceNewType").value;
    const dimension = document.getElementById("maintenanceNewDimension").value;
    const subDimension = document.getElementById("maintenanceNewSubDimension").value.trim();
    const source = document.getElementById("maintenanceNewSource").value.trim();
    const evidence = document.getElementById("maintenanceNewEvidence").value;
    const useFor = document.getElementById("maintenanceNewUseFor").value.trim();
    const body = document.getElementById("maintenanceNewContent").value.trim();
    if (type === "知识原子" && (!dimension || !subDimension)) throw new Error("知识原子必须选择主维度并填写二级分类");
    const spec = Object.values(data.taxonomy?.atomic_dimensions || {}).find(item => item.label === dimension);
    if (type === "知识原子" && spec && !spec.sub_dimensions.includes(subDimension)) throw new Error("二级分类须从当前主维度的标准词典中选择");
    const lines = ["---", `title: ${JSON.stringify(title)}`, `knowledge_type: ${JSON.stringify(type)}`, "status: Active"];
    if (type === "知识原子") lines.push(`dimension: ${JSON.stringify(dimension)}`, `sub_dimension: ${JSON.stringify(subDimension)}`);
    if (source) lines.push("source_ref:", `  - ${JSON.stringify(source)}`);
    if (evidence) lines.push(`evidence_level: ${JSON.stringify(evidence)}`);
    if (useFor) lines.push(`use_for: ${JSON.stringify(useFor)}`);
    lines.push("---", "", `# ${title}`, "", body, "");
    return lines.join("\n");
  }

  function resetMaintenancePreview() {
    state.maintenance.preview = null;
    document.getElementById("maintenanceDiff").textContent = "尚未生成差异";
    document.getElementById("commitMaintenance").disabled = true;
  }

  function updateMaintenanceClassificationFields() {
    const atomic = document.getElementById("maintenanceNewType").value === "知识原子";
    const input = document.getElementById("maintenanceNewSubDimension");
    let suggestions = document.getElementById("subDimensionSuggestions");
    if (!suggestions) {
      suggestions = document.createElement("datalist"); suggestions.id = "subDimensionSuggestions";
      input.after(suggestions); input.setAttribute("list", suggestions.id);
    }
    const spec = Object.values(data.taxonomy?.atomic_dimensions || {}).find(item => item.label === document.getElementById("maintenanceNewDimension").value);
    suggestions.innerHTML = (spec?.sub_dimensions || []).map(value => `<option value="${esc(value)}"></option>`).join("");
    document.getElementById("maintenanceNewDimension").disabled = !atomic;
    document.getElementById("maintenanceNewSubDimension").disabled = !atomic;
    if (!atomic) {
      document.getElementById("maintenanceNewDimension").value = "";
      document.getElementById("maintenanceNewSubDimension").value = "";
    }
  }

  function setMaintenanceMode(mode) {
    state.maintenance.mode = mode;
    document.querySelectorAll("[data-maintenance-mode]").forEach(button => button.classList.toggle("active", button.dataset.maintenanceMode === mode));
    document.getElementById("maintenanceEditSection").hidden = mode !== "edit";
    document.getElementById("maintenanceCreateSection").hidden = mode !== "create";
    resetMaintenancePreview();
  }

  function setLeftWorkspaceTab(tabName) {
    const graphActive = tabName === "graph";
    document.getElementById("lensPanel").hidden = !graphActive;
    document.getElementById("maintenancePanel").hidden = graphActive;
    document.getElementById("leftTabGraph").classList.toggle("active", graphActive);
    document.getElementById("leftTabGraph").setAttribute("aria-selected", String(graphActive));
    document.getElementById("leftTabMaintenance").classList.toggle("active", !graphActive);
    document.getElementById("leftTabMaintenance").setAttribute("aria-selected", String(!graphActive));
  }

  async function openMaintenance(mode, path = "") {
    if (!localManagement) return;
    setLeftWorkspaceTab("maintenance");
    document.getElementById("nodePanel").hidden = true;
    setMaintenanceMode(mode);
    if (!state.operationTask) {
      try {
        state.operationTask = (await requestJson("/api/operations/current")).task;
      } catch (_) {}
      updateOperationsPanel();
    }
    if (mode === "edit") {
      if (!path) {
        document.getElementById("maintenanceResult").textContent = "请先在星图中选择需要调改的笔记";
        return;
      }
      const result = await requestJson(`/api/maintenance/note?path=${encodeURIComponent(path)}`);
      state.maintenance.note = result.note;
      document.getElementById("maintenanceEditPath").value = result.note.path;
      document.getElementById("maintenanceEditContent").value = result.note.content;
      document.getElementById("maintenanceResult").textContent = "已加载目标笔记，修改后生成差异";
    } else {
      updateMaintenanceClassificationFields();
      document.getElementById("maintenanceNewPath").value = maintenanceSuggestedPath();
      document.getElementById("maintenanceResult").textContent = "填写新增笔记后生成差异";
    }
  }

  async function previewMaintenance() {
    const mode = state.maintenance.mode;
    const payload = mode === "edit" ? {
      mode,
      path: state.maintenance.note?.path || "",
      content: document.getElementById("maintenanceEditContent").value,
      baseline_sha256: state.maintenance.note?.sha256 || "",
    } : {
      mode,
      path: document.getElementById("maintenanceNewPath").value.trim(),
      title: document.getElementById("maintenanceNewTitle").value.trim(),
      asset_type: document.getElementById("maintenanceNewType").value,
      content: composeNewNote(),
    };
    const result = await requestJson("/api/maintenance/preview", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify(payload),
    });
    state.maintenance.preview = result.preview;
    if (mode === "create") document.getElementById("maintenanceNewPath").value = result.preview.path;
    document.getElementById("maintenanceDiff").textContent = result.preview.diff || "内容没有变化";
    document.getElementById("commitMaintenance").disabled = !result.preview.diff;
    document.getElementById("maintenanceResult").textContent = result.preview.diff ? "差异已生成，请确认后写入" : "内容没有变化";
  }

  async function commitMaintenance() {
    const preview = state.maintenance.preview;
    if (!preview) throw new Error("请先生成差异预览");
    const result = await requestJson("/api/maintenance/commit", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({preview, token: preview.token, confirm: true}),
    });
    document.getElementById("maintenanceResult").textContent = `已写入并读回验证：${result.result.path}`;
    state.maintenance.preview = null;
    document.getElementById("commitMaintenance").disabled = true;
    toast("笔记已写入并验证");
    setTimeout(() => location.reload(), 700);
  }
  Object.assign(G, { maintenanceTypeFolders, maintenanceSuggestedPath, composeNewNote, resetMaintenancePreview, updateMaintenanceClassificationFields, setMaintenanceMode, setLeftWorkspaceTab, openMaintenance, previewMaintenance, commitMaintenance });
})();
