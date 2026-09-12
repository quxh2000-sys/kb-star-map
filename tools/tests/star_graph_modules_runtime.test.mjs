// 星图 7 个模块的运行时冒烟测试。
//
// 为什么需要它：模块被拆分/改造后，语法能过、装配顺序能过，但"接线"仍可能错——
// 例如某模块解构了尚未导出的成员、导出名单写漏、初始化时访问了不存在的 DOM。
// 这类错误只有真正执行模块体才会暴露。改造过程中确实踩过一次（`let G.x` 语法错），
// 人工点浏览器才发现；本用例把它前移到自动测试。
//
// 做法：用最小 DOM/Canvas 桩在 Node 里按清单顺序执行 core + 7 个模块，
// 断言加载无异常、命名空间暴露预期 API、且渲染确实调用了绘制方法。

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const HERE = dirname(fileURLToPath(import.meta.url));
const ASSETS = join(HERE, "..", "dashboard_assets");
const manifest = JSON.parse(readFileSync(join(ASSETS, "star_graph.modules.json"), "utf8"));

const DASHBOARD_DATA = JSON.stringify({
  meta: { title: "测试快照", scanned_notes: 0 },
  graph: {
    nodes: [],
    edges: [],
    clusters: [],
    stats: { edge_count: 0 },
    healthByNodeId: {},
  },
  taxonomy: {},
  health: [],
  recent: [],
});

function makeCtx(calls) {
  const noop = (name) => (...args) => { calls.push(name); return undefined; };
  return {
    canvas: { width: 1440, height: 752 },
    save: noop("save"), restore: noop("restore"), beginPath: noop("beginPath"),
    moveTo: noop("moveTo"), lineTo: noop("lineTo"), arc: noop("arc"),
    fill: noop("fill"), stroke: noop("stroke"), fillRect: noop("fillRect"),
    strokeRect: noop("strokeRect"), clearRect: noop("clearRect"),
    fillText: noop("fillText"), setLineDash: noop("setLineDash"),
    setTransform: noop("setTransform"), translate: noop("translate"),
    scale: noop("scale"), closePath: noop("closePath"), rect: noop("rect"),
    measureText: () => ({ width: 42 }),
    createRadialGradient: () => ({ addColorStop() {} }),
    createLinearGradient: () => ({ addColorStop() {} }),
    // 可写属性：赋值不报错即可
    fillStyle: "", strokeStyle: "", lineWidth: 1, font: "", textAlign: "",
    globalAlpha: 1, shadowBlur: 0, shadowColor: "", globalCompositeOperation: "",
    lineCap: "", lineJoin: "",
  };
}

/** 通用桩：任何未知成员都可调用、可继续取属性，避免逐个补 DOM 方法。 */
function universalStub() {
  const fn = function () { return universalStub(); };
  return new Proxy(fn, {
    get(_target, prop) {
      if (prop === "then") return undefined;            // 不可被当作 thenable
      if (prop === Symbol.toPrimitive) return () => "";
      if (prop === Symbol.iterator) return undefined;
      if (prop === "toString" || prop === "valueOf") return () => "";
      return universalStub();
    },
    apply() { return universalStub(); },
    set() { return true; },
  });
}

/** 用通用桩兜底元素上未被显式实现的成员。 */
function wrapElement(el) {
  return new Proxy(el, {
    get(target, prop) {
      if (prop in target) return target[prop];
      if (prop === "then") return undefined;
      if (prop === "children" || prop === "childNodes") return [];
      return universalStub();
    },
    set(target, prop, value) { target[prop] = value; return true; },
  });
}

function makeElement(id, ctxCalls, cache) {
  const listeners = new Map();
  const el = {
    id,
    textContent: "",
    innerHTML: "",
    value: "",
    checked: false,
    hidden: false,
    disabled: false,
    width: 1440,
    height: 752,
    dataset: {},
    style: new Proxy({}, { get: () => "", set: () => true }),
    children: [],
    parentNode: null,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener(type, fn) { listeners.set(type, fn); },
    removeEventListener() {},
    dispatchEvent() { return true; },
    appendChild(child) { el.children.push(child); return child; },
    removeChild() {}, remove() {},
    setAttribute() {}, getAttribute: () => null, removeAttribute() {},
    // 冒烟测试关注"模块接线"而非"DOM 正确性"：任何查询都返回可用元素，
    // 避免因桩返回 null 产生与真实接线无关的假失败。
    querySelector: (sel) => {
      const key = `${id}::${sel}`;
      if (!cache.has(key)) cache.set(key, makeElement(sel, ctxCalls, cache));
      return cache.get(key);
    },
    querySelectorAll: () => [],
    closest: (sel) => {
      const key = `${id}::closest::${sel}`;
      if (!cache.has(key)) cache.set(key, makeElement(sel, ctxCalls, cache));
      return cache.get(key);
    },
    contains: () => false,
    insertBefore(c) { return c; }, replaceChildren() {},
    focus() {}, blur() {}, click() {}, scrollIntoView() {},
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 1440, height: 752, right: 1440, bottom: 752 }),
    getContext: () => makeCtx(ctxCalls),
    setPointerCapture() {}, releasePointerCapture() {},
    offsetWidth: 1440, offsetHeight: 752,
    clientWidth: 1440, clientHeight: 752,
  };
  return wrapElement(el);
}

/** 建立最小浏览器环境并执行全部模块，返回 { sandbox, ctxCalls, api } */
function loadModules() {
  const ctxCalls = [];
  const elements = new Map();

  const doc = {
    getElementById(id) {
      if (id === "dashboard-data") {
        return { textContent: DASHBOARD_DATA, id };
      }
      if (!elements.has(id)) elements.set(id, makeElement(id, ctxCalls, elements));
      return elements.get(id);
    },
    querySelector: (sel) => {
      const key = `document::${sel}`;
      if (!elements.has(key)) elements.set(key, makeElement(sel, ctxCalls, elements));
      return elements.get(key);
    },
    querySelectorAll: () => [],
    createElement: (tag) => makeElement(tag, ctxCalls, elements),
    addEventListener() {}, removeEventListener() {},
    execCommand() { return true; },
    body: makeElement("body", ctxCalls, elements),
    documentElement: makeElement("html", ctxCalls, elements),
    hidden: false,
    readyState: "complete",
  };

  let rafBudget = 200;
  const sandbox = {
    document: doc,
    location: { protocol: "http:", hostname: "127.0.0.1", reload() {}, href: "http://127.0.0.1/" },
    navigator: { clipboard: { writeText: () => Promise.resolve() }, userAgent: "node" },
    // 同步执行回调，并设预算上限，避免 render→requestRender 递归失控
    requestAnimationFrame(fn) {
      if (rafBudget-- > 0) { try { fn(0); } catch { /* 渲染期异常不阻断加载冒烟 */ } }
      return 1;
    },
    cancelAnimationFrame() {},
    setTimeout: (fn) => { try { fn(); } catch {} return 0; },
    clearTimeout() {},
    fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
    console,
    JSON, Math, Date, Object, Array, Map, Set, Number, String, Boolean,
    isNaN, parseInt, parseFloat, RegExp, Error, Promise, Symbol, Intl,
    // 常见浏览器全局量：冒烟测试需要，缺任一项模块就会在加载期抛错
    devicePixelRatio: 1,
    innerWidth: 1440,
    innerHeight: 900,
    getComputedStyle: () => ({ getPropertyValue: () => "", width: "1440px", height: "752px" }),
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {}, removeListener() {} }),
    alert() {}, confirm: () => false, prompt: () => null,
    setInterval: () => 0,
    clearInterval() {},
    queueMicrotask(fn) { try { fn(); } catch {} },
    atob: (s) => Buffer.from(s, "base64").toString("binary"),
    btoa: (s) => Buffer.from(s, "binary").toString("base64"),
    TextEncoder,
    TextDecoder,
    URL,
    URLSearchParams,
    performance: { now: () => 0, mark() {}, measure() {} },
    ResizeObserver: class { observe() {} unobserve() {} disconnect() {} },
    IntersectionObserver: class { observe() {} unobserve() {} disconnect() {} },
    MutationObserver: class { observe() {} disconnect() {} takeRecords() { return []; } },
    Blob: class { constructor() {} },
    FileReader: class { readAsText() {} readAsDataURL() {} },
    CustomEvent: class { constructor(t, o) { this.type = t; this.detail = o?.detail; } },
    Event: class { constructor(t) { this.type = t; } },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.window.isSecureContext = true;
  sandbox.window.addEventListener = () => {};

  const context = vm.createContext(sandbox);
  const sources = [
    ["star_graph_core.js", readFileSync(join(ASSETS, "star_graph_core.js"), "utf8")],
    ...manifest.modules.map((m) => [m.file, readFileSync(join(ASSETS, m.file), "utf8")]),
  ];
  for (const [name, code] of sources) {
    try {
      new vm.Script(code, { filename: name }).runInContext(context);
    } catch (err) {
      throw new Error(`模块 ${name} 加载失败：${err.message}`);
    }
  }
  return { sandbox, ctxCalls, api: sandbox.KBStarGraph };
}

test("全部模块按清单顺序加载且不抛异常", () => {
  const { api } = loadModules();
  assert.ok(api, "globalThis.KBStarGraph 未被建立");
});

test("命名空间暴露预期 API 面", () => {
  const { api } = loadModules();
  const expected = [
    // 状态与数据
    "data", "nodes", "edges", "state",
    // 渲染
    "render", "requestRender", "resize", "drawNodes", "drawEdges", "drawMini",
    // 交互
    "selectNode", "showNode", "resetView", "pointer",
    // 工具
    "copyText", "toast", "requestJson",
    // 维护面板
    "openMaintenance", "previewMaintenance", "commitMaintenance",
    // 运营面板
    "createOperationTask", "updateOperationsPanel",
    // 镜头与检索
    "renderLens", "renderLegend", "applyFilters", "renderSearch",
  ];
  const missing = expected.filter((k) => api[k] === undefined);
  assert.deepEqual(missing, [], `命名空间缺少成员：${missing.join(", ")}`);
});

test("初始化后确实执行了渲染绘制", () => {
  const { ctxCalls } = loadModules();
  assert.ok(ctxCalls.length > 0, "未调用任何 canvas 绘制方法，渲染链路可能未接通");
  const uniq = new Set(ctxCalls);
  assert.ok(uniq.has("clearRect"), "渲染未清屏，主循环可能未运行");
});

test("可变状态挂在命名空间上，可被跨模块读写", () => {
  const { api } = loadModules();
  for (const key of ["showBackground", "mainline", "frameRequested", "spatialGrid", "chainColumns"]) {
    assert.ok(key in api, `可变状态 ${key} 未挂到命名空间（跨模块读写会失效）`);
  }
  assert.equal(api.showBackground, false, "showBackground 初值应为 false");
});
