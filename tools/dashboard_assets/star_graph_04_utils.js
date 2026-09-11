// 通用工具：复制、提示条、打开笔记、请求封装
// 由 star_graph.js 按接缝拆分后改造为独立模块；模块间只通过 globalThis.KBStarGraph 通信。
globalThis.KBStarGraph = globalThis.KBStarGraph || {};
(() => {
  "use strict";
  const G = globalThis.KBStarGraph;
  const { data, core, canvas, ctx, mini, miniCtx, nodes, edges, clusters, nodeById, adjacency, colors, state, basePositions, localManagement, esc, requestRender, resize, updateGraphControls, passesScope, passes, alpha, render, drawGrid, drawOperations, drawClusters, drawEdges, drawNodes, drawMini, fitFocusedNodes, layoutSourceChain, updateRelationView, locateRelatedNode, setRelationView, selectNode, showNode, hideNode, resetView, pointer } = G;
  async function copyText(value) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(value);
    const input = document.createElement("textarea");
    input.value = value;
    document.body.appendChild(input);
    input.select();
    document.execCommand("copy");
    input.remove();
  }

  function toast(message) {
    const el = document.getElementById("toast");
    el.textContent = message;
    el.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => el.hidden = true, 3200);
  }

  async function openInObsidian(path) {
    if (!localManagement) {
      await copyText(path);
      toast("分享模式已复制路径；请使用本地管理模式打开笔记");
      return;
    }
    const response = await fetch("/api/open-note", {method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({path})});
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.message || "无法打开笔记");
    toast(result.openedWith === "obsidian" ? "已在Obsidian中打开对应笔记" : "已用系统默认程序打开该笔记");
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, options);
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.message || "运营任务请求失败");
    return result;
  }
  Object.assign(G, { copyText, toast, openInObsidian, requestJson });
})();
