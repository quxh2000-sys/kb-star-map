// 设置面板：更新、外观（浅色/暗黑/随系统）、笔记、接入智能体。
// 由 star_graph.js 按接缝拆分后改造为独立模块；模块间只通过 globalThis.KBStarGraph 通信。
// 依赖 07 模块已绑定 #checkUpdate；本模块在 07 之后执行。
globalThis.KBStarGraph = globalThis.KBStarGraph || {};
(() => {
  "use strict";
  const G = globalThis.KBStarGraph;
  const runtime = globalThis.__KB_RUNTIME__ || {};
  const serverSettings = Object.assign(
    {note_open_mode: "auto", agent_write: true, show_background: false},
    runtime.settings || {},
  );
  const THEME_KEY = "kb-star-graph-theme";
  const THEMES = ["light", "dark", "auto"];

  const $ = id => document.getElementById(id);

  // ---------- 外观 ----------
  function readTheme() {
    try {
      const saved = localStorage.getItem(THEME_KEY);
      return THEMES.includes(saved) ? saved : "auto";
    } catch (error) {
      return "auto";
    }
  }

  // 「随系统」要解析成实际明暗，因为画布是 JS 画的，CSS 管不到
  function effectiveTheme(value) {
    if (value !== "auto") return value;
    try {
      return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    } catch (error) {
      return "dark";
    }
  }

  function applyTheme(value, persist = true) {
    const theme = THEMES.includes(value) ? value : "auto";
    document.documentElement.setAttribute("data-theme", theme);
    // 知识类型配色是有语义的：画布、图例、运营轨道必须同时换，不能只反相画布。
    // 图例色块是 renderLegend 写死的内联样式，不重画就会和画布对不上。
    if (typeof G.setPalette === "function") {
      G.setPalette(effectiveTheme(theme));
      if (typeof G.renderLegend === "function") G.renderLegend();
      if (typeof G.renderOperationalRail === "function") G.renderOperationalRail();
      if (typeof G.requestRender === "function") G.requestRender();
    }
    if (persist) {
      try { localStorage.setItem(THEME_KEY, theme); } catch (error) { /* 无痕模式忽略 */ }
    }
    document.querySelectorAll("#themeChoice [data-theme-value]").forEach(button => {
      button.setAttribute("aria-checked", String(button.dataset.themeValue === theme));
    });
    const hint = $("themeHint");
    if (hint) {
      hint.textContent = theme === "auto"
        ? "随系统：跟随操作系统的深浅色设置。"
        : theme === "light" ? "浅色：适合明亮环境。" : "暗黑：适合夜间与暗色环境。";
    }
  }

  // ---------- 面板开合 ----------
  let lastFocused = null;

  function openPanel() {
    const panel = $("settingsPanel");
    const backdrop = $("settingsBackdrop");
    if (!panel) return;
    lastFocused = document.activeElement;
    panel.hidden = false;
    if (backdrop) backdrop.hidden = false;
    const openButton = $("openSettings");
    if (openButton) openButton.setAttribute("aria-expanded", "true");
    const first = panel.querySelector("button, select, input");
    if (first) first.focus();
  }

  function closePanel() {
    const panel = $("settingsPanel");
    const backdrop = $("settingsBackdrop");
    if (!panel || panel.hidden) return;
    panel.hidden = true;
    if (backdrop) backdrop.hidden = true;
    const openButton = $("openSettings");
    if (openButton) openButton.setAttribute("aria-expanded", "false");
    if (lastFocused && typeof lastFocused.focus === "function") lastFocused.focus();
  }

  // ---------- 与服务端同步 ----------
  async function pushSettings(updates) {
    if (!G.localManagement) return null;
    try {
      const result = await G.requestJson("/api/settings", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(updates),
      });
      return result.settings;
    } catch (error) {
      G.toast(`设置没能保存：${error.message}`);
      return null;
    }
  }

  // 把「默认展示后台记录」同步给镜头面板里那个开关，避免两处状态不一致
  function syncBackgroundToggle(value) {
    const toggle = $("showBackgroundRecords");
    if (!toggle) return;
    if (toggle.checked === Boolean(value)) return;
    toggle.checked = Boolean(value);
    toggle.dispatchEvent(new Event("change", {bubbles: true}));
  }

  // ---------- 智能体 ----------
  function agentBrief() {
    const service = runtime.service || `http://127.0.0.1:${location.port || 8765}`;
    const token = (runtime.token || "").trim();
    return [
      "# 知识库星图工作台 · 智能体接入",
      "",
      `本地服务：${service}`,
      `笔记库：${runtime.vault || "（见工作台）"}`,
      `访问令牌：${token || "（未生成）"}`,
      "",
      "## 请求方式",
      "读接口无需令牌；写接口必须带请求头：",
      `    X-KB-Token: ${token || "<令牌>"}`,
      "",
      "## 接口",
      "GET  /api/status              服务状态",
      "GET  /api/settings            当前设置",
      "GET  /api/version             数据指纹（工具版本 + 页面 mtime）",
      "GET  /api/maintenance/note?path=<相对路径>   读取一篇笔记",
      "POST /api/open-note           在编辑器里打开笔记 {path}",
      "POST /api/maintenance/preview 生成差异预览 {mode,path,content}",
      "POST /api/maintenance/commit  确认写入 {preview,token,confirm}",
      "",
      "## 注意",
      "· 写入前先 preview 拿 diff 与 token，再 commit，不要直接改文件。",
      "· 关闭「允许智能体写入」后，只有浏览器界面能写。",
    ].join("\n");
  }

  async function copyTextSafely(text, okMessage) {
    try {
      await G.copyText(text);
      G.toast(okMessage);
    } catch (error) {
      G.toast(error.message);
    }
  }

  // ---------- 初始化 ----------
  function initVersion() {
    if (!G.localManagement) {
      const version = $("settingsVersion");
      if (version) version.textContent = "分享快照模式";
      return;
    }
    fetch("/api/version", {cache: "no-store"})
      .then(response => response.json())
      .then(data => {
        const version = $("settingsVersion");
        if (!version || !data || !data.token) return;
        const found = String(data.token).split(":")[0];
        // 从源码目录直接跑服务时读不到安装清单，显示「未知」不如说清楚
        version.textContent = found && found !== "未知" ? `v${found}` : "本机工具（版本未知）";
      })
      .catch(() => { /* 服务端可能正在重启 */ });
  }

  function initAgentSection() {
    const service = runtime.service || `http://127.0.0.1:${location.port || 8765}`;
    const serviceEl = $("settingsService");
    if (serviceEl) serviceEl.textContent = service;
    const tokenEl = $("settingsToken");
    if (tokenEl) tokenEl.textContent = runtime.token ? `${runtime.token.slice(0, 8)}…${runtime.token.slice(-4)}` : "—";
    const vaultEl = $("settingsVault");
    if (vaultEl) vaultEl.textContent = runtime.vault || "—";

    const writeToggle = $("agentWriteEnabled");
    if (writeToggle) {
      writeToggle.checked = Boolean(serverSettings.agent_write);
      writeToggle.addEventListener("change", async event => {
        const value = event.target.checked;
        const saved = await pushSettings({agent_write: value});
        if (saved) {
          serverSettings.agent_write = Boolean(saved.agent_write);
          G.toast(value ? "已允许智能体写入" : "已关闭智能体写入，接口只读");
        } else {
          event.target.checked = !value;
        }
      });
    }

    const regenerate = $("regenerateToken");
    if (regenerate) {
      regenerate.addEventListener("click", async () => {
        if (!window.confirm("重新生成令牌后，已经接入的智能体需要换成新令牌。继续？")) return;
        const token = Array.from(crypto.getRandomValues(new Uint8Array(16)))
          .map(byte => byte.toString(16).padStart(2, "0")).join("");
        const saved = await pushSettings({agent_token: token});
        if (!saved) return;
        runtime.token = token;
        if (tokenEl) tokenEl.textContent = `${token.slice(0, 8)}…${token.slice(-4)}`;
        G.toast("令牌已更新，页面会自动刷新");
      });
    }

    const copyService = $("copyServiceUrl");
    if (copyService) copyService.addEventListener("click", () => copyTextSafely(service, "服务地址已复制"));

    const copyToken = $("copyAgentToken");
    if (copyToken) copyToken.addEventListener("click", () => copyTextSafely(runtime.token || "", "令牌已复制"));

    const copyBrief = $("copyAgentBrief");
    if (copyBrief) copyBrief.addEventListener("click", () => copyTextSafely(agentBrief(), "接入说明已复制"));
  }

  function initNoteSection() {
    const modeSelect = $("noteOpenMode");
    if (modeSelect) {
      modeSelect.value = String(serverSettings.note_open_mode || "auto");
      modeSelect.addEventListener("change", async event => {
        const saved = await pushSettings({note_open_mode: event.target.value});
        if (saved) {
          serverSettings.note_open_mode = String(saved.note_open_mode);
          G.toast("打开方式已保存");
        } else {
          event.target.value = String(serverSettings.note_open_mode || "auto");
        }
      });
    }

    const backgroundToggle = $("showBackgroundDefault");
    if (backgroundToggle) {
      backgroundToggle.checked = Boolean(serverSettings.show_background);
      backgroundToggle.addEventListener("change", async event => {
        const value = event.target.checked;
        const saved = await pushSettings({show_background: value});
        if (!saved) {
          event.target.checked = !value;
          return;
        }
        serverSettings.show_background = Boolean(saved.show_background);
        syncBackgroundToggle(serverSettings.show_background);
      });
    }
  }

  function initThemeSection() {
    document.querySelectorAll("#themeChoice [data-theme-value]").forEach(button => {
      button.addEventListener("click", () => applyTheme(button.dataset.themeValue));
    });
    // 选「随系统」时，系统切换明暗要跟着变（画布配色也要跟着换）
    try {
      const media = window.matchMedia("(prefers-color-scheme: light)");
      const onChange = () => { if (readTheme() === "auto") applyTheme("auto", false); };
      if (media.addEventListener) media.addEventListener("change", onChange);
      else if (media.addListener) media.addListener(onChange);
    } catch (error) { /* 老浏览器忽略 */ }
    applyTheme(readTheme(), false);
  }

  function initPanel() {
    const openButton = $("openSettings");
    if (openButton) {
      openButton.setAttribute("aria-expanded", "false");
      openButton.addEventListener("click", openPanel);
    }
    const closeButton = $("closeSettings");
    if (closeButton) closeButton.addEventListener("click", closePanel);
    const backdrop = $("settingsBackdrop");
    if (backdrop) backdrop.addEventListener("click", closePanel);
    document.addEventListener("keydown", event => {
      if (event.key === "Escape") closePanel();
    });
  }

  initPanel();
  initThemeSection();
  initNoteSection();
  initAgentSection();
  initVersion();

  // 设置面板里的「展示后台记录」默认值在镜头面板渲染后才有对应控件，晚一拍同步
  if (serverSettings.show_background) {
    setTimeout(() => syncBackgroundToggle(true), 0);
  }

  Object.assign(G, {applyTheme, openSettingsPanel: openPanel, closeSettingsPanel: closePanel});
})();
