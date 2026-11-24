// 视频 PS 前端 9/9：视频生成栏、右栏折叠、启动。
// 9 个文件是同一个全局作用域的普通脚本，按 index.html 里的顺序加载（原来是一个 app.js）。
// ---------- 视频生成栏：模型、LoRA（可叠几个 + 强度）、用哪些参考图，然后生成 ----------
let loraAvail = [], loraSel = [], genModels = [], genModel = "";
let genWfs = [], genWorkflow = "";                                 // 自己的视频工作流（workflows/video/*.json）；"" = 内置 H3
const shortName = (n) => n.replace(/\.(safetensors|ckpt|pt|pth)$/i, "");   // 下拉里只显示短名，完整名在 title
function renderLoras() {
  const opts = (cur) => [T('<option value="">— 选择 LoRA —</option>')].concat(
    [...new Set(loraAvail.concat(cur ? [cur] : []))].map((n) => `<option value="${esc(n)}" title="${esc(n)}" ${n === cur ? "selected" : ""}>${esc(shortName(n))}</option>`)).join("");
  $("#loraRows").innerHTML = loraSel.map((l, k) => T`<div class="lora-row" data-k="${k}">
    <select class="lora-name" title="${esc(l.name)}">${opts(l.name)}</select>
    <input class="lora-str" type="number" step="0.05" min="-2" max="2" value="${l.strength}" title="强度">
    <button class="lora-del ghostbtn" title="不用这个 LoRA">✕</button></div>`).join("") ||
    T('<div class="sub">没有用 LoRA（只用模型本身）</div>');
  $("#genModel").innerHTML = [...new Set(genModels.concat(genModel ? [genModel] : []))]
    .map((n) => `<option value="${esc(n)}" title="${esc(n)}" ${!genWorkflow && n === genModel ? "selected" : ""}>${esc(shortName(n))}</option>`).join("") +
    genWfs.map((w) => `<option value="wf-${esc(w.name)}"${w.available ? "" : " disabled"} title="${esc(w.note ? uiMsg(w.note) : w.name)}"` +
      `${w.name === genWorkflow ? " selected" : ""}>${esc(T("工作流 · ") + w.name)}${w.available ? "" : esc(T("（工作流不合格）"))}</option>`).join("");
  $("#genModel").title = genWorkflow ? T("工作流 · ") + genWorkflow : genModel;
  document.body.classList.toggle("gen-wf", !!genWorkflow);
}
async function loadSam() {                                        // 识别模型：ComfyUI 里名字带 sam 的 checkpoint，选了存 app_settings.json
  try {
    const r = await api("/api/sam_models", { timeout: 8000 });
    $("#samModel").innerHTML = [...new Set((r.models || []).concat(r.model ? [r.model] : []))]
      .map((n) => `<option value="${esc(n)}" title="${esc(n)}" ${n === r.model ? "selected" : ""}>${esc(shortName(n))}</option>`).join("");
    $("#samModel").title = r.model || "";
    $("#samMsg").textContent = r.error || "";
  } catch (e) { $("#samMsg").textContent = ""; }        // 连不上时顶栏的圆点会变红，这里不再刷「请求超时」
}
$("#samModel").onchange = async () => {
  try { await api("/api/sam_models", { json: { model: $("#samModel").value } }); $("#samMsg").textContent = ""; }
  catch (e) { $("#samMsg").textContent = ""; }         // 保存失败也不刷文字（圆点会红）
};
loadSam();
async function loadLoras() {
  try {
    const r = await api("/api/h3_loras", { timeout: 8000 });
    loraAvail = r.available; loraSel = r.selected; genModels = r.models || []; genModel = r.model || "";
    genWfs = r.workflows || []; genWorkflow = r.workflow || "";
    if (r.steps) $("#genSteps").value = r.steps;
    S.h3short = r.short || 768;
    S.h3mode = r.mode === "auto" ? "preset" : (r.mode || "preset");
    S.h3preset = r.preset || "768";
    S.h3free = (r.free && r.free.length === 2) ? r.free.map(Number) : [1344, 768];
    $("#genShort").innerHTML = (r.shorts || [480, 544, 640, 720, 768, 864, 1024, 1088])
      .map((v) => `<option value="${v}" ${v === S.h3short ? "selected" : ""}>${v}</option>`).join("");
    $("#genResMode").value = S.h3mode;
    $("#genW").value = S.h3free[0]; $("#genH").value = S.h3free[1];
    syncResUi();
    $("#loraMsg").textContent = r.error || "";
    renderLoras();
  } catch (e) { $("#loraMsg").textContent = e.message; }
}
function renderPresets() {                                         // 档位下拉：每档写上按现在画幅算出来的实际宽高
  if (!S.d) return;
  const f = getFrame(), keep = S.h3mode;
  S.h3mode = "preset";
  const old = S.h3preset;
  $("#genPreset").innerHTML = H3_PRESET_IDS.map((k) => {
    S.h3preset = k;
    const [w, h] = h3Canvas(f.w, f.h);
    const name = k === "768" ? T("768p 原生") : k + "p";
    return `<option value="${k}">${esc(name)} · ${w}×${h}${k === "960" || k === "1080" ? T("（未验证）") : ""}</option>`;
  }).join("");
  S.h3preset = old; S.h3mode = keep;
  $("#genPreset").value = S.h3preset || "768";
}
function syncResUi() {                                             // 按模式显示档位 / 短边 / 宽高输入（自由模式多一句说明）
  $("#genPresetWrap").style.display = S.h3mode === "preset" ? "" : "none";
  $("#genShortWrap").style.display = S.h3mode === "short" ? "" : "none";
  $("#genFreeWrap").style.display = S.h3mode === "free" ? "" : "none";
  $("#genResHint").style.display = S.h3mode === "free" ? "" : "none";
  renderGenRefs();
}
let genSaveSeq = 0;                                                // 连点两下时丢掉旧响应（避免把刚填的数覆盖回去）
async function saveGen() {                                         // 模型 / LoRA / 画布分辨率改了马上存（app_settings.json）
  const seq = ++genSaveSeq;
  try {
    const body = { loras: loraSel.filter((l) => l.name), model: genModel, steps: +$("#genSteps").value || 20,
                   mode: S.h3mode, preset: S.h3preset || "768", short: +$("#genShort").value || 768,
                   free: [+$("#genW").value || 1344, +$("#genH").value || 768], workflow: genWorkflow };
    const r = await api("/api/h3_loras", { json: body });
    if (seq !== genSaveSeq) return;
    loraSel = r.selected; genModel = r.model || genModel; if (r.steps) $("#genSteps").value = r.steps;
    if (r.workflows) { genWfs = r.workflows; genWorkflow = r.workflow || ""; }
    if (r.short) S.h3short = r.short;
    if (r.mode) S.h3mode = r.mode;
    if (r.preset) S.h3preset = r.preset;
    if (r.free) S.h3free = r.free.map(Number);
    syncResUi();
    renderLoras();
  } catch (e) { if (seq === genSaveSeq) $("#loraMsg").textContent = e.message; }
}
$("#genModel").onchange = () => {                                  // H3 主模型，或「工作流 · 名字」（value = wf-名字）
  const v = $("#genModel").value;
  if (v.startsWith("wf-")) genWorkflow = v.slice(3); else { genModel = v; genWorkflow = ""; }
  renderLoras(); renderGenRefs(); saveGen();
};
$("#genSteps").onchange = () => saveGen();
$("#genResMode").onchange = () => { S.h3mode = $("#genResMode").value; syncResUi(); saveGen(); };
$("#genPreset").onchange = () => { S.h3preset = $("#genPreset").value; renderGenRefs(); saveGen(); };
$("#genShort").onchange = () => { S.h3short = +$("#genShort").value || 768; saveGen(); };
$("#genW").onchange = () => saveGen();
$("#genH").onchange = () => saveGen();
$("#loraAdd").onclick = () => { loraSel.push({ name: "", strength: 1.0 }); renderLoras(); };
$("#loraRows").addEventListener("change", (e) => {
  const row = e.target.closest(".lora-row"); if (!row) return;
  const l = loraSel[+row.dataset.k];
  if (e.target.classList.contains("lora-name")) l.name = e.target.value;
  if (e.target.classList.contains("lora-str")) l.strength = +e.target.value;
  if (l.name) saveGen();
});
$("#loraRows").addEventListener("click", (e) => {
  const b = e.target.closest(".lora-del"); if (!b) return;
  loraSel.splice(+b.closest(".lora-row").dataset.k, 1); saveGen();
});
loadLoras();

// 记住用户 ✕ 掉的参考图（按项目存在浏览器里，刷新 / 重开软件也还在）。键 = 项目 id + 行 key，false = 这次不用
const GEN_OFF_KEY = "vps_genoff";
const genChecked = new Proxy((() => { try { return JSON.parse(localStorage.getItem(GEN_OFF_KEY) || "{}"); } catch { return {}; } })(), {
  set(t, k, v) {
    if (v === false) t[k] = false; else delete t[k];
    try { localStorage.setItem(GEN_OFF_KEY, JSON.stringify(t)); } catch {}
    return true;
  },
});
// H3 生成分辨率（和 engine.h3_canvas / preset_canvas 一样，改一边要改另一边）：
//   preset = 分辨率预设：标准画面框（竖屏对调）里按画幅比例放进去；768p = 官方默认（短边 768 + 面积上限 768×1344）
//   short  = 短边缩放：面板里选的短边，面积上限跟着短边放大
//   free   = 自由分辨率：面板里填的宽高上限，按画幅比例放进去
// 每边取 32 的倍数（预设正好在中间时：短边往下、长边往上）
const H3_SHORT_DEFAULT = 768;
const H3_PRESET_BOX = { 480: [854, 480], 540: [960, 540], 720: [1280, 720], 768: null, 960: [1706, 960], 1080: [1920, 1080] };
const H3_PRESET_IDS = ["480", "540", "720", "768", "960", "1080"];
function presetCanvas(w, h, preset) {                               // 分辨率预设的画布（engine.preset_canvas；768p 走官方默认规则）
  const r = w / h, box = H3_PRESET_BOX[preset];
  if (!box) { const save = S.h3mode; S.h3mode = "default"; const c = h3Canvas(w, h); S.h3mode = save; return c; }
  const snapTie = (v, up) => { const q = v / 32, i = Math.floor(q), d = q - i; return Math.max(32, (i + (d > 0.5 || (d === 0.5 && up) ? 1 : 0)) * 32); };
  const [bw, bh] = r >= 1 ? box : [box[1], box[0]];
  const [nw, nh] = r >= bw / bh ? [bw, bw / r] : [bh * r, bh];
  return [snapTie(nw, r >= 1), snapTie(nh, r < 1)];
}
// 二次采样（2026-11-18 用户定：默认关，二采 1080p、去噪 0.5）：记在浏览器里（所有项目共用），genOp 带 pass2
const PASS2_KEY = "vps_gen_pass2";
const pass2Get = () => { try { return { on: false, preset: "1080", denoise: 0.5, ...JSON.parse(localStorage.getItem(PASS2_KEY) || "{}") }; } catch { return { on: false, preset: "1080", denoise: 0.5 }; } };
function pass2Save() {
  const v = { on: $("#genPass2").checked, preset: $("#genPass2Res").value || "1080", denoise: Math.min(0.9, Math.max(0.1, +$("#genPass2Dn").value || 0.5)) };
  try { localStorage.setItem(PASS2_KEY, JSON.stringify(v)); } catch {}
  renderGenRefs();
}
function syncPass2Ui() {
  const v = pass2Get(), f = S.d && getFrame();
  $("#genPass2").checked = !!v.on;
  $("#genPass2Res").innerHTML = ["720", "1080"].map((k) => {
    const c = f ? presetCanvas(f.w, f.h, k) : null;
    return `<option value="${k}"${v.preset === k ? " selected" : ""}>${k}p${c ? ` · ${c[0]}×${c[1]}` : ""}</option>`;
  }).join("");
  if (document.activeElement !== $("#genPass2Dn")) $("#genPass2Dn").value = v.denoise;
  $("#genPass2ResWrap").hidden = $("#genPass2DnWrap").hidden = !v.on;
}
$("#genPass2").onchange = pass2Save;
$("#genPass2Res").onchange = pass2Save;
$("#genPass2Dn").onchange = pass2Save;
// 主体重绘强度（默认 0 = 本体不重绘）、颜色校正（默认开）：2026-11-21 用户要（成片颜色和场景不统一），记在浏览器里（所有项目共用）
const LOOK_KEY = "vps_gen_look";
const lookGet = () => { try { return { body: 0, color: true, ...JSON.parse(localStorage.getItem(LOOK_KEY) || "{}") }; } catch { return { body: 0, color: true }; } };
function lookSave() {
  const v = { body: Math.min(1, Math.max(0, +$("#genBodyRedraw").value || 0)), color: $("#genColorFix").checked };
  try { localStorage.setItem(LOOK_KEY, JSON.stringify(v)); } catch {}
  $("#genBodyRedraw").value = v.body;
  renderGenPrompt();                                                 // 强度 0 和 > 0 时系统提示词的写法不同
}
$("#genBodyRedraw").value = lookGet().body;
$("#genColorFix").checked = lookGet().color;
$("#genBodyRedraw").onchange = $("#genColorFix").onchange = lookSave;
function h3Canvas(w, h) {
  const r = w / h;
  const snap = (v) => Math.max(32, Math.round(v / 32) * 32);
  const snapTie = (v, up) => { const q = v / 32, i = Math.floor(q), d = q - i; return Math.max(32, (i + (d > 0.5 || (d === 0.5 && up) ? 1 : 0)) * 32); };
  if (S.h3mode === "preset" && H3_PRESET_BOX[S.h3preset || "768"]) return presetCanvas(w, h, S.h3preset);
  if (S.h3mode === "free") {
    const [fw, fh] = (S.h3free && S.h3free.length === 2) ? S.h3free.map(Number) : [1344, 768];
    const [nw, nh] = r >= fw / fh ? [fw, fw / r] : [fh * r, fh];
    return [snap(nw), snap(nh)];
  }
  const short = S.h3mode === "short" ? +(S.h3short || H3_SHORT_DEFAULT) : H3_SHORT_DEFAULT;
  let [nw, nh] = r >= 1 ? [short * r, short] : [short, short / r];
  const cap = Math.max(768 * 1344, 768 * 1344 * (short / H3_SHORT_DEFAULT) ** 2);   // 面积上限跟着短边放大（和 engine 一样）
  if (nw * nh > cap) { const k = Math.sqrt(cap / (nw * nh)); nw *= k; nh *= k; }
  return [snap(nw), snap(nh)];
}
let transSave = Promise.resolve();
const growText = (v) => (+v ? v + " px" : T("关"));                  // 融合带
const avoidText = (v) => (+v < 0 ? T`重绘 ${-v} px` : +v > 0 ? T`保护 ${v} px` : "0");   // 主体边缘：负 = 人边也重绘，正 = 留出距离
function syncTransUi() {
  $("#genTransGrowVal").textContent = growText($("#genTransGrow").value);
  $("#genTransAvoidVal").textContent = avoidText($("#genTransAvoid").value);
  $("#genFrames").disabled = !$("#genBlue").checked;                // 前后扩展只对移动区域重绘有用
  $("#genFrames").closest(".sub-opt").classList.toggle("off", !$("#genBlue").checked);
  for (const [c, n] of [["#genEdgeFeather", "#genEdgePx"], ["#genHoleFeather", "#genHolePx"]]) {   // 羽化：关着时宽度变灰
    $(n).disabled = !$(c).checked;
    $(n).closest(".sub-opt").classList.toggle("off", !$(c).checked);
  }
}
function saveTrans() {
  syncTransUi();
  const pid = S.pid;
  if (!pid) return;
  const json = { blue: $("#genBlue").checked, frames: +$("#genFrames").value,
                 grow: +$("#genTransGrow").value, avoid: +$("#genTransAvoid").value,
                 edge_feather: $("#genEdgeFeather").checked, edge_px: Math.min(128, Math.max(1, +$("#genEdgePx").value || 32)),
                 hole_feather: $("#genHoleFeather").checked, hole_px: Math.min(128, Math.max(1, +$("#genHolePx").value || 32)) };
  transSave = transSave.catch(() => {}).then(async () => {
    const settings = await api(`/api/p/${pid}/trans`, { json });
    if (pid !== S.pid) return;
    S.d.trans = settings;
    S.zoneGen = (S.zoneGen || 0) + 1; zoneDecoded.clear(); zoneImgs.clear(); zoneRetry.clear(); drawSoon();
  });
  transSave.catch(e => setLog(e.message, true));
}
$("#genBlue").onchange = saveTrans;
$("#genFrames").onchange = saveTrans;
$("#genTransGrow").onchange = saveTrans;
$("#genTransGrow").oninput = syncTransUi;
$("#genTransAvoid").onchange = saveTrans;
$("#genTransAvoid").oninput = syncTransUi;
for (const id of ["#genEdgeFeather", "#genEdgePx", "#genHoleFeather", "#genHolePx"]) $(id).onchange = saveTrans;
// ---------- 参考图片（视频生成）：清单式，一行一张（07d-reflist.js）----------
// 首帧固定第一行（没有时提醒）；尾帧 / 背景图 / 资料卡 / 其他图片按需「+ 添加」。编号 = 传给 H3 的 Picture N，
// 顺序跟后端一样：首帧、尾帧、关键帧（按帧号，只当参考图）、背景图、资料卡、其他图片。✕ = 这次不用（文件和图片标签里的选用都不动；其他图片从列表去掉）。
function genRefItems() {                                           // 所有可能的参考图：key、用途、来源、缩略图、有没有图
  const tabs = imgTabs(), pn = S.d.panorama || {};
  const items = [];
  const tab = (key, role, it) => items.push({ key, role, it, ok: !!(it && it.pick),
    src: it && it.pick ? T`候选 #${it.cands.indexOf(it.pick) + 1} · ${imgLabel(it)}` : "",
    thumb: it && it.pick ? imgUrl(it, it.pick, 240) : "", big: it && it.pick ? imgUrl(it, it.pick, 0) : "" });
  tab("first", T("首帧"), tabs.find((x) => x.kind === "first"));
  tab("last", T("尾帧"), tabs.find((x) => x.kind === "last"));
  for (const it of keyTabs()) tab("key:" + it.id, imgLabel(it), it);
  const bgUrl = (w) => `/api/p/${S.pid}/panorama/${pn.use || "qwen_full"}.png?v=${pn.v}&w=${w}`;
  items.push({ key: "bg", role: T("背景图"), ok: !!pn.filled, src: T("背景图标签选用的那张"), thumb: pn.filled ? bgUrl(240) : "", big: pn.filled ? bgUrl(0) : "" });
  for (const it of tabs.filter((x) => x.kind === "card")) tab("card:" + it.id, T("人物 · ") + it.label, it);
  for (const r of S.d.gen_refs || []) items.push({ key: "other:" + r.id, role: r.label || T("其他图片"), ok: true, src: T("上传的图片"),
    thumb: `/api/p/${S.pid}/gen_refs/${r.id}.png?w=240`, big: `/api/p/${S.pid}/gen_refs/${r.id}.png` });
  return items;
}
const genUsed = (x) => x.ok && genChecked[S.pid + x.key] !== false;
function renderGenRefs() {
  if (!S.d) return;
  renderGenPrompt();                                               // 参考图变了：系统提示词（没改过的）跟着重算
  renderPresets();                                                 // 画幅变了：档位上写的宽高跟着变
  syncPass2Ui();
  { const f = getFrame(), c1 = h3Canvas(f.w, f.h), pv = pass2Get(), c2 = pv.on && !genWorkflow ? presetCanvas(f.w, f.h, pv.preset) : null;
    const [cw, ch] = c2 && c2[0] * c2[1] > c1[0] * c1[1] ? c2 : c1;   // 成片分辨率 = H3 生成画面；开了二采且更大 = 二采的大小
    const g = (a, b) => (b ? g(b, a % b) : a), d = g(cw, ch), ratio = f.ratio && f.ratio !== "free" && f.ratio !== "orig" ? f.ratio : `${cw / d}:${ch / d}`;
    $("#genRes").textContent = `${cw} × ${ch}`;
    $("#genResSub").textContent = (cw / d > 50 ? (cw / ch).toFixed(2) + ":1" : ratio) + (cw > ch ? T(" · 横") : cw < ch ? T(" · 竖") : ""); }
  if (![$("#genTransGrow"), $("#genTransAvoid"), $("#genFrames"), $("#genBlue"), $("#genEdgeFeather"), $("#genEdgePx"),
        $("#genHoleFeather"), $("#genHolePx")].includes(document.activeElement)) {   // 外扩 / 主体避让：项目设置，默认 48 / -16
    const t = S.d.trans || {};
    $("#genBlue").checked = !!t.blue; $("#genFrames").value = t.frames ?? ((t.grow ?? 48) > 0 ? 2 : 0);
    $("#genTransGrow").value = t.grow ?? 48; $("#genTransAvoid").value = t.avoid ?? -16;
    $("#genEdgeFeather").checked = t.edge_feather ?? true; $("#genEdgePx").value = t.edge_px ?? 32;   // 羽化：画框边缘默认开、空洞默认关（generate.feather_opts）
    $("#genHoleFeather").checked = t.hole_feather ?? false; $("#genHolePx").value = t.hole_px ?? 32;
    syncTransUi();
  }
  let n = 0;
  const rows = [];
  for (const x of genRefItems()) {
    const used = genUsed(x);
    if (x.key === "first" && !used) {                              // 首帧永远占第一行：没有就提醒
      rows.push({ key: "first", role: T("首帧"), empty: true, fixed: true, change: true,
                  emptyHint: x.ok ? T("这次没用首帧（建议用上：稳很多）") : T("还没有首帧：建议加一张（稳很多），点 + 上传") });
      continue;
    }
    if (!used) continue;
    rows.push({ key: x.key, n: ++n, role: x.role, src: x.src, thumb: x.thumb, big: x.big, change: true, fixed: false });
  }
  $("#genRefs").innerHTML = refListHTML(rows);
}
function openImgTab(it) { S.tab = it.id; renderTabs(); }           // 去图片编辑里这张图的标签（挑候选 / 重新生成）
async function ensureTab(kind) {                                   // 没有这个图片标签就先建一个
  let it = imgTabs().find((t) => t.kind === kind);
  if (it) return it;
  const res = await api(`/api/p/${S.pid}/images`, { json: { kind } });
  if (res.pending) await watchJob();
  Object.assign(S.d, await api(`/api/p/${S.pid}`));
  it = imgTabs().find((t) => t.kind === kind);
  if (!it) throw new Error(T("建图片标签失败"));
  return it;
}
async function genRefUpload(key, file) {                           // 上传一张：首帧 / 尾帧 / 资料卡 = 上传成候选并选用；背景图 = 上传成背景图；其他 = 加一张
  if (key === "new") {
    await api(`/api/p/${S.pid}/gen_refs`, { method: "POST", body: file });
    Object.assign(S.d, await api(`/api/p/${S.pid}`));
    renderGenRefs(); setLog(T("已添加一张参考图"));
    return;
  }
  const [kind, id] = String(key).split(":");
  if (kind === "bg") {
    S.d.panorama = await api(`/api/p/${S.pid}/panorama/result_upload`, { method: "POST", body: file });
    genChecked[S.pid + "bg"] = true;
    renderPano(); renderGenRefs(); setLog(T("背景图换成上传的这张了"));
    return;
  }
  if (kind === "other") {                                          // 换图：加新的、去掉旧的
    await api(`/api/p/${S.pid}/gen_refs`, { method: "POST", body: file });
    S.d.gen_refs = await api(`/api/p/${S.pid}/gen_refs/${id}`, { method: "DELETE" });
    Object.assign(S.d, await api(`/api/p/${S.pid}`));
    renderGenRefs(); setLog(T("已换成上传的这张"));
    return;
  }
  const it = id ? imgTabs().find((t) => t.id === id) : await ensureTab(kind);
  const up = await api(`/api/p/${S.pid}/images/${it.id}/cands_upload`, { method: "POST", body: file });
  Object.assign(it, up);
  Object.assign(it, await api(`/api/p/${S.pid}/images/${it.id}/pick`, { json: { name: up.cands[up.cands.length - 1] } }));
  genChecked[S.pid + key] = true;                                  // 上传的直接当成选用
  renderTabs();
  setLog(T`${imgLabel(it)}：用上传的这张`);
}
async function genRefDrop(key) {                                   // ✕：这次不用这张（图片标签里的选用、文件都不动）；其他图片从列表去掉
  const [kind, id] = key.split(":");
  if (kind === "other") {
    S.d.gen_refs = await api(`/api/p/${S.pid}/gen_refs/${id}`, { method: "DELETE" });
    renderGenRefs(); setLog(T("已去掉这张其他图片（文件留在项目里）"));
    return;
  }
  genChecked[S.pid + key] = false;
  renderGenRefs();
  setLog(T("这次生成不用这张了（图还在原来的地方，可以从「+」加回来）"));
}
const uploadTo = async (key, multiple = false) => { for (const f of await pickImageFile(multiple)) await genRefUpload(key, f); };
function genChangeMenu(key, anchor) {                              // 「换 ▾」：去图片编辑里挑 / 上传一张
  const [kind, id] = key.split(":");
  const it = kind === "card" || kind === "key" ? imgTabs().find((t) => t.id === id) : imgTabs().find((t) => t.kind === kind);
  const items = [];
  if (kind === "first" || kind === "last" || kind === "card" || (kind === "key" && it))
    items.push({ label: T("去图片编辑里挑 / 生成"), hint: it ? it.label : T("会新建标签"), run: async () => openImgTab(it || await ensureTab(kind)) });
  if (kind === "bg") {
    const bgTab = imgTabs().find((t) => t.kind === "bg");
    items.push({ label: T("去背景图标签换"), disabled: !bgTab, hint: bgTab ? "" : T("还没有背景图标签"), run: () => openImgTab(bgTab) });
  }
  items.push({ label: T("上传一张"), run: () => uploadTo(key) });
  if (kind === "first" && !genUsed({ key, ok: !!(it && it.pick) }) && it && it.pick)
    items.unshift({ label: T("用回首帧标签里选用的那张"), run: () => { genChecked[S.pid + key] = true; renderGenRefs(); } });
  openRefMenu(anchor, items);
}
function genAddMenu(anchor) {                                      // 「+ 添加」：只列还没用上的
  const items = [];
  for (const x of genRefItems()) {
    if (x.key === "first" || x.key.startsWith("other:") || genUsed(x)) continue;
    if (x.ok) items.push({ label: x.role, hint: x.src, run: () => { genChecked[S.pid + x.key] = true; renderGenRefs(); } });
    else if (x.key === "last") items.push({ label: T("尾帧"), hint: T("上传一张"), run: () => uploadTo("last") });
    else if (x.key === "bg") items.push({ label: T("背景图"), hint: T("先在背景图标签提取 / 生成"), disabled: true });
  }
  if (!imgTabs().some((t) => t.kind === "card")) items.push({ label: T("人物资料卡"), hint: T("在图片编辑里「+」新建资料卡"), disabled: true });
  items.push({ sep: true }, { label: T("上传其他图片…"), hint: T("可多选"), run: () => uploadTo("new", true) });
  openRefMenu(anchor, items);
}
$("#genRefs").addEventListener("click", (e) => {
  if (!S.d || refZoom(e)) return;
  const b = e.target.closest("[data-act]"), row = e.target.closest(".rr");
  if (!b || !row) return;
  const key = row.dataset.key, act = b.dataset.act;
  b.dataset.refmenu = "gen:" + key;
  if (act === "upload") uploadTo(key).catch((err) => setLog(err.message, true));
  else if (act === "change") genChangeMenu(key, b);
  else if (act === "del") genRefDrop(key).catch((err) => setLog(err.message, true));
});
$("#genRefAdd").dataset.refmenu = "gen:add";
$("#genRefAdd").onclick = () => S.d && genAddMenu($("#genRefAdd"));
$("#imgZoom").onclick = () => { $("#imgZoom").hidden = true; };
const randSeed = () => Math.floor(Math.random() * 2 ** 31);
$("#genSeedRnd").onclick = () => { $("#genSeed").value = randSeed(); };
$("#imgSeedRnd").onclick = () => { $("#imgSeed").value = randSeed(); };
// 提示词里 @ 能引用的（07e-mention.js）：这次的参考图 + 粗剪。资料卡的 key 用图层 id（后端按图层认）。all = 没勾的也算
function genMentionItems(all) {
  const rows = [], tabs = imgTabs();
  for (const x of genRefItems()) {
    if (!x.ok || (!all && !genUsed(x))) continue;
    const [kind, id] = x.key.split(":");
    if (kind === "card") { const t = tabs.find((t) => t.id === id); if (t && t.oid) rows.push({ name: imgLabel(t), key: "card:" + t.oid, thumb: x.thumb }); }
    else if (kind === "other" || kind === "key") rows.push({ name: x.role, key: x.key, thumb: x.thumb });
    else rows.push({ ...mentionName({ first: "首帧", last: "尾帧", bg: "背景图" }[kind]), key: x.key, thumb: x.thumb });
  }
  rows.push({ ...mentionName("粗剪"), key: "video" });
  return uniqueNames(rows);
}
for (const id of ["#genUser", "#genSys", "#genApiPrompt"]) mentionAttach($(id), () => genMentionItems(false));
function genRefPayload() {                                         // 这次会传给 H3 的参考图（生成和系统提示词预览共用）
  const used = genRefItems().filter(genUsed), on = (k) => used.some((x) => x.key === k);
  const ticked = (pre) => used.filter((x) => x.key.startsWith(pre)).map((x) => x.key.slice(pre.length));
  const cards = ticked("card:").map((iid) => (imgTabs().find((t) => t.id === iid) || {}).oid).filter(Boolean);   // 用上的资料卡 → 图层 id
  const anchors = { first: on("first"), last: on("last") };
  for (const it of keyTabs()) anchors["key:" + it.id] = on("key:" + it.id);   // 关键帧：勾了的当 <Picture N>（默认不钉帧，generate.KEY_PIN）
  return { anchors, use_bg: on("bg"), cards, extra_refs: ticked("other:"),   // 其他图片 → gen_refs 的 id
           version: S.d.current };                                 // 排队的话按点击时的版本生成
}

// ---------- 提示词：系统提示词（自动生成，可改）+ 用户提示词，存在项目里（gen_prompt）----------
let gpSysDefault = "", gpTimer = 0, gpSaveTimer = 0, gpFor = "";
const gpCustom = () => !!((S.d.gen_prompt || {}).system || "").trim();
function syncGenPromptUi() {
  const custom = gpCustom();
  $("#genSysState").textContent = custom ? T("已修改") : T("自动");
  $("#genSysState").classList.toggle("on", custom);
  $("#genSysHint").textContent = custom ? T("改过了：不再跟着图层和参考图自动更新") : T("跟着图层和参考图自动更新");
  $("#genSysReset").disabled = !custom;
}
function renderGenPrompt() {                                        // 换项目时载入；系统提示词没改过就按现在的参考图重新算
  if (!S.d) return;
  const g = S.d.gen_prompt || {};
  if (gpFor !== S.pid) {
    gpFor = S.pid; gpSysDefault = "";
    $("#genUser").value = g.user || "";
    $("#genSys").value = g.system || "";
    $("#genApiPrompt").value = g.api || "";
  }
  syncGenPromptUi();
  if (gpCustom()) return;
  clearTimeout(gpTimer);
  gpTimer = setTimeout(async () => {
    const pid = S.pid, body = genRefPayload();
    try {
      const r = await api(`/api/p/${pid}/gen_prompt/preview`, { json: { ...body, body_redraw: lookGet().body } });   // 强度 > 0：重新打光的写法
      if (pid !== S.pid || gpCustom()) return;
      gpSysDefault = r.system;
      if (document.activeElement !== $("#genSys")) $("#genSys").value = r.system;
    } catch (err) { $("#genSysHint").textContent = T("系统提示词没算出来：") + err.message; }
  }, 400);
}
function saveGenPrompt() {
  if (!S.d) return Promise.resolve();
  clearTimeout(gpSaveTimer);
  const pid = S.pid, sys = $("#genSys").value;
  const g = { user: $("#genUser").value, system: sys.trim() && sys.trim() !== gpSysDefault.trim() ? sys : "",   // 和默认一样 = 没改
              api: $("#genApiPrompt").value };                     // 视频生成走 API 时的提示词（原样发给平台）
  S.d.gen_prompt = { ...(g.user.trim() ? { user: g.user } : {}), ...(g.system ? { system: g.system } : {}), ...(g.api.trim() ? { api: g.api } : {}) };
  syncGenPromptUi();
  return api(`/api/p/${pid}/gen_prompt`, { json: g }).catch((err) => setLog(T("提示词没存上：") + err.message, true));
}
const saveGenPromptSoon = () => { clearTimeout(gpSaveTimer); gpSaveTimer = setTimeout(saveGenPrompt, 600); };
$("#genUser").oninput = saveGenPromptSoon;
$("#genApiPrompt").oninput = saveGenPromptSoon;
$("#genSys").oninput = saveGenPromptSoon;
$("#genUser").onchange = $("#genSys").onchange = () => saveGenPrompt();
$("#genSysReset").onclick = async () => {
  $("#genSys").value = gpSysDefault;
  await saveGenPrompt();
  renderGenPrompt();
};

function genOp() {                                                 // 这次视频生成的参数（生成和「导出」共用）
  return { type: "compose", objects: [], h3: true, use_pano: true, prompt_style: "lora", ...genRefPayload(),
           prompt: $("#genUser").value.trim(), system: gpCustom() ? $("#genSys").value : null,
           mentions: mentionsOf([$("#genUser").value, gpCustom() ? $("#genSys").value : ""], genMentionItems(true)),
           seed: +$("#genSeed").value || 0, vl: $("#genVl").value !== "off",
           ...(!genWorkflow && genEngine !== "api" ? { body_redraw: lookGet().body, color_fix: lookGet().color } : {}),   // 只给 H3
           ...(pass2Get().on && !genWorkflow ? { pass2: { on: true, preset: pass2Get().preset, denoise: pass2Get().denoise } } : {}),
           ...(genWorkflow ? { workflow: genWorkflow } : {}),
           ...($("#genVl").value !== "off" ? { vl_model: $("#genVl").value } : {}) };
}
// 空区分析用哪个看图模型（不用 / ComfyUI 上的）：默认 Qwen3-VL 8B，记在浏览器里（所有项目共用）
const genVlPick = () => { let v = null; try { v = localStorage.getItem("vps_gen_vl"); } catch {}
  return v === "0" ? "off" : !v || v === "1" ? "qwen3vl8b" : v; };             // "0" / "1" = 以前的开关
async function loadVlModels() {
  try {
    const r = await api("/api/vl_models", { timeout: 15000 }), want = genVlPick();
    $("#genVl").innerHTML = `<option value="off">${T("不用")}</option>` + r.models.map((m) =>
      `<option value="${esc(m.key)}"${m.available ? "" : " disabled"}>${esc(uiMsg(m.label))}${m.available ? "" : T("（ComfyUI 上没有）")}</option>`).join("");
    $("#genVl").value = r.models.some((m) => m.key === want && m.available) || want === "off" ? want : "qwen3vl8b";
  } catch { $("#genVl").value = genVlPick() === "off" ? "off" : "qwen3vl8b"; }
}
$("#genVl").onchange = () => { try { localStorage.setItem("vps_gen_vl", $("#genVl").value); } catch {} };
loadVlModels();
// ---------- 视频生成用哪条路：ComfyUI（H3）或云端 API（粗剪 + 勾选的参考图 + 用户写的提示词，原样发）。记在浏览器里 ----------
let genEngine = (() => { try { return localStorage.getItem("vps_gen_engine") === "api" ? "api" : "comfy"; } catch { return "comfy"; } })();
function syncGenEngine() {
  const isApi = genEngine === "api";
  document.body.classList.toggle("gen-api", isApi);
  $$("#genEngineSw [data-eng]").forEach((b) => b.classList.toggle("on", b.dataset.eng === genEngine));
  if (isApi) genApiInfo();
}
function setGenEngine(e) {
  genEngine = e;
  try { localStorage.setItem("vps_gen_engine", e); } catch {}
  syncGenEngine();
}
async function genApiInfo() {
  try {
    const c = await api("/api/providers"), pf = c.profile || {}, rs = pf.video_resolutions || [];
    $("#genApiInfo").innerHTML = (c.profile ? esc(pf.name || c.provider) : `<span class="bad">${T("还没设云端平台：设置 → 云端 API")}</span>`) +
      (!c.profile || pf.video_path ? "" : ` · <span class="bad">${T("这个平台的配置里没有 video_path，不能做视频生成")}</span>`) +
      (c.key_set ? "" : ` · <span class="bad">${T("还没填 API key")}</span>`);
    if (document.activeElement !== $("#genApiModel")) $("#genApiModel").value = c.video_model || pf.video_model || "";
    $("#genApiRes").innerHTML = rs.map((r) => `<option value="${esc(r)}">${esc(r)}</option>`).join("");
    $("#genApiRes").value = rs.includes(c.video_resolution) ? c.video_resolution : (rs[0] || "");
  } catch {}
}
async function saveGenApi(json, msg) {
  try {
    const c = await api("/api/providers", { json });
    if (typeof fillProviders === "function") fillProviders(c);
    setLog(msg);
    genApiInfo();
  } catch (err) { setLog(err.message, true); }
}
$("#genApiModel").onchange = () => saveGenApi({ video_model: $("#genApiModel").value.trim() }, T("视频模型改成 ") + ($("#genApiModel").value.trim() || T("平台默认")));
$("#genApiRes").onchange = () => saveGenApi({ video_resolution: $("#genApiRes").value }, T("视频分辨率改成 ") + $("#genApiRes").value);
$("#genEngineSw").addEventListener("click", (e) => {                  // 切到 API：先提醒（用户定：每次都弹，可勾「不再提示」）
  const b = e.target.closest("[data-eng]");
  if (!b || b.dataset.eng === genEngine) return;
  if (b.dataset.eng !== "api") return setGenEngine("comfy");
  let off = false;
  try { off = localStorage.getItem("vps_gen_api_warn_off") === "1"; } catch {}
  if (off) return setGenEngine("api");
  $("#genApiWarnNoMore").checked = false;
  $("#genApiWarnDlg").hidden = false;                                  // 关掉 / 取消 = 留在 ComfyUI
});
$("#genApiWarnOk").onclick = () => {
  try { if ($("#genApiWarnNoMore").checked) localStorage.setItem("vps_gen_api_warn_off", "1"); } catch {}
  $("#genApiWarnDlg").hidden = true;
  setGenEngine("api");
};
$("#genApiWarnCancel").onclick = () => { $("#genApiWarnDlg").hidden = true; };
syncGenEngine();
const LONG_GEN_S = 15;                                       // 超过这么多秒的视频生成前提醒显存
$("#genBtn").onclick = async () => {
  if (!S.d || submitBusy) return;
  const pid = S.pid;
  await saveGenPrompt();
  if (genEngine === "api") {
    const prompt = $("#genApiPrompt").value.trim();
    if (!prompt) return setLog(T("先写提示词"), true);
    try {
      await transSave;
      await flushLayerSaves();
      await submitOnce(() => api(`/api/p/${pid}/op`, { json: { type: "api_video", objects: [], use_pano: true, ...genRefPayload(), prompt,
                                                                        mentions: mentionsOf(prompt, genMentionItems(true)) } }));
      watchJob();
    } catch (e) { setLog(e.message, true); }
    return;
  }
  const secs = S.d.n / (S.d.fps || 24);
  if (secs > LONG_GEN_S) {                                   // H3 在 ComfyUI 上生成：越长越吃显存，超过 15 秒先提醒（云端 API 不占本地显存，不提醒）
    const s = secs.toFixed(1);
    const go = await uiConfirm(uiLanguage === "en"
      ? `This video is ${s} s, longer than ${LONG_GEN_S} s. H3 needs a lot of VRAM for long videos, so generation may run out of memory or be very slow. Consider choosing Edit in segments when you open the video, or lowering the output resolution. Generate anyway?`
      : `这段视频 ${s} 秒，超过 ${LONG_GEN_S} 秒。H3 生成长视频很吃显存，可能显存不足失败或非常慢。建议打开视频时选「分段编辑」，或调低输出分辨率。仍然生成？`,
      { ok: T("仍然生成") });
    if (!go || S.pid !== pid) return;
  }
  const op = genOp();
  try {
    await transSave;
    await flushLayerSaves();
    const saves = layerSaves.get(pid);
    if (saves?.error) throw new Error(T("图层保存失败，请重新保存后再生成：") + saves.error);
    await submitOnce(() => api(`/api/p/${pid}/op`, { json: op }));
    watchJob();
  } catch (e) { setLog(e.message, true); }
};

// 「抠像与重绘范围」：鼠标放在哪一项（或正在拖它），画面叠加层就只亮那一项管的区；重绘幅外扩顺便画出框
$$(":is(#maskPanel, #mattePanel) .mctl").forEach((el) => {
  const f = el.dataset.focus ? +el.dataset.focus : null;          // 没写 data-focus 的行 = 不突出哪一类（以前会变成 NaN，叠加层全被调暗）
  const on = () => {
    $$(":is(#maskPanel, #mattePanel) .mctl").forEach((x) => x.classList.toggle("on", x === el));
    S.zoneFocus = f;
    S.growLine = !!el.querySelector("#growRange");
    if (S.growLine) drawLayers();
    drawZones();
  };
  const off = () => {
    if (el.contains(document.activeElement) && document.activeElement.type === "range") return;   // 还在拖
    el.classList.remove("on");
    S.zoneFocus = null;
    if (S.growLine) { S.growLine = false; drawLayers(); }
    drawZones(); drawGizmo();
  };
  el.addEventListener("mouseenter", on);
  el.addEventListener("mouseleave", off);
  el.addEventListener("focusin", on);
  el.addEventListener("focusout", off);
});

// 首帧 / 尾帧 / 资料卡的系统基础提示：标签里存的（旧标签只有一段 prompt，去掉背景句当基础提示），恢复默认用服务端的
let STILL_DEFAULT = "", STILL_OLD = [], SYS_DEF = {};   // 系统基础提示的默认值：填补 / 生成 / 上传的画面各一份
api("/api/image_sys_default").then((r) => {
  STILL_DEFAULT = r.still; STILL_OLD = r.old || [];
  SYS_DEF = { inpaint: r.still, gen: r.gen || r.still, upload: r.upload || r.still };
}).catch(() => {});
const BG_SENT = "Picture 2 is the clean, empty background of this exact shot and framing: paint every background area, " +
  "including the blurry and empty-looking parts, to match Picture 2 exactly. ";
function sysOf(it) {
  const s = (it.sys || it.prompt || "").split(BG_SENT).join("").trim();
  if (!isStill(it) || !STILL_DEFAULT) return s;
  const defs = [...STILL_OLD, ...Object.values(SYS_DEF)];
  return !s || defs.includes(s) ? sysDefault(it) : s;   // 没改过的默认提示（哪个模式的都算）：换成现在这个模式的
}
function genModeOf(it) { return it.base_upload ? "upload" : it.mode === "inpaint" ? "inpaint" : "gen"; }   // 默认生成
function sysDefault(it) { return isStill(it) ? SYS_DEF[genModeOf(it)] || STILL_DEFAULT : sysOf(it); }

// ---------- 启动：所有脚本都加载完以后才跑（原来在额外参考图那一段后面）----------
(async () => {
  let info;
  try { info = await api("/api/info"); }
  catch (e) {                                                       // 后端没起来：说清楚，别让页面一片空白不出声
    setLog(T("连不上 ReFrameVideo 后端：确认启动窗口还开着，然后刷新页面。") + e.message, true);
    return;
  }
  try { S.pw = (await api("/api/preview")).w; } catch {}
  if ((info.api || 0) < 263) {                                       // 页面是新的、服务端还是旧的：提示重启
    const b = document.createElement("div");
    b.textContent = T("服务端版本过旧，请停止后重新启动 server.py（页面已更新，但服务端还在运行旧代码）");
    b.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:99;background:#c0392b;color:#fff;padding:8px 14px;font-weight:600";
    document.body.appendChild(b);
    document.body.style.setProperty("--stale", "33px");            // 不要盖住顶栏：整页往下让出这条横幅
  }
  $("#engTagText").textContent = info.engine === "mock" ? T("假引擎（只测界面）") : T("ComfyUI 引擎");
  engDot(info.engine === "mock" ? "mock" : "ok");
  $("#engineTag").classList.toggle("mock", info.engine === "mock");
  try {
    await loadProjectList();                                          // 项目列表只依赖自己的后端：先做，别被 ComfyUI 的模型列表挡住
    const last = localStorage.getItem("vps_pid");                    // 记住上次打开的项目
    if (last && last.startsWith("seq:")) await window.uiOpenSeq?.(last.slice(4));   // 上次停在长片段的分段页
    else if (last && [...$("#projectSel").options].some((o) => o.value === last)) {
      $("#projectSel").value = last;
      setProject(await api(`/api/p/${last}`));
    }
  } catch (e) {
    setLog(T("项目列表没加载出来：") + e.message, true);
  }
  afterChange();
  loadQwenModels().catch(() => {});                                 // ComfyUI 模型列表：离线/慢都不影响启动，后台慢慢取
  if (!S.d && !window.uiSeqOpen?.()) window.uiOpenProjectDialog?.();
})();

// ---------- 「模型文件」页（2026-11-18）：图片生成 / 视频生成面板 ComfyUI 按钮右边。每个模型文件（主模型 / 文本编码器 / VAE / PE /
// 看图模型）选用哪个量化版；不选 = 默认，默认不在时服务端自动用同一模型的其他版本（标「自动」）。存 app_settings.json 的 model_files ----------
let mfGroup = "image";
function mfRow(sl) {
  const sameOpts = sl.options.filter((o) => o.same), other = sl.options.filter((o) => !o.same);
  const opt = (o) => `<option value="${esc(o.name)}"${o.name === sl.chosen ? " selected" : ""}>${esc(o.name)}</option>`;
  const def = `<option value=""${sl.chosen ? "" : " selected"}>${esc(T("默认") + " · " + (sl.auto ? sl.current : sl.default))}${sl.auto ? esc(T("（自动）")) : ""}</option>`;
  const extra = sl.chosen && !sl.options.some((o) => o.name === sl.chosen) ? `<option value="${esc(sl.chosen)}" selected>${esc(sl.chosen)}</option>` : "";
  const groups = (sameOpts.length ? `<optgroup label="${esc(T("同一模型的版本"))}">${sameOpts.map(opt).join("")}</optgroup>` : "") +
    (other.length ? `<optgroup label="${esc(T("其他文件"))}">${other.map(opt).join("")}</optgroup>` : "");
  const state = sl.missing ? `<div class="mf-state bad">${esc(T("ComfyUI 上没有这个文件：") + sl.current)}</div>`
    : sl.auto ? `<div class="mf-state">${esc(T("默认文件不在，自动用了同一模型的这个版本"))}</div>` : "";
  return `<div class="mf-row" data-slot="${esc(sl.slot)}"><span title="${esc(sl.default)}">${esc(T(sl.label))}</span>` +
    `<select>${def}${extra}${groups}</select>${state}</div>`;
}
async function mfLoad() {
  $("#mfMsg").textContent = T("读取中…");
  try {
    const r = await api(`/api/model_files?group=${mfGroup}`, { timeout: 30000 });
    $("#mfBody").innerHTML = r.sections.map((sec) => `<h4>${esc(T(sec.title))}</h4>` + sec.slots.map(mfRow).join("")).join("");
    $("#mfMsg").textContent = r.error ? uiMsg(r.error) : "";
  } catch (e) { $("#mfMsg").textContent = e.message; }
}
function openModelFiles(group) {
  mfGroup = group;
  $("#mfTitle").textContent = T(group === "video" ? "模型文件 · 视频生成" : "模型文件 · 图片生成");
  $("#mfBody").innerHTML = "";
  $("#modelFilesDlg").hidden = false;
  mfLoad();
}
$("#mfBody").addEventListener("change", async (e) => {
  const row = e.target.closest(".mf-row");
  if (!row) return;
  try {
    await api("/api/model_files", { json: { slot: row.dataset.slot, file: e.target.value } });
    await mfLoad();
  } catch (err) { $("#mfMsg").textContent = err.message; }
});
$("#mfOk").onclick = () => {
  $("#modelFilesDlg").hidden = true;
  if (mfGroup === "video") { loadVlModels(); loadLoras(); }        // 主模型和「模型」下拉是同一个设置                        // 可用 / 灰掉跟着实际选的文件变
  else if (typeof loadQwenModels === "function") loadQwenModels();
};
$("#genFilesBtn").onclick = () => openModelFiles("video");
$("#imgFilesBtn").onclick = () => openModelFiles("image");
