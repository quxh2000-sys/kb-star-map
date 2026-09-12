import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";
import test from "node:test";
import {fileURLToPath} from "node:url";
import {dirname, resolve} from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const corePath = resolve(here, "../dashboard_assets/star_graph_core.js");

function loadCore() {
  const context = {globalThis: {}};
  runInNewContext(readFileSync(corePath, "utf8"), context);
  return context.globalThis.KBStarGraphCore;
}

test("auto reload fires only when the fingerprint really changed and the editor is clean", () => {
  const core = loadCore();
  // 第一轮：记住指纹，不刷新
  assert.equal(core.reloadDecision(null, "1.0.0:111", false), "record");
  // 没变化：什么都不做（否则页面会反复刷）
  assert.equal(core.reloadDecision("1.0.0:111", "1.0.0:111", false), "wait");
  // 工具更新或星图重建：刷新
  assert.equal(core.reloadDecision("1.0.0:111", "1.0.1:111", false), "reload");
  assert.equal(core.reloadDecision("1.0.0:111", "1.0.0:222", false), "reload");
  // 有未提交的差异预览：先别刷，等用户处理完
  assert.equal(core.reloadDecision("1.0.0:111", "1.0.1:111", true), "defer");
  // 服务重启中拿不到指纹：等下一轮
  assert.equal(core.reloadDecision("1.0.0:111", "", false), "wait");
  assert.equal(core.reloadDecision("1.0.0:111", null, false), "wait");
});


test("mainline defaults hide background classes and navigation notes, not business summaries", () => {
  const core=loadCore();
  for(const type of ["运行记录","专题知识","拆解记录"]){
    assert.equal(core.isMainlineNode({asset_type:type,title:"内容",path:"内容.md"}),false);
  }
  for(const title of ["产品canonical索引","知识库汇总","README","readme-项目说明"]){
    assert.equal(core.isMainlineNode({asset_type:"知识原子",title,path:`${title}.md`}),false);
  }
  assert.equal(core.isMainlineNode({asset_type:"知识原子",title:"产值汇总计算规则",path:"规则.md",summary:"见索引与README"}),true);
  assert.equal(core.isMainlineNode({asset_type:"来源资料",title:"产品原文",path:"原文.md"}),true);
});

test("hidden decomposition projects only proven directed source paths and can be restored", () => {
  const core=loadCore();
  const nodes=[{id:"raw",asset_type:"来源资料",title:"原文"},{id:"split",asset_type:"拆解记录",title:"拆解"},{id:"atom",asset_type:"知识原子",title:"原子"},{id:"other",asset_type:"知识原子",title:"其他"}];
  const edges=[{id:"a",source:"raw",target:"split",family:"source"},{id:"b",source:"split",target:"atom",family:"source"},{id:"c",source:"split",target:"other",family:"unclassified"}];
  const view=core.mainlineGraph(nodes,edges,false);
  assert.deepEqual([...view.nodes.map(n=>n.id)],['raw','atom','other']);
  assert.equal(view.edges.length,1);
  assert.equal(view.edges[0].source,'raw');assert.equal(view.edges[0].target,'atom');
  assert.deepEqual([...view.edges[0].via],['split']);
  assert.equal(view.edges[0].projected,true);
  assert.equal(core.mainlineGraph(nodes,edges,true).nodes.length,4);
  assert.equal(core.mainlineGraph(nodes,edges,true).edges.length,3);
  assert.equal(edges.length,3);
});

test("a canvas click locates a related note without replacing the locked note", () => {
  const core=loadCore();
  assert.equal(core.canvasNoteAction(null,"product"),"select");
  assert.equal(core.canvasNoteAction("product","case"),"locate");
  assert.equal(core.canvasNoteAction("product","product"),"current");
  assert.equal(core.canvasNoteAction("product",null),"none");
  assert.equal(core.canvasNoteAction("product","case",true),"select");
});

test("unified focus includes incoming cases and all direct links plus upstream paths without unrelated siblings", () => {
  const core = loadCore();
  const nodes = [{id:"raw",asset_type:"来源资料"},{id:"split",asset_type:"拆解记录"},{id:"product",asset_type:"知识原子",dimension:"产品维度"},{id:"case",asset_type:"知识原子",dimension:"案例维度"},{id:"engineering",asset_type:"知识原子",dimension:"工程维度"},{id:"sibling",asset_type:"知识原子"},{id:"unrelated",asset_type:"知识原子"}];
  const edges = [{source:"raw",target:"split",family:"source"},{source:"split",target:"product",family:"source"},{source:"split",target:"sibling",family:"source"},{source:"case",target:"product",family:"call"},{source:"product",target:"engineering",family:"unclassified"}];
  assert.deepEqual([...core.focusNeighborhood("product",edges,nodes)].sort(), ["case","engineering","product","raw","split"]);
  assert.ok(core.focusNeighborhood("case",edges,nodes).has("product"));
  assert.deepEqual([...core.focusNeighborhood("raw",edges,nodes)].sort(), ["product","raw","sibling","split"]);
});

test("nine dimension layout retains every related node and explicitly groups unclassified atoms", () => {
  const core=loadCore();
  const nodes=[{id:"p",title:"产品",asset_type:"知识原子",dimension:"产品维度"},{id:"e",title:"隧道",asset_type:"知识原子",dimension:"工程维度"},{id:"c",title:"案例",asset_type:"知识原子",dimension:"案例维度"},{id:"u",title:"未分类",asset_type:"知识原子"}];
  const layout=core.focusLayout("p",nodes);
  assert.equal(layout.positions.length,4);
  assert.equal(new Set(layout.positions.map(n=>`${n.x},${n.y}`)).size,4);
  assert.deepEqual([...layout.groups.filter(g=>g.count).map(g=>g.title)].sort(),["工程维度","待定维度","案例维度"]);
  const coverage=core.dimensionCoverage(nodes);
  assert.equal(coverage.length,9);
  assert.equal(coverage.find(g=>g.dimension==="产品维度").count,1);
  assert.equal(coverage.find(g=>g.dimension==="行业维度").status,"unreviewed");
  assert.equal(core.dimensionCoverage(nodes,{行业维度:"not_in_source"}).find(g=>g.dimension==="行业维度").status,"not_in_source");
});

test("source expansion preserves a direct audit link but does not recursively unfold governance records", () => {
  const core=loadCore();
  const nodes=[{id:"raw",asset_type:"来源资料"},{id:"split",asset_type:"拆解记录"},{id:"atom",asset_type:"知识原子"},{id:"directAudit",asset_type:"治理规则"},{id:"indirectAudit",asset_type:"治理规则"}];
  const edges=[{source:"raw",target:"split",family:"source"},{source:"split",target:"atom",family:"source"},{source:"raw",target:"directAudit",family:"unclassified"},{source:"split",target:"indirectAudit",family:"source"}];
  assert.deepEqual([...core.focusNeighborhood("raw",edges,nodes)].sort(),["atom","directAudit","raw","split"]);
});

test("world and screen coordinates are inverse", () => {
  const core = loadCore();
  const camera = {x: 120, y: 80, zoom: 1.7, width: 900, height: 600};
  const point = {x: 320, y: 240};
  const roundTrip = core.screenToWorld(core.worldToScreen(point, camera), camera);
  assert.ok(Math.abs(roundTrip.x - point.x) < 0.0001);
  assert.ok(Math.abs(roundTrip.y - point.y) < 0.0001);
});

test("label visibility responds to priority and zoom", () => {
  const core = loadCore();
  assert.equal(core.visibleLabel({label_priority: 1}, 0.5), false);
  assert.equal(core.visibleLabel({label_priority: 4}, 0.5), false);
  assert.equal(core.visibleLabel({label_priority: 4}, 1.0), true);
  assert.equal(core.visibleLabel({label_priority: 2}, 1.5), false);
  assert.equal(core.visibleLabel({label_priority: 2}, 1.8), true);
});

test("spatial grid hit test returns nearest node", () => {
  const core = loadCore();
  const nodes = [
    {id: "a", x: 100, y: 100, radius: 5},
    {id: "b", x: 130, y: 100, radius: 5},
  ];
  const grid = core.buildSpatialGrid(nodes, 32);
  assert.equal(core.hitTest(grid, {x: 102, y: 101}, 8)?.id, "a");
  assert.equal(core.hitTest(grid, {x: 400, y: 400}, 8), null);
});

test("mobile selection collapses the lens panel", () => {
  const core = loadCore();
  assert.equal(core.shouldCollapseLens(390, true), true);
  assert.equal(core.shouldCollapseLens(1200, true), false);
  assert.equal(core.shouldCollapseLens(390, false), false);
});

test("default knowledge type lens shows the eight uniform asset groups", () => {
  const core = loadCore();
  assert.deepEqual(
    [...core.defaultTypeFilters()],
    ["来源资料", "拆解记录", "知识原子", "专题知识", "需求记录", "方案成果", "验证反馈", "治理规则"],
  );
  assert.equal(core.displayType("方案成果"), "方案成果");
});

test("relation views separate source call and all edges", () => {
  const core = loadCore();
  const edges = [
    {source: "source", target: "focus", family: "source"},
    {source: "focus", target: "called", family: "call"},
    {source: "legacy", target: "focus", family: "unclassified"},
  ];

  assert.deepEqual(JSON.parse(JSON.stringify(core.relationViewCounts("focus", edges))), {source: 1, call: 1, all: 3});
  assert.deepEqual([...core.relationNeighborhood("focus", 1, edges, "source")].sort(), ["focus", "source"]);
  assert.deepEqual([...core.relationNeighborhood("focus", 1, edges, "call")].sort(), ["called", "focus"]);
  assert.deepEqual([...core.relationNeighborhood("focus", 1, edges, "all")].sort(), ["called", "focus", "legacy", "source"]);
});

test("source chain reaches atoms through indexes without dragging in other sources", () => {
  const core=loadCore();
  const nodes=[{id:"raw",asset_type:"来源资料"},{id:"index",asset_type:"拆解记录"},{id:"split",asset_type:"拆解记录"},{id:"atom",asset_type:"知识原子"},{id:"other",asset_type:"来源资料"}];
  const edges=[{source:"raw",target:"index",family:"source"},{source:"index",target:"split",family:"source"},{source:"split",target:"atom",family:"source"},{source:"other",target:"atom",family:"source"}];
  assert.deepEqual([...core.sourceChain("raw",edges,nodes)].sort(),["atom","index","raw","split"]);
  assert.deepEqual([...core.sourceChain("atom",edges,nodes)].sort(),["atom","index","other","raw","split"]);
});

test("multi-select lenses union within a dimension and intersect across dimensions", () => {
  const core = loadCore();
  const filters = {
    types: new Set(["产品", "规则"]),
    industries: new Set(["公路", "铁路"]),
    evidence: new Set(["2", "3"]),
    health: new Set(["healthy", "warning"]),
  };
  const health = {product: "warning", rule: "blocked"};

  assert.equal(core.passesFilters({id: "product", asset_type: "产品", industries: ["公路"], evidence_level: 3}, filters, health), true);
  assert.equal(core.passesFilters({id: "rule", asset_type: "规则", industries: ["铁路"], evidence_level: 2}, filters, health), false);
  assert.equal(core.passesFilters({id: "project", asset_type: "项目", industries: ["公路"], evidence_level: 3}, filters, health), false);
});

test("rail layers independently hide graph node groups and health markers", () => {
  const core = loadCore();
  const visible = {nodes: true, products: true, rules: true, updates: true, health: true};

  assert.equal(core.layerAllowsNode({asset_type: "知识原子", dimension: "产品维度"}, {...visible, products: false}), false);
  assert.equal(core.layerAllowsNode({asset_type: "知识原子", dimension: "规则维度"}, {...visible, rules: false}), false);
  assert.equal(core.layerAllowsNode({asset_type: "运行记录"}, {...visible, updates: false}), false);
  assert.equal(core.layerAllowsNode({asset_type: "知识原子"}, visible), true);
  assert.equal(core.layerAllowsNode({asset_type: "知识原子"}, {...visible, nodes: false}), false);
  assert.equal(core.layerAllowsHealth({...visible, health: false}), false);
});

test("interactive legend can hide every type without falling back to all nodes", () => {
  const core = loadCore();
  const selectedTypes = new Set(["产品"]);

  core.toggleSelection(selectedTypes, "产品");

  assert.deepEqual([...selectedTypes], []);
  assert.equal(core.passesFilters({asset_type: "产品"}, {types: selectedTypes}), false);
});

test("interactive legend toggles one type back into the shared selection", () => {
  const core = loadCore();
  const selectedTypes = new Set();

  core.toggleSelection(selectedTypes, "规则");

  assert.deepEqual([...selectedTypes], ["规则"]);
  assert.equal(core.passesFilters({asset_type: "规则"}, {types: selectedTypes}), true);
});

test("lens summaries distinguish an empty type selection from unrestricted dimensions", () => {
  const core = loadCore();

  assert.equal(core.selectionSummary([], {emptyMeansAll: false}), "未选择");
  assert.equal(core.selectionSummary([], {emptyMeansAll: true}), "全部");
  assert.equal(core.selectionSummary(["公路", "铁路"], {emptyMeansAll: true}), "公路、铁路");
  assert.equal(core.selectionSummary(["产品", "规则"], {allSelected: true}), "全部");
});

test("all knowledge types stay summarized as all when runtime records are also visible", () => {
  const core = loadCore();
  const selected = new Set([...core.defaultTypeFilters(), "运行记录"]);

  assert.equal(core.selectionContainsAll(selected, core.defaultTypeFilters()), true);
});

test("graph controls are hidden by default and collapse for node focus", () => {
  const core = loadCore();

  assert.equal(core.graphControlsVisible(false, false), false);
  assert.equal(core.graphControlsVisible(true, false), true);
  assert.equal(core.graphControlsVisible(true, true), false);
});

test("product focus reveals the types of semantic source nodes", () => {
  const core = loadCore();
  const nodes = [
    {id: "product", asset_type: "知识原子", dimension: "产品维度"},
    {id: "source-a", asset_type: "拆解记录"},
    {id: "source-b", asset_type: "来源资料"},
    {id: "rule", asset_type: "知识原子", dimension: "规则维度"},
  ];
  const edges = [
    {source: "source-a", target: "product", relation: "decomposes_to"},
    {source: "source-b", target: "product", relation: "decomposes_to"},
    {source: "rule", target: "product", relation: "wikilink"},
  ];

  assert.deepEqual(
    [...core.semanticSourceTypes("product", edges, nodes)],
    ["来源资料", "拆解记录"],
  );
});

test("operational metrics are calculated from the currently scoped nodes", () => {
  const core = loadCore();
  const scopedNodes = [
    {id: "a", source_ref: [], evidence_level: 1, modified: "2026-09-08"},
    {id: "b", source_ref: ["来源"], evidence_level: 3, modified: "2026-09-07"},
    {id: "c", source_ref: [], evidence_level: "", modified: "2026-09-08, 09:20"},
  ];
  const metrics = core.operationalMetrics(scopedNodes, {a: "warning"});

  assert.deepEqual(JSON.parse(JSON.stringify(metrics)), {
    current: 3,
    missingSource: 2,
    lowEvidence: 2,
    governance: 1,
    latestUpdated: "2026-09-08",
  });
});

test("multiple operational tags match with OR semantics", () => {
  const core = loadCore();
  const selected = new Set(["missingSource", "governance"]);

  assert.equal(core.passesOperationalFilters({id: "a", source_ref: []}, selected, {}, "2026-09-08"), true);
  assert.equal(core.passesOperationalFilters({id: "b", source_ref: ["来源"]}, selected, {b: "warning"}, "2026-09-08"), true);
  assert.equal(core.passesOperationalFilters({id: "c", source_ref: ["来源"]}, selected, {}, "2026-09-08"), false);
});

test("operations layout creates the eight-stage spine and source satellites", () => {
  const core = loadCore();
  const layout = core.buildOperationsLayout(
    {sources: [{node_id: "s1", title: "资料", path: "知识库/资料.md"}]},
    1200,
    800,
  );

  assert.equal(layout.nodes.filter(node => node.kind === "stage").length, 8);
  assert.equal(layout.nodes.filter(node => node.kind === "source").length, 1);
  assert.equal(
    layout.edges.some(edge => edge.source === "s1" && edge.target === "stage:资料读取"),
    true,
  );
  assert.ok(layout.nodes.find(node => node.id === "s1").x >= 300);
  assert.ok(layout.nodes.find(node => node.id === "stage:资料读取").x >= 340);
});

test("operation source summary distinguishes empty and attached states", () => {
  const core = loadCore();

  assert.equal(core.operationSourceSummary(null), "尚未创建任务草稿");
  assert.equal(core.operationSourceSummary({sources: []}), "尚未加入来源");
  assert.equal(core.operationSourceSummary({sources: [{}, {}]}), "已加入2份来源");
});

test("operation writes are enabled only for the localhost management service", () => {
  const core = loadCore();

  assert.equal(core.isLocalManagementLocation("http:", "127.0.0.1"), true);
  assert.equal(core.isLocalManagementLocation("http:", "localhost"), true);
  assert.equal(core.isLocalManagementLocation("file:", ""), false);
  assert.equal(core.isLocalManagementLocation("https:", "example.com"), false);
});

test("complete operations layout connects candidates requirements retrievals and package", () => {
  const core = loadCore();
  const layout = core.buildOperationsLayout({
    stage: "输出准备与售前交接",
    sources: [{node_id: "s1", title: "资料"}],
    candidates: [{id: "c1", title: "候选规则", disposition: "new"}],
    requirements: [{id: "r1", text: "客户目标", kind: "goal"}],
    retrievals: [{node_id: "k1", title: "召回知识", asset_type: "规则"}],
    knowledge_package: {task_id: "t1", title: "知识包"},
  }, 1200, 800);

  const kinds = new Set(layout.nodes.map(node => node.kind));
  assert.equal(kinds.has("candidate"), true);
  assert.equal(kinds.has("requirement"), true);
  assert.equal(kinds.has("retrieval"), true);
  assert.equal(kinds.has("package"), true);
  assert.equal(layout.edges.some(edge => edge.source === "c1" && edge.target === "stage:分解与查重"), true);
  assert.equal(layout.edges.some(edge => edge.source === "r1" && edge.target === "stage:需求理解与知识召回"), true);
  assert.equal(layout.edges.some(edge => edge.source === "k1" && edge.target === "r1"), true);
  const requirementStage = layout.nodes.find(node => node.id === "stage:需求理解与知识召回");
  const requirementNode = layout.nodes.find(node => node.id === "r1");
  const retrievalNode = layout.nodes.find(node => node.id === "k1");
  assert.ok(requirementNode.y > requirementStage.y);
  assert.ok(retrievalNode.x > requirementStage.x);
});

test("compact labels keep long knowledge titles from dominating the operations view", () => {
  const core = loadCore();

  assert.equal(core.compactLabel("安全责任闭环", 8), "安全责任闭环");
  assert.equal(core.compactLabel("这是一个非常长的知识节点标题", 8), "这是一个非常长…");
});

// ---------- 变化时间镜头（设计规格 §3.1「哪些区域正在变化」）----------

test("time buckets are the five mutually exclusive ranges", () => {
  const core=loadCore();
  assert.equal(JSON.stringify(core.timeBuckets()),JSON.stringify(["7d","30d","90d","365d","older"]));
  assert.equal(core.timeBucketLabel("7d"),"近 7 天");
  assert.equal(core.timeBucketLabel("older"),"一年以上");
});

test("time bucket boundaries land on the right side of each cutoff", () => {
  const core=loadCore();
  const ref="2026-09-11";
  const cases=[
    ["2026-09-11","7d"],   // 0 天
    ["2026-09-04","7d"],   // 恰 7 天
    ["2026-09-03","30d"],  // 8 天
    ["2026-08-12","30d"],  // 恰 30 天
    ["2026-08-11","90d"],  // 31 天
    ["2026-06-13","90d"],  // 恰 90 天
    ["2026-06-12","365d"], // 91 天
    ["2025-09-11","365d"], // 恰 365 天
    ["2025-09-10","older"],// 366 天
  ];
  for(const [modified,expected] of cases){
    assert.equal(core.timeBucketOf({modified},ref),expected,`${modified} 应归入 ${expected}`);
  }
});

test("unparseable dates are reported as unknown instead of being guessed", () => {
  const core=loadCore();
  assert.equal(core.timeBucketOf({modified:""},"2026-09-11"),null);
  assert.equal(core.timeBucketOf({modified:"去年"},"2026-09-11"),null);
  assert.equal(core.timeBucketOf({modified:"2026-09-11"},""),null);
});

test("time filter passes everything when nothing is selected and drops unknown dates when active", () => {
  const core=loadCore();
  const ref="2026-09-11";
  const fresh={modified:"2026-09-10"};
  const stale={modified:"2025-01-01"};
  const unknown={modified:"未知"};
  assert.equal(core.passesTimeFilter(fresh,new Set(),ref),true);
  assert.equal(core.passesTimeFilter(stale,new Set(),ref),true);
  assert.equal(core.passesTimeFilter(unknown,new Set(),ref),true);
  assert.equal(core.passesTimeFilter(fresh,new Set(["7d"]),ref),true);
  assert.equal(core.passesTimeFilter(stale,new Set(["7d"]),ref),false);
  // 日期不可解析的节点在启用时间筛选时被排除，不臆造归属
  assert.equal(core.passesTimeFilter(unknown,new Set(["7d"]),ref),false);
  // 多选取并集
  assert.equal(core.passesTimeFilter(stale,new Set(["7d","older"]),ref),true);
});

test("time bucket counts cover every node exactly once", () => {
  const core=loadCore();
  const ref="2026-09-11";
  const nodes=[{modified:"2026-09-11"},{modified:"2026-09-01"},{modified:"2026-01-01"},{modified:"2024-01-01"},{modified:"未知"}];
  const {counts,unknown}=core.timeBucketCounts(nodes,ref);
  assert.equal(unknown,1);
  assert.equal(counts["7d"],1);
  assert.equal(counts["30d"],1);
  assert.equal(counts["365d"],1);
  assert.equal(counts["older"],1);
  const total=Object.values(counts).reduce((a,b)=>a+b,0)+unknown;
  assert.equal(total,nodes.length,"所有节点应恰好落入一个桶或计入 unknown");
});

// ---------- 时间轴回放（参照 Obsidian Graph view 的 Animate）----------

test("playback timeline lists sorted unique dates and its range", () => {
  const core=loadCore();
  const tl=core.playbackTimeline([
    {modified:"2026-05-10 12:30"},{modified:"2026-04-02"},
    {modified:"2026-05-10"},{modified:"未知"},{modified:""},
  ]);
  assert.equal(JSON.stringify(tl.dates),JSON.stringify(["2026-04-02","2026-05-10"]));
  assert.equal(tl.min,"2026-04-02");
  assert.equal(tl.max,"2026-05-10");
  assert.equal(tl.total,2,"同一天的不同写法应合并，无法解析的不计入");
});

test("playback timeline is empty-safe", () => {
  const core=loadCore();
  const tl=core.playbackTimeline([]);
  assert.equal(tl.total,0);
  assert.equal(tl.min,"");
  assert.equal(tl.max,"");
});

test("playback passes everything when disabled and reveals cumulatively when enabled", () => {
  const core=loadCore();
  const early={modified:"2026-01-01"};
  const mid={modified:"2026-06-01"};
  const late={modified:"2026-09-01"};
  const unknown={modified:"未知"};
  // 未启用：全部通过
  assert.equal(core.withinPlayback(early,""),true);
  assert.equal(core.withinPlayback(late,""),true);
  assert.equal(core.withinPlayback(unknown,""),true);
  // 游标推到 mid：只显示"截止该日已存在"的
  assert.equal(core.withinPlayback(early,"2026-06-01"),true);
  assert.equal(core.withinPlayback(mid,"2026-06-01"),true);
  assert.equal(core.withinPlayback(late,"2026-06-01"),false);
  // 日期不可解析的节点在回放期间不显示（与时间筛选口径一致，不臆造归属）
  assert.equal(core.withinPlayback(unknown,"2026-06-01"),false);
  // 游标到最大日：全部有日期的节点都出现
  assert.equal(core.withinPlayback(late,"2026-09-01"),true);
});

// ---------- 分组着色（对照 Obsidian Graph view 的 Groups）----------

// 注意：core 在独立 vm realm 里求值，跨 realm 的数组原型不同，
// assert.deepEqual 会报 "same structure but not reference-equal"，统一用 JSON 比较。
const asJson = value => JSON.stringify(value);

test("type filter defaults cover custom and fallback types instead of a hardcoded whitelist", () => {
  const core = loadCore();
  // 分发包模板的兜底类型是「未分类」，不在硬编码清单里。
  // 若默认选集只认清单，这类库首次打开会被 passesFilters 过滤成空图。
  const fallbackOnly = [{asset_type: "未分类"}, {asset_type: "未分类"}];
  assert.equal(asJson(core.effectiveTypeFilters(fallbackOnly)), asJson(["未分类"]));
  assert.equal(
    core.passesFilters(fallbackOnly[0], {types: new Set(core.effectiveTypeFilters(fallbackOnly))}, {}),
    true,
  );
});

test("type filter defaults keep canonical order and append custom types sorted", () => {
  const core = loadCore();
  const nodes = [
    {asset_type: "方案成果"},
    {asset_type: "未分类"},
    {asset_type: "来源资料"},
    {asset_type: "我的自定义类"},
    {asset_type: "运行记录"},
  ];
  // 已知类型按硬编码顺序在前；自定义类型按字典序在后。
  assert.equal(
    asJson(core.effectiveTypeFilters(nodes)),
    asJson(["来源资料", "方案成果", "我的自定义类", "未分类"]),
  );
});

test("type filter defaults stay identical to the legacy whitelist on a standard vault", () => {
  const core = loadCore();
  const canonical = core.defaultTypeFilters().map(type => ({asset_type: type}));
  // 标准库（无自定义类型）不得因为这次改动改变默认可见集合。
  assert.equal(
    asJson(core.effectiveTypeFilters([...canonical, {asset_type: "运行记录"}])),
    asJson(core.defaultTypeFilters()),
  );
});

test("type filter defaults never auto-select the background record type", () => {
  const core = loadCore();
  // 「运行记录」维持原设计：默认不选，由「显示后台记录」开关另行加入。
  assert.equal(core.effectiveTypeFilters([{asset_type: "运行记录"}]).includes("运行记录"), false);
  assert.equal(asJson(core.effectiveTypeFilters([])), asJson([]));
});

test("query matching is case-insensitive and supports AND across terms", () => {
  const core=loadCore();
  const node={title:"PMSmart 产品介绍",path:"知识库/PMSmart.md",summary:"基建版量控",
    tags:["产品","基建"],aliases:["PMS"],industries:["基建"],dimension:"产品维度",sub_dimension:"平台产品"};
  assert.equal(core.matchQuery(node,"pmsmart"),true,"大小写不敏感");
  assert.equal(core.matchQuery(node,"PMSMART"),true);
  assert.equal(core.matchQuery(node,"pmsmart 基建"),true,"空格为「与」");
  assert.equal(core.matchQuery(node,"pmsmart 房建"),false,"任一词不中即不匹配");
  assert.equal(core.matchQuery(node,"产品维度"),true,"九维可参与分组");
  assert.equal(core.matchQuery(node,"基建"),true,"行业可参与分组");
});

test("empty query matches nothing so a blank group cannot swallow the vault", () => {
  const core=loadCore();
  const node={title:"任意",path:"a.md"};
  assert.equal(core.matchQuery(node,""),false);
  assert.equal(core.matchQuery(node,"   "),false);
  assert.equal(core.matchQuery(node,null),false);
});

test("group color resolves first-match-wins and falls back to null", () => {
  const core=loadCore();
  const node={title:"PMSmart 产品介绍",path:"知识库/PMSmart.md",tags:["产品"]};
  const groups=[{query:"不存在的词",color:"#111111"},{query:"pmsmart",color:"#ff8a4c"},{query:"产品",color:"#7ee081"}];
  assert.equal(core.resolveGroupColor(node,groups),"#ff8a4c","先定义者优先");
  assert.equal(core.resolveGroupColor(node,[]),null,"无分组回落资产类型配色");
  assert.equal(core.resolveGroupColor({title:"无关",path:"x.md"},groups),null,"无命中回落");
  assert.equal(core.resolveGroupColor(node,[{query:"pmsmart"}]),null,"分组缺颜色按未命中处理");
});
