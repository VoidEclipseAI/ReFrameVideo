// 视频 PS 前端 8/9：顶部标签、参考图标签（首帧 / 尾帧 / 资料卡）、额外参考图。
// 9 个文件是同一个全局作用域的普通脚本，按 index.html 里的顺序加载（原来是一个 app.js）。
// ---------- 改图模型选择、全部重绘、上传图片当候选 ----------

async function loadQwenModels(data) {
  data = data || await api("/api/qwen", { timeout: 8000 });
  const sel = $("#qwenSel");
  sel.hidden = !data.models.length;
  sel.innerHTML = data.models.map((m) =>
    `<option value="${esc(m.key)}"${m.available ? "" : " disabled"}${m.note ? ` title="${esc(uiMsg(m.note))}"` : ""}>${esc(m.key.startsWith("wf-") ? T("工作流 · ") + m.key.slice(3) : m.label)}${
      m.available ? "" : (m.key.startsWith("wf-") ? T("（工作流不合格）") : T("（ComfyUI 上没有）"))}</option>`).join("");   // 内置模型灰掉时悬停看缺哪个文件
  sel.title = (data.models.find((m) => m.key === data.current && m.key.startsWith("wf-")) ? T("用的是自己的 ComfyUI 工作流（workflows/image 文件夹）") : T("改图用的模型；workflows/image 文件夹里的工作流也列在这里"));
  sel.value = data.current;
  S.pes = data.pes || []; S.peKind = data.pe || "";
  fillPeSel(curImg());
}
// PE 下拉：不用 / 各个 PE 模型。「不用」记在这张图（it.pe = false）；用哪个 PE 是全局设置（换改图模型时服务端自动跟着换）
function fillPeSel(it) {
  const sel = $("#imgPe");
  const opts = S.pes && S.pes.length ? S.pes : [{ key: "on", label: T("开"), available: true }];   // 假引擎没有 PE 列表：只有开 / 不用
  sel.innerHTML = `<option value="off">${T("不用")}</option>` + opts.map((m) =>
    `<option value="${esc(m.key)}"${m.available ? "" : " disabled"}>${esc(m.label)}${m.available ? "" : T("（ComfyUI 上没有）")}</option>`).join("");
  sel.value = it && it.pe === false ? "off" : (S.pes && S.pes.length ? S.peKind : "on");
}
$("#qwenSel").onchange = async (e) => {
  try { await loadQwenModels(await api(`/api/qwen/${encodeURIComponent(e.target.value)}`, { method: "POST" })); setLog(T("改图模型已切换")); }
  catch (err) { setLog(err.message, true); }
};

function renderAll() { renderLayers(); renderVersions(); renderPano(); renderFrameProps(); renderTabs(); afterChange(); }


// ---------- 顶部标签：视频 + 这个视频的图片（首帧 / 尾帧 / 背景图 / 资料卡）----------
// 图片标签里只做一件事：Qwen 出几张候选，挑一张（✓）。合成视频时自动用上挑好的图。
const KIND_LABEL = { first: T("首帧"), last: T("尾帧"), bg: T("背景图"), card: T("资料卡"), key: T("关键帧") };
// 图片标签、视频结果都跟着版本走：只看当前版本的（没记版本的旧标签 / 旧结果每个版本都显示）
const ofVer = (v) => !v || !S.d || v === S.d.current;
// 标签可以拖动排顺序（ui/ptabs.js 的 uiReorder）：页面顺序全局一份，图片标签顺序每个项目一份，记在浏览器里（不改项目文件）
const readOrder = (k) => { try { return JSON.parse(localStorage.getItem(k) || "[]"); } catch { return []; } };
const sortByOrder = (arr, ord, key) => arr.map((x, i) => [x, ord.indexOf(key(x)), i])
  .sort((a, b) => (a[1] < 0 ? 1e6 + a[2] : a[1]) - (b[1] < 0 ? 1e6 + b[2] : b[1])).map((a) => a[0]);
const imgOrderKey = () => "vps_imgorder_" + S.pid;
const imgTabs = () => sortByOrder(((S.d && S.d.images) || []).filter((it) => it.kind === "bg" || ofVer(it.version)), readOrder(imgOrderKey()), (it) => it.id);
window.uiSetTabOrder = (bar, ids) => {                               // 拖完松手：记下新顺序，重绘标签
  try { localStorage.setItem(bar === "pages" ? "vps_page_order" : imgOrderKey(), JSON.stringify(ids.map((x) => x.replace(/^ires:/, "")))); } catch {}
  renderTabs();
};
const keyTabs = () => imgTabs().filter((t) => t.kind === "key").sort((a, b) => (a.frame - b.frame) || (a.id < b.id ? -1 : 1));   // 和后端 images.keys 同序
const imgLabel = (it) => {
  if (it.kind === "key") return T("关键帧") + " · " + FR(it.frame ?? 0);   // 可以多张：名字带帧号
  const defaults = { first: "首帧", last: "尾帧", bg: "背景图", card: "资料卡" };
  return it.label === defaults[it.kind] ? T(it.label) : it.label;
};
const curImg = () => imgTabs().find((t) => t.id === S.tab);                       // 图片编辑里正在编辑的那张
const curIres = () => S.tab.startsWith("ires:") ? imgTabs().find((t) => "ires:" + t.id === S.tab) : null;   // 图片结果里正在看的那张
const curAny = () => curImg() || curIres();
const hashStr = (t) => [...String(t || "")].reduce((h, c) => (h * 31 + c.charCodeAt(0)) | 0, 7);
const imgUrl = (it, name, w = 0) => `/api/p/${S.pid}/images/${it.id}/${name}.png?v=${hashStr(it.sig)}_${it.frame ?? ""}_${it.base_v || ""}${w ? "&w=" + w : ""}`;   // base_v：重新取画面后原图换了

// 结果标签：合成生成的视频（测试阶段不建新版本，只放这里看）。顶部只有一个「结果」标签，
// 所有结果在右边列表里切换（S.resId = 正在看的那个；没选就看最新的）。
const resTabs = () => ((S.d && S.d.results) || []).filter((r) => ofVer(r.base));
const resKey = (r) => "res:" + r.id;
const curRes = () => {
  if (S.tab !== "res") return null;
  const rs = resTabs();
  return rs.find((r) => r.id === S.resId) || rs[rs.length - 1] || null;
};

function renderTabs() {
  if (!S.d) return;
  if (S.tab === "video") S.inStill = false;                         // 回到视频编辑：S.i 又是视频的播放头
  const pn = S.d.panorama;
  if (S.tab === "orig" && !origTab()) S.tab = "video";
  if (S.tab !== "video" && S.tab !== "orig" && !curImg() && !curIres() && !curRes()) S.tab = "video";
  // 标签栏：视频 · 图片编辑（首帧 / 尾帧 / 背景图 / 资料卡，下面一行选编辑哪一张）· 图片结果（只看生成出来的图）· 原视频 · 视频结果
  if (S.tab.startsWith("ires:") && !curIres()) S.tab = "video";
  const imgs = imgTabs(), onImg = !!curImg(), onIres = !!curIres();
  if (onImg) S.lastImg = S.tab;
  if (onIres) S.lastImg = S.tab.slice(5);
  const nres = imgs.reduce((k, t) => k + (t.kind === "bg" ? (pn ? bgCards().length : 0) : t.cands.length), 0);
  const hid = hiddenPages();
  if (hid.has(pageOf(S.tab))) setPageHidden(pageOf(S.tab), false), hid.delete(pageOf(S.tab));   // 正在看的页总是显示
  const list = [{ id: "video", label: T("视频编辑"), icon: "film" }]
    .concat(imgs.length ? [{ id: "imgs", label: T("图片编辑"), icon: "image", n: imgs.length, on: onImg, del: true },
                           { id: "ires", label: T("图片结果"), icon: "images", n: nres, on: onIres, del: true }] : [])
    .concat(origTab() ? [{ id: "orig", label: T("原视频"), icon: "play", del: true }] : [])
    .concat(resTabs().length ? [{ id: "res", label: T("视频结果"), icon: "film", n: resTabs().length, res: true, del: true }] : [])
    .filter((t) => !hid.has(t.id));
  const pagesSorted = sortByOrder(list, readOrder("vps_page_order"), (t) => t.id);
  list.length = 0; list.push(...pagesSorted);
  $("#tabList").innerHTML = list.map((t) => `<div class="dtab ${t.id === S.tab || t.on ? "on" : ""}${t.res ? " res" : ""}" data-id="${esc(t.id)}" role="tab" title="${esc(imgLabel(t))}">${IC(t.icon || "dot")}<span class="dtab-lbl">${esc(imgLabel(t))}</span>${t.n ? `<span class="dtab-n">${t.n}</span>` : ""}${t.ok ? '<span class="ok">✓</span>' : ""}${t.del ? T`<span class="x" title="关掉这一页（内容不删，右边 ＋ 里可以再打开）">${IC("x")}</span>` : ""}</div>`).join("");
  $("#tabSub").hidden = !onImg && !onIres;                          // 下面一行：编辑 = 每一张（× 删除）；结果 = 每一张的结果（几张、✓ 已选用）
  const kindIcon = (k) => (k === "bg" ? "image" : k === "card" ? "rect" : "image");
  $("#tabSub").innerHTML = onImg ? imgs.map((t) => {
    const ok = t.kind === "bg" ? !!(pn && pn.filled) : !!t.pick;
    return T`<div class="dtab ${t.id === S.tab ? "on" : ""}" data-id="${esc(t.id)}" role="tab">${IC(kindIcon(t.kind))}<span class="dtab-lbl">${esc(imgLabel(t))}</span>${ok ? '<span class="ok">✓</span>' : ""}<span class="x" title="删除这个图片标签">${IC("x")}</span></div>`;
  }).join("") + T`<button type="button" class="ctab-add tab-sub-add" title="新建图片：首帧 / 尾帧 / 背景图 / 资料卡" aria-label="新建图片">${IC("plus")}</button>` : onIres ? imgs.map((t) => {
    const k = t.kind === "bg" ? (pn ? (pn.filled ? T("已补全") : T("已提取")) : "0") : t.cands.length + T(" 张");
    const ok = t.kind === "bg" ? !!(pn && pn.filled) : !!t.pick;
    return `<div class="dtab ${"ires:" + t.id === S.tab ? "on" : ""}" data-id="ires:${esc(t.id)}" role="tab">${IC(kindIcon(t.kind))}<span class="dtab-lbl">${esc(imgLabel(t))} · ${k}</span>${ok ? '<span class="ok">✓</span>' : ""}</div>`;
  }).join("") : "";
  renderGenRefs();                                                 // 视频生成栏的参考勾选跟着图片标签的「选用」变
  const it = curImg(), rs = curRes();
  const still = !rs && it && isStill(it) && !it.base_upload;   // 上传了画面的首帧 / 尾帧：和资料卡一样看图
  const orig = S.tab === "orig", ir = curIres();
  const prevTab = document.body.dataset.tab;
  document.body.dataset.tab = ir ? "ires" : orig ? "orig" : rs ? "res" : !it ? "video" : it.kind === "bg" ? "bg" : still ? "still" : "card";
  window.uiSyncRailPage?.();
  document.body.classList.toggle("img-up", !ir && !rs && !!it && isStill(it) && !!it.base_upload);   // 没有视频图层的画面（接着编辑 / 上传的图）
  if (S.shape && S.shape.view === "img" && !document.body.classList.contains("img-up")) endShape();      // 切走了：局部重绘的画笔收起
  { const IMG_EDIT = ["still", "card", "bg"];                       // 从别的页进图片编辑：自动打开「生成」组（在图片编辑里切标签不动）
    if (IMG_EDIT.includes(document.body.dataset.tab) && !IMG_EDIT.includes(prevTab)) { window.uiOpenGroup?.("gen"); autoRefreshBases(); } }
  if (window.uiResultsMode) uiResultsMode();                        // 图片结果 / 视频结果：只留「生成」组（结果列表在里面），别的组锁住
  { const host = ["ires", "card", "bg"].includes(document.body.dataset.tab) ? "#imgView" : "#stage";   // 小标签条放在看得见的那块：看大图时画布是藏着的
    if ($("#tabSub").parentElement !== $(host)) $(host).prepend($("#tabSub")); }
  syncImgCmp();                                                    // 离开图片结果时收起对比
  if (ir) return renderIres(ir);
  if (!rs && !orig) stopRes();
  if (orig) playOrig();
  if (!still) S.stillShow = null;
  if (still) enterStill(it);
  if (rs) renderRes(rs);
  else if (it) renderRefTab(it);
}

// 「原视频」标签：播放原视频（v0）整段，方便和结果对比。开没开记在浏览器里（每个项目一份）
const origKey = () => "vps_origtab_" + S.pid;
const origTab = () => { try { return localStorage.getItem(origKey()) === "1"; } catch { return false; } };
function setOrigTab(on) { try { on ? localStorage.setItem(origKey(), "1") : localStorage.removeItem(origKey()); } catch {} }
// 关掉的页（图片编辑 / 图片结果 / 视频结果）：每个项目一份，记在浏览器里
const PAGE_NAMES = { imgs: T("图片编辑"), ires: T("图片结果"), res: T("视频结果") };
const hideKey = () => "vps_hidetabs_" + S.pid;
const hiddenPages = () => { try { return new Set(JSON.parse(localStorage.getItem(hideKey()) || "[]")); } catch { return new Set(); } };
function setPageHidden(page, on) {
  const h = hiddenPages();
  on ? h.add(page) : h.delete(page);
  try { localStorage.setItem(hideKey(), JSON.stringify([...h])); } catch {}
}
const pageOf = (id) => id === "video" || id === "orig" ? id : id === "res" || id.startsWith("res:") ? "res" : id === "ires" || id.startsWith("ires:") ? "ires" : "imgs";
async function playOrig() {
  const v = $("#resVideo"), key = "orig:" + S.pid;
  $("#resCaption").textContent = T`原视频（版本 v0）· ${S.d.w}×${S.d.h}`;
  if (resShown && resShown.key === key) return;
  resShown = { key };
  try {
    const b = await (await fetch(`/api/p/${S.pid}/v/v0/clip`)).blob();
    if (!resShown || resShown.key !== key) return;
    const old = v.src;
    v.src = resShown.url = URL.createObjectURL(new Blob([b], { type: "video/mp4" }));
    if (old && old.startsWith("blob:")) URL.revokeObjectURL(old);
    v.play().catch(() => {});
  } catch (err) { setLog(T("原视频读不到：") + err.message, true); resShown = null; }
}
let resShown = null;                                               // 现在 <video> 里放的是哪个结果（blob 地址）
function stopRes() {
  const v = $("#resVideo");
  if (!v.paused) v.pause();
}
function renderResList(cur) {
  $("#resList").innerHTML = resTabs().slice().reverse().map((r) =>
    `<div class="resItem ${r.id === cur.id ? "on" : ""}" data-rid="${esc(r.id)}" title="${esc(resText(r.name))}">` +
    `<img src="/api/p/${S.pid}/results/${encodeURIComponent(r.id)}/thumb" alt="" loading="lazy">` +
    `<div class="ri-t"><b>${esc(resText(r.label))}</b><span class="sub">${esc(r.time || "")}</span></div></div>`).join("");
  $("#resCount").textContent = T`· ${resTabs().length} 条`;
}
$("#resList").addEventListener("click", (e) => {
  const t = e.target.closest(".resItem");
  if (t) { S.resId = t.dataset.rid; renderTabs(); }
});
// 视频结果的生成信息（模型、LoRA、步数、seed、参考图、提示词）+ 在文件夹中显示
const resInfoCache = new Map();
const shortModel = (s) => String(s || "").replace(/\.safetensors/g, "");
function infoRows(rows) {
  return rows.filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) =>
    `<div class="ri-row"><span>${esc(k)}</span><b title="${esc(v)}">${esc(v)}</b></div>`).join("");
}
function infoPrompt(p) {
  return p ? T`<details class="ri-prompt"><summary>提示词</summary><pre>${esc(p)}</pre></details>` : "";
}
async function renderResInfo(r) {
  const box = $("#resInfo");
  const head = `<div class="ri-head"><b>${esc(resText(r.label))}</b>` +
    T`<button type="button" class="ghostbtn ri-reveal" data-reveal="video" data-name="${esc(r.id)}" title="在资源管理器里选中这个视频文件">${IC("folder")} 在文件夹中显示</button></div>`;
  let info = resInfoCache.get(S.pid + r.id);
  if (!info) {
    box.innerHTML = head + T('<div class="sub">读取生成信息…</div>');
    try { info = await api(`/api/p/${S.pid}/results/${encodeURIComponent(r.id)}/info`); resInfoCache.set(S.pid + r.id, info); }
    catch (err) { box.innerHTML = head + `<div class="sub">${esc(err.message)}</div>`; return; }
    if (S.resId && S.resId !== r.id) return;
  }
  box.innerHTML = head + infoRows([
    [T("生成时间"), info.time || r.time], [T("内容"), resText(r.name)], [T("尺寸"), r.w && r.h ? `${r.w}×${r.h}` : ""],
    [T("帧数"), info.frames], [T("主模型"), shortModel(info.model)],
    ...(info.loras || []).map((l, i) => [i ? "" : "LoRA", shortModel(l)]),
    [T("采样"), info.steps ? `${info.sampler || ""} ${info.steps} ${T("步")}`.trim() : ""], ["Seed", info.seed],
    [T("参考图"), (info.refs || []).join(T("、")) || T("无")], [T("版本"), r.base],
  ]) + infoPrompt(info.prompt);
}
$("#resInfo").addEventListener("click", (e) => {
  const b = e.target.closest("[data-reveal]");
  if (b) revealOutput({ kind: b.dataset.reveal, name: b.dataset.name });
});
async function renderRes(r) {
  renderResList(r);
  renderResInfo(r);
  $("#resCaption").textContent = `${resText(r.label)} · ${r.w}×${r.h}`;
  if (resShown && resShown.key === resKey(r) + S.pid) return;
  const v = $("#resVideo"), key = resKey(r) + S.pid;
  resShown = { key };
  try {                                                            // 用 blob 播放：避免下载工具拦截视频地址
    const b = await (await fetch(`/api/p/${S.pid}/results/${encodeURIComponent(r.id)}/video`)).blob();
    if (!resShown || resShown.key !== key) return;
    if (resShown.url) URL.revokeObjectURL(resShown.url);
    const old = v.src;
    v.src = resShown.url = URL.createObjectURL(new Blob([b], { type: "video/mp4" }));
    if (old && old.startsWith("blob:")) URL.revokeObjectURL(old);
    v.play().catch(() => {});
  } catch (err) { setLog(T("结果视频读不到：") + err.message, true); resShown = null; }
}
async function revealOutput(input) {
  try {
    const result = await api(`/api/p/${S.pid}/reveal_output`, {json: input});
    setLog(result.path);
  } catch (err) { setLog(err.message, true); }
}

// 切到首帧 / 尾帧时提醒：还没有可用的背景图，做出来的首尾帧可能和原视频对不上，视频背景会突然变
// 「可用」和生成时一样：提取过、生成过候选、选中的那张在（画幅改过导致旧背景对不上也算没有）。
// 「不再提示」记在浏览器里，每个项目一份（不改项目文件）。
const bgWarnKey = () => "vps_bgwarn_" + S.pid;
function panoUsable() {
  const pn = S.d && S.d.panorama;
  if (!pn || !pn.filled || !pn.use) return false;
  return !(typeof panoFrameStale === "function" && panoFrameStale(pn));
}
function maybeWarnStillBg() {
  if (!S.d || !S.pid) return;
  const it = curImg();
  if (!it || !isStill(it)) return;
  if (panoUsable()) return;
  try { if (localStorage.getItem(bgWarnKey()) === "1") return; } catch {}
  $("#bgWarnNoMore").checked = false;
  $("#bgWarnDlg").hidden = false;
}
function closeBgWarn() {
  try { if ($("#bgWarnNoMore").checked) localStorage.setItem(bgWarnKey(), "1"); } catch {}
  $("#bgWarnDlg").hidden = true;
}
$("#bgWarnOk").onclick = closeBgWarn;
$("#bgWarnGo").onclick = async () => {
  closeBgWarn();
  const bg = imgTabs().find((t) => t.kind === "bg");
  if (bg) return showTab(bg.id);
  try {                                                             // 还没有背景图标签：新建一个（和「＋」菜单里的背景图同一条路）
    const res = await api(`/api/p/${S.pid}/images`, { json: { kind: "bg" } });
    if (res.pending) await watchJob();
    else setProject(await api(`/api/p/${S.pid}`), true);
    const it = imgTabs().find((t) => t.kind === "bg");
    if (it) showTab(it.id);
  } catch (err) { setLog(err.message, true); }
};

function showTab(id) {
  setPageHidden(pageOf(id), false);                                // 打开的页（包括有新结果时自动打开）不再隐藏
  if (id.startsWith("res:")) { S.resId = id.slice(4); id = "res"; }   // 某个结果：切到「结果」标签并选中它
  if (id === "imgs" || id === "ires") {                            // 图片编辑 / 图片结果：回到上次看的那张，没有就第一张
    const t = imgTabs().find((x) => x.id === S.lastImg) || imgTabs()[0];
    if (!t) return;
    id = (id === "ires" ? "ires:" : "") + t.id;
  }
  S.tab = id;
  $("#tabMenu").hidden = true;
  renderTabs();
  maybeWarnStillBg();                                              // 首帧 / 尾帧：没有可用背景图就提醒（见上）
  if (id === "video") requestAnimationFrame(draw);
}
for (const bar of ["#tabList", "#tabSub"]) $(bar).addEventListener("click", async (e) => {
  const t = e.target.closest(".dtab");
  if (!t) return;
  if (!e.target.closest(".x")) return showTab(t.dataset.id);
  if (t.dataset.id === "orig") { setOrigTab(false); resShown = null; stopRes(); return showTab("video"); }   // 关掉原视频标签
  if (PAGE_NAMES[t.dataset.id]) {                                  // 关掉图片编辑 / 图片结果 / 视频结果这一页（内容不删）
    const on = pageOf(S.tab) === t.dataset.id;
    setPageHidden(t.dataset.id, true);
    if (on) { S.tab = "video"; resShown = null; stopRes(); requestAnimationFrame(draw); }
    return renderTabs();
  }
  const it = imgTabs().find((x) => x.id === t.dataset.id);          // 标签右边的 ×：删除这个图片标签
  if (!it || !(await uiConfirm(T`删除「${imgLabel(it)}」标签和它的所有候选？`, { ok: T("删除"), danger: true }))) return;
  try {
    setProject(await api(`/api/p/${S.pid}/images/${it.id}`, { method: "DELETE" }), true);
    if (S.tab === it.id) showTab("video"); else renderTabs();
  } catch (err) { setLog(err.message, true); }
});

const keyFrameNow = () => Math.max(0, Math.min(S.d.n - 1, (S.tab === "video" || !S.inStill ? S.i : S.vidI ?? S.i) | 0));   // 新建关键帧取的那一帧
function openTabMenu(e, anchor, imagesOnly) {
  e.stopPropagation();
  if (!S.d) return;
  const m = $("#tabMenu");
  if (!m.hidden && m._anchor === anchor) { m.hidden = true; return; }
  m._anchor = anchor;
  const has = (kind, oid) => imgTabs().find((t) => t.kind === kind && (kind !== "card" || t.oid === oid));
  const row = (kind, oid, label, note) =>
    `<div data-kind="${kind}" data-oid="${esc(oid || "")}">${esc(label)}<span class="note">${esc(has(kind, oid) ? T("已有，切换过去") : note)}</span></div>`;
  const hid = hiddenPages();
  // 关掉的页总是列出来（图片标签和视频结果跟着版本走：这个版本还没有时灰着，原来干脆不列，用户以为打不开了）
  const pages = Object.entries(PAGE_NAMES).filter(([k]) => hid.has(k)).map(([k, v]) => (k === "res" ? resTabs().length : imgTabs().length)
    ? T`<div data-page="${k}">${esc(v)}<span class="note">← 再打开这一页</span></div>`
    : T`<div class="off" data-page-empty="${k}">${esc(v)}<span class="note">这个版本还没有</span></div>`).join("");
  let h = (imagesOnly ? "" : pages + T`<div data-kind="orig">原视频<span class="note">${origTab() ? T("已有，切换过去") : T("← 播放原视频，方便对比")}</span></div>` + '<div class="sep"></div>') +
          row("first", "", T("首帧"), T("← 按现在的图层摆放取第 0 帧")) + row("last", "", T("尾帧"), T("← 取最后一帧")) +
          row("bg", "", T("背景图"), T("← 背景全景图")) +
          `<div data-kind="key">${T("关键帧")}<span class="note">${T("← 取视频编辑播放头这一帧")} · ${FR(keyFrameNow())}</span></div>`;
  const layers = objs();
  h += '<div class="sep"></div>' + (layers.length
    ? layers.map((o) => row("card", o.id, T("资料卡 · ") + o.name, T("← 抠出这个人做三视图"))).join("")
    : T('<div class="note">资料卡：先识别出主体</div>'));
  m.innerHTML = h;
  const host = $("#docTabs").getBoundingClientRect(), r = anchor.getBoundingClientRect();
  m.style.left = Math.max(0, Math.min(r.left - host.left - 4, host.width - 240)) + "px";
  m.style.top = r.bottom - host.top + 4 + "px";
  m.hidden = false;
}
$("#tabAdd").onclick = (e) => openTabMenu(e, $("#tabAdd"), false);
$("#tabSub").addEventListener("click", (e) => { const b = e.target.closest(".tab-sub-add"); if (b) openTabMenu(e, b, true); });
$("#tabMenu").addEventListener("click", async (e) => {
  const pg = e.target.closest("[data-page]");
  if (pg) { $("#tabMenu").hidden = true; return showTab(pg.dataset.page); }
  const r = e.target.closest("[data-kind]");
  if (!r) return;
  $("#tabMenu").hidden = true;
  const kind = r.dataset.kind, oid = r.dataset.oid || null;
  if (kind === "orig") { setOrigTab(true); return showTab("orig"); }
  if (kind === "key") {                                            // 关键帧：可以多张，每次新建（帧号 = 视频编辑播放头）
    const before = new Set(imgTabs().map((t) => t.id));
    try {
      const res = await api(`/api/p/${S.pid}/images`, { json: { kind, frame: keyFrameNow() } });
      if (res.pending) await watchJob();
      setProject(await api(`/api/p/${S.pid}`), true);
      const it = imgTabs().find((t) => t.kind === "key" && !before.has(t.id));
      if (it) showTab(it.id);
    } catch (err) { setLog(err.message, true); }
    return;
  }
  const find = () => imgTabs().find((t) => t.kind === kind && (kind !== "card" || t.oid === oid));
  if (find()) return showTab(find().id);
  try {
    const res = await api(`/api/p/${S.pid}/images`, { json: { kind, oid } });
    if (res.pending) await watchJob();
    else setProject(await api(`/api/p/${S.pid}`), true);
    const it = find();
    if (it) showTab(it.id);
  } catch (err) { setLog(err.message, true); }
});
addEventListener("pointerdown", (e) => { if (!e.target.closest("#tabMenu") && !e.target.closest("#tabAdd") && !e.target.closest(".tab-sub-add")) $("#tabMenu").hidden = true; }, true);

function showMain(src, caption) {
  if ($("#imgMain").getAttribute("src") !== src) $("#imgMain").src = src;
  $("#imgCaption").textContent = caption;
  if (typeof repaintOv === "function") repaintOv(curImg());
  syncImgCmp();
}

// 图片结果：和输入图对比（2026-11-15 用户要）。左 = 交给模型的输入图（首帧 / 尾帧 / 资料卡 = 这张标签的「原图」base，背景图 = 提取的底图），
// 右 = 大图现在显示的这张（选用的 / 鼠标停着的候选）；拖中间竖线，或开着对比时在图上按住拖
let imgCmpOn = false, imgCmpX = 0.5;
const imgCmpSrc = (it) => !it ? "" : it.kind === "bg" ? panoSrc("plate", 0) : imgUrl(it, "base");
function syncImgCmp() {
  const ires = document.body.dataset.tab === "ires", it = curIres();
  $("#imgCmpBtn").hidden = !ires;
  $("#imgCmpBtn").classList.toggle("on", imgCmpOn);
  const on = imgCmpOn && ires && !!it && !!$("#imgMain").getAttribute("src") && $("#imgMain").naturalWidth > 0;
  document.body.classList.toggle("img-cmp", on);
  $("#imgCmp").hidden = $("#imgCmpLine").hidden = $("#imgCmpTags").hidden = !on;
  if (!on) return;
  const src = imgCmpSrc(it);
  if ($("#imgCmp").getAttribute("src") !== src) $("#imgCmp").src = src;
  const box = $("#imgBox").getBoundingClientRect(), r = $("#imgMain").getBoundingClientRect();
  const rect = { left: r.left - box.left + "px", top: r.top - box.top + "px", width: r.width + "px", height: r.height + "px" };
  Object.assign($("#imgCmp").style, rect, { clipPath: `inset(0 ${(1 - imgCmpX) * 100}% 0 0)` });
  Object.assign($("#imgCmpTags").style, rect);
  Object.assign($("#imgCmpLine").style, { left: r.left - box.left + r.width * imgCmpX + "px", top: rect.top, height: rect.height });
}
$("#imgCmpBtn").addEventListener("click", (e) => { e.stopPropagation(); imgCmpOn = !imgCmpOn; syncImgCmp(); });
$("#imgMain").addEventListener("load", syncImgCmp);
new ResizeObserver(() => syncImgCmp()).observe($("#imgBox"));          // 右栏拖宽、标签条换位置等：重新对齐
new ResizeObserver(() => syncImgCmp()).observe($("#imgMain"));
$("#imgBox").addEventListener("pointerdown", (e) => {               // 拖竖线（开着对比时在图上任何地方按下都行）
  if (!document.body.classList.contains("img-cmp") || e.button !== 0 || e.target.closest("#imgCmpBtn")) return;
  e.preventDefault();
  const move = (ev) => {
    const r = $("#imgMain").getBoundingClientRect();
    imgCmpX = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width));
    syncImgCmp();
  };
  move(e);
  const up = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", up); };
  addEventListener("pointermove", move); addEventListener("pointerup", up);
});

// 首帧 / 尾帧标签 = 视频编辑停在这一帧：同一个画布、图层、蒙版面板；叠加层 = 这张图的补图范围
function enterStill(it) {
  if (S.playing) togglePlay();
  if (!S.inStill) { S.vidI = S.i; S.inStill = true; }              // 进首帧 / 尾帧 / 关键帧会改 S.i：先记下视频编辑的播放头（新建关键帧取这一帧）
  const i = it.kind === "key" ? Math.min(it.frame ?? 0, S.d.n - 1) : it.frame ?? (it.kind === "first" ? 0 : S.d.n - 1);
  if (S.i !== i) S.i = i;
  renderProps();
  requestAnimationFrame(draw);
}

// 图片生成面板（四种参考图共用）：首帧 / 尾帧 / 资料卡出候选；背景图拼全景
const IMG_OPTS = [["imgClose", "img_close", 0], ["imgGrow", "img_grow", 0], ["imgEdge", "img_edge", 0], ["imgMgrow", "img_mgrow", 0]];   // 默认都 0；「推荐 N」按钮在 index.html
async function renderRefTab(it) {
  setTimeout(syncImgEngine, 0);                                  // ComfyUI / API 两条路：面板显示哪些控件
  $("#rgKind").textContent = "· " + imgLabel(it);
  $("#imgKeyRow").hidden = it.kind !== "key";
  if (it.kind === "key") {
    if (document.activeElement !== $("#imgKeyFrame")) $("#imgKeyFrame").value = it.frame ?? 0;
    $("#imgKeyFrame").max = S.d.n - 1;
    $("#imgKeySec").textContent = `${((it.frame ?? 0) / S.d.fps).toFixed(1)} s / ${S.d.n - 1}`;
  }
  if (typeof placeCommonRows === "function") placeCommonRows();     // 图片模型 / seed 两行：背景图进第二步，其它标签回顶部
  $("#imgResN").textContent = it.kind === "bg" ? bgCards().length : it.cands ? it.cands.length : 0;
  $("#imgBaseReset").hidden = !(it.base_upload && isStill(it)) || !!it.base_from;   // 接着编辑的候选图：不能改回视频画面
  { const up = isStill(it) && !!it.base_upload;   // 局部重绘：只在没有视频图层的画面上（接着编辑的候选图 / 上传的图）
    $("#imgRepaintRow").hidden = !up;
    $("#imgRepaintInfo").textContent = it.repaint ? T`已画 ${it.repaint}%：只重绘这块` : T("没画：整张按提示词改");
    $("#imgRepaintClear").hidden = !it.repaint;
    $("#imgFeatherRow").hidden = !(up && it.repaint) && !(isStill(it) && it.mode === "inpaint");
    const fe = it.repaint_feather ?? 16; $("#imgFeather").value = fe; $("#imgFeatherVal").textContent = fe ? fe + " px" : T("硬边"); }
  $("#imgBaseUp").innerHTML = IC("upload") + (it.base_upload ? T("换一张上传的图") : T("上传图片来编辑"));
  if (it.kind === "bg") { $("#refBlock").hidden = true; renderPano(); renderBgMain(); return; }   // 背景图没有「原图 / 背景参考」这两行
  $("#imgGen").hidden = false;
  if ($("#imgDesc").dataset.for !== it.id) {                         // 换了标签才重新载入（正在写的不被刷掉）
    $("#imgDesc").value = it.desc || ""; $("#imgSys").value = sysOf(it); $("#imgDesc").dataset.for = it.id;
  }
  const still = isStill(it), pano = !!S.d.panorama;
  $$("#imgModeSw [data-mode]").forEach((b) => b.classList.toggle("on", b.dataset.mode === (it.mode === "inpaint" ? "inpaint" : "gen")));
  fillPeSel(it);
  $("#imgBody").checked = !!it.body;
  $("#imgBodyPartsRow").hidden = !it.body;                         // 没勾「补全被截断的人物」时「重点部位」没用，不显示
  $("#imgBodyParts").value = it.body_parts || "";
  $("#imgPeBox").hidden = !it.pe_prompt;
  $("#imgPeText").textContent = it.pe_prompt || "";
  IMG_OPTS.forEach(([id, k, rec]) => {                             // 补图范围（蒙版面板里那一组）：默认 0，「推荐」按钮给推荐值
    const v = it[k] ?? rec;
    if (document.activeElement !== $("#" + id)) $("#" + id).value = v;
    $(`#${id}Val`).textContent = v + " px";
  });
  $("#imgOcc").checked = !!it.img_occ;                             // 空的地方灰 128 或预涂背景（旧标签的「透明」当灰）
  document.querySelectorAll('input[name="imgFill"]').forEach((r) => { r.checked = r.value === (it.fill === "bg" ? "bg" : "grey"); });
  $("#bgLibrary").hidden = !still;
  $("#imgAddFrame").hidden = it.kind !== "card";
  $("#imgAddTab").hidden = !imgTabs().some(t => t.id !== it.id && t.kind !== "bg");
  $("#bgLibrary").innerHTML = IC("image") + ((pano || it.bg_local) && it.use_bg !== false ? T("换背景图") : T("选背景图"));
  if ($("#imgTabPick").dataset.owner !== it.id) {
    $("#imgTabPick").hidden = true;
    $("#imgTabPick").dataset.owner = it.id;
  }
  // 参考图清单（07d-reflist.js）：① 原图（这一帧的画面，固定）→ ② 背景参考（可选）→ 额外参考图
  const bgOn = still && (pano || it.bg_choice === "local") && it.use_bg !== false;
  const bgName = it.bg_choice === "local" ? "bg_local" : "bg";
  const bgQ = `&b=${esc(it.bg_local || "")}_${esc(S.d.panorama?.v || "")}_${esc(it.bg_choice || "default")}`;
  $("#imgRefs").innerHTML = refListHTML([
    { key: "base", n: 1, role: still ? T("原图（这一帧的画面）") : T("原图"), src: it.base_from && it.cands.includes(it.base_from) ? T`接着编辑：候选 #${it.cands.indexOf(it.base_from) + 1}`
        : still ? (it.base_upload ? T("上传的图") : (uiLanguage === "en" ? `Video frame ${it.frame ?? 0}, laid out with the current edits` : `视频第 ${it.frame ?? 0} 帧，按现在的编辑摆好`)) : T("资料卡要抠的画面"),
      thumb: imgUrl(it, "base", 240), big: imgUrl(it, "base", 0), fixed: true, refresh: still && !it.base_upload, more: true },
    ...(bgOn ? [{ key: bgName, n: 2, role: T("背景图"), src: it.bg_choice === "local" ? T("这张图自己上传的背景") : T("全景图变换到这一帧"),
                  thumb: imgUrl(it, bgName, 240) + bgQ, big: imgUrl(it, bgName, 0) + bgQ, change: true }] : []),
  ]);
  $("#refBlock").hidden = false;
  const stale = new Set((S.staleCands || {})[it.id] || []);
  const wait = S.genWait && S.genWait.id === it.id ? Math.max(0, S.genWait.total - (it.cands.length - S.genWait.from)) : 0;
  $("#imgCands").innerHTML = it.cands.slice().reverse().map((c) => {         // 最新的在最上面
    const k = it.cands.indexOf(c), on = it.pick === c, old = stale.has(c);
    const ci = (it.cand_info || {})[c] || {}, seed = ci.seed ?? (it.cand_seed || {})[c];
    const sub = [ci.upload ? T("上传的图") : "", ci.time ? ci.time.slice(5) : "", seed != null ? "seed " + seed : ""].filter(Boolean).join(" · ");
    return `<div class="rc ${on ? "on" : ""} ${old ? "stale" : ""}" data-name="${c}" data-pick="${c}"><img src="${imgUrl(it, c, 240)}">` +
      `<span class="rc-t"><b class="num">#${k + 1}</b><small>${esc(sub)}</small></span>` +
      `${old ? T('<span class="stalebtn" title="出这张之后视频里的图层又改过了，可能和现在的画面对不上">过期</span>') : ""}` +
      `<span class="pickbtn">${on ? T("✓ 已选用") : T("选用")}</span>` +
      (isStill(it) ? T`<button type="button" class="rc-edit" data-continue="${c}" title="用这张接着编辑：回到图片编辑，这张图成为新画面（不再带视频图层）">${IC("pencil")}</button>` : "") + `</div>`;
  }).join("");
  if (wait) $("#imgCands").insertAdjacentHTML("afterbegin", T`<div class="rc wait">生成中… 还有 ${wait} 张</div>`);
  if (!it.cands.length && !wait) $("#imgCands").innerHTML = T('<div class="empty">还没有候选：点「生成候选」</div>');
  renderExtra(it);
  showPicked(it);
  if (still) {
    const { fresh, stale_cands, base_fresh } = await api(`/api/p/${S.pid}/images_sig`);
    S.staleCands = stale_cands || {};
    if (curImg() !== it) return;
    const bad = it.pick && (fresh || {})[it.id] === false;
    const rb = $("#imgRefs .rr-refresh");                           // 画面不是按现在的编辑取的：刷新按钮标出来
    if (rb) { const old = (base_fresh || {})[it.id] === false; rb.classList.toggle("warn", old); rb.title = old ? T("视频里的图层改过了，这张画面是旧的：点一下按现在的编辑重新取（已选的候选保留）") : T("按视频里现在的编辑重新取这一帧的画面（已选的候选保留）"); }
    $("#imgStaleNote").hidden = !bad;
    $("#imgStaleNote").textContent = bad ? T("选用的这张是按旧的图层摆放出的，和现在的画面对不上：重新生成候选，或改回图层") : "";
    const now = new Set(S.staleCands[it.id] || []);
    $$("#imgCands .rc[data-name]").forEach((el) => el.classList.toggle("stale", now.has(el.dataset.name)));
  } else $("#imgStaleNote").hidden = true;
}
// ---------- 额外参考图（Picture 3、4…；首帧 / 尾帧有背景参考时它是 Picture 2）----------
const xFirst = (it) => 2 + (isStill(it) && (S.d.panorama || it.bg_choice === "local") && it.use_bg !== false ? 1 : 0);
const xNote = (r, n) => (r.note || "").split("{n}").join(n);                      // 显示：占位符换成实际图号
const xSave = (txt, n) => txt.split(`Picture ${n}`).join("Picture {n}");            // 保存：图号换回占位符（删了前面的图号会自动顺延）
function renderExtra(it) {
  const rs = it.refs || [], n0 = xFirst(it);
  const srcName = (r) => r.src === "frame" ? (uiLanguage === "en" ? `Video frame ${r.frame}` : `视频第 ${r.frame} 帧`) : r.src === "tab" ? T`来自「${r.label ? imgLabel(r) : T("标签")}」` : T("上传的图片");
  $("#imgExtra").innerHTML = refListHTML(rs.map((r, k) => ({ key: "ref_" + r.id, n: n0 + k, role: T("参考图"), src: srcName(r),
    thumb: imgUrl(it, "ref_" + r.id, 240), big: imgUrl(it, "ref_" + r.id, 0), change: true, note: xNote(r, n0 + k), noteN: n0 + k })));
}
// 提示词里 @ 能引用的图（07e-mention.js）：原图、背景图、额外参考图。all = 全部可能的（发请求时认名字用）
function imgMentionItems(all) {
  const it = curImg();
  if (!it) return [];
  const thumb = (k) => ($(`#imgRefs .rr[data-key="${k}"] img, #imgExtra .rr[data-key="${k}"] img`) || {}).src || "";
  const rows = [{ ...mentionName("原图"), key: "base", thumb: thumb("base") }];
  const bg = it.bg_choice === "local" ? "bg_local" : "bg";
  if (all || $(`#imgRefs .rr[data-key="${bg}"]`)) rows.push({ ...mentionName("背景图"), key: bg, thumb: thumb(bg) });
  (it.refs || []).forEach((r, k) => rows.push({ name: (uiLanguage === "en" ? "ref" : "参考图") + (k + 1),
    alias: uiLanguage === "en" ? "参考图" + (k + 1) : "", key: "ref_" + r.id, thumb: thumb("ref_" + r.id) }));
  return uniqueNames(rows);
}
for (const id of ["#imgDesc", "#imgSys", "#imgApiPrompt"]) mentionAttach($(id), () => imgMentionItems(false));
const xDone = (r) => { const it = curImg(); if (it && r && r.id === it.id) { Object.assign(it, r); renderExtra(it); } };
const ridOf = (el) => el.closest(".rr").dataset.key.slice(4);       // 行 key「ref_<id>」→ 额外参考图的 id
$("#imgExtra").addEventListener("click", async (e) => {
  if (refZoom(e)) return;
  const b = e.target.closest("[data-act]"), it = curImg();
  if (!b || !it) return;
  const rid = ridOf(b);
  if (b.dataset.act === "change") {                                // 换图：上传一张替换这张（顺序和说明不变）
    const [f] = await pickImageFile();
    if (!f) return;
    try { xDone(await api(`/api/p/${S.pid}/images/${it.id}/refs?src=upload&replace=${rid}`, { method: "POST", body: f })); }
    catch (err) { setLog(err.message, true); }
    return;
  }
  if (b.dataset.act === "del") {
    try { xDone(await api(`/api/p/${S.pid}/images/${it.id}/refs/${rid}`, { method: "DELETE" })); }
    catch (err) { setLog(err.message, true); }
  }
});
$("#imgExtra").addEventListener("change", async (e) => {
  const ta = e.target.closest("textarea"), it = curImg();
  if (!ta || !it) return;
  try { Object.assign(it, await api(`/api/p/${S.pid}/images/${it.id}/refs/${ridOf(ta)}`,
                                    { json: { note: xSave(ta.value, ta.dataset.n) } })); setLog(T("参考图说明已保存")); }
  catch (err) { setLog(err.message, true); }
});
$("#imgRefs").addEventListener("click", (e) => {
  if (refZoom(e)) return;
  const b = e.target.closest("[data-act]");
  if (!b) return;
  if (b.dataset.act === "refresh") return $("#imgBaseReset").click();  // 原图「刷新」：按现在的编辑重新取画面（候选保留）
  if (b.dataset.act === "more") {                                  // 原图 ⋯：换成上传的图 / 改回视频画面
    const it = curImg(), still = it && isStill(it);
    return openRefMenu(b, [
      { label: it && it.base_upload ? T("换一张上传的图…") : T("换成上传的图…"), hint: T("代替从视频取的画面"), run: () => $("#imgBaseUp").click() },
      ...(still && !it.base_from ? [{ label: T("改回视频画面"), hint: it.base_upload ? T("按现在的编辑重新取") : T("现在就是视频画面"), disabled: !it.base_upload, run: () => $("#imgBaseReset").click() }] : []),
    ]);
  }
  if (b.dataset.act === "change") $("#bgLibrary").click();          // 背景图「换 ▾」：打开背景图挑选格子
  if (b.dataset.act === "del") chooseBackground("off");
});
// 「+ 添加」：背景图（首帧 / 尾帧）/ 上传图片 / 从项目里选图 / 视频当前帧（资料卡）
$("#imgRefAdd").dataset.refmenu = "img:add";
$("#imgRefAdd").onclick = () => {
  const it = curImg();
  if (!it) return;
  const still = isStill(it), pano = !!S.d.panorama;
  const bgOn = still && (pano || it.bg_choice === "local") && it.use_bg !== false;
  const items = [];
  if (still && !bgOn) items.push({ label: T("背景图"), hint: pano ? T("从背景候选里挑") : T("挑 / 上传一张背景"), run: () => $("#bgLibrary").click() });
  items.push({ label: T("上传图片…"), hint: T("可多选"), run: () => $("#imgAddUp").click() });
  if (imgTabs().some((t) => t.id !== it.id && t.kind !== "bg")) items.push({ label: T("从项目里选图"), hint: T("别的图片标签里的图"), run: () => $("#imgAddTab").click() });
  if (it.kind === "card") items.push({ label: T("视频当前帧"), hint: FR(S.i), run: () => $("#imgAddFrame").click() });
  openRefMenu($("#imgRefAdd"), items);
};
// 挑图浮层（背景图候选 / 项目里的图）：贴在参考图「+ 添加」下面弹出，不放在面板底部（那里会被任务监控盖住）
function showPick(box) {
  box.hidden = false;
  const a = ($("#imgRefAdd").offsetParent ? $("#imgRefAdd") : $("#refGenPanel")).getBoundingClientRect();
  const w = Math.min(380, innerWidth - 16);
  box.style.width = w + "px";
  box.style.left = Math.max(8, Math.min(a.right - w, innerWidth - w - 8)) + "px";
  const h = box.offsetHeight;
  box.style.top = (a.bottom + 4 + h <= innerHeight - 8 ? a.bottom + 4 : Math.max(8, a.top - h - 4)) + "px";
}
$("#bgLibrary").onclick = () => {
  const box = $("#imgTabPick"), it = curImg(), pn = S.d.panorama || {};
  if (!box.hidden && box.dataset.source === "bg") { box.hidden = true; return; }
  const choices = (pn.candidates || []).map((name,i) => [name, T("候选 ") + (i+1)]);
  if (pn.uploaded) choices.push(["upload",T("项目上传")]);
  if (!pn.mode && pn.filled) choices.push(["qwen_full",T("Qwen 整图")],["filled",T("补洞图")]);
  if (it.bg_local) choices.push(["local",T("本图上传")]);
  box.dataset.source = "bg";
  box.querySelector(".cands").innerHTML = choices.map(([name,label]) => {
    const url = name === "local" ? imgUrl(it,"bg_local",240) + "&b=" + it.bg_local :
      `/api/p/${S.pid}/panorama/${name}.png?v=${pn.v}&w=240`;
    const active = it.use_bg !== false && (it.bg_choice || pn.use || "qwen_full") === name;
    return `<button type="button" data-bg="${esc(name)}" aria-pressed="${active}"><img src="${url}" alt=""><span>${active ? "✓ " : ""}${label}</span></button>`;
  }).join("") + T('<button type="button" data-bg="upload_local">＋ 上传背景图</button>');
  showPick(box);
};
async function chooseBackground(choice) {
  const it = curImg();
  if (!it) return;
  $("#imgTabPick").hidden = true;
  if (choice === "upload_local") {
    renderRefTab(it);
    const input = document.createElement("input");
    input.type = "file"; input.accept = "image/*";
    const url = `/api/p/${S.pid}/images/${it.id}/background_upload`;
    input.onchange = async () => {
      if (!input.files[0]) return;
      try { Object.assign(it, await api(url, {method:"POST", body:input.files[0]})); }
      catch (err) { setLog(err.message, true); }
      if (curImg() === it) renderRefTab(it);
    };
    input.click();
    return;
  }
  const use_bg = choice !== "off";
  const bg_choice = use_bg ? choice : it.bg_choice || "";
  $("#bgLibrary").disabled = true;
  const generate = $("#imgGen");
  if (generate) generate.disabled = true;
  try {
    await api(`/api/p/${S.pid}/images/${it.id}/gen_opts`, { json: { use_bg, bg_choice } });
    Object.assign(it, {use_bg, bg_choice});
  } catch (err) { setLog(err.message, true); }
  $("#bgLibrary").disabled = false;
  if (generate) generate.disabled = false;
  if (curImg() === it) renderRefTab(it);
};
$("#imgAddUp").onclick = () => $("#imgUpFile").click();
$("#imgUpFile").onchange = async () => {
  const it = curImg(), files = [...$("#imgUpFile").files];
  $("#imgUpFile").value = "";
  if (!it) return;
  for (const f of files) {
    try { xDone(await api(`/api/p/${S.pid}/images/${it.id}/refs?src=upload`, { method: "POST", body: f })); }
    catch (err) { setLog(`${f.name}：${err.message}`, true); }
  }
};
$("#imgAddFrame").onclick = async () => {
  const it = curImg();
  if (!it) return;
  try { xDone(await api(`/api/p/${S.pid}/images/${it.id}/refs?src=frame&i=${S.i}`, { method: "POST" })); setLog(uiLanguage === "en" ? `Added frame ${S.i}` : `已加入第 ${S.i} 帧`); }
  catch (err) { setLog(err.message, true); }
};
$("#imgAddTab").onclick = () => {                                   // 别的图片标签里的图：挑中的排最前，再是候选和原图
  const it = curImg(), box = $("#imgTabPick");
  if (!it) return;
  if (!box.hidden && box.dataset.source === "project") { box.hidden = true; return; }
  box.dataset.source = "project";
  const cells = [];
  for (const t of imgTabs()) {
    if (t.id === it.id || t.kind === "bg") continue;
    const names = [...(t.pick ? [t.pick] : []), ...t.cands.filter((c) => c !== t.pick), "base"];
    for (const n of names)
      cells.push(`<div data-from="${t.id}" data-n="${n}"><img src="${imgUrl(t, n, 240)}">` +
                 `<span class="cap">${esc(imgLabel(t))} · ${n === "base" ? T("原图") : (n === t.pick ? T("已选 ") : "") + "#" + (t.cands.indexOf(n) + 1)}</span></div>`);
  }
  box.querySelector(".cands").innerHTML = cells.join("") || T('<div class="empty">别的标签里还没有图</div>');
  showPick(box);
};
$("#imgTabPick").addEventListener("click", async (e) => {
  const bg = e.target.closest("[data-bg]");
  if (bg) { await chooseBackground(bg.dataset.bg); return; }
  const c = e.target.closest("[data-from]"), it = curImg();
  if (!c || !it) return;
  $("#imgTabPick").hidden = true;
  try { xDone(await api(`/api/p/${S.pid}/images/${it.id}/refs?src=tab&from_iid=${c.dataset.from}&name=${c.dataset.n}`, { method: "POST" })); }
  catch (err) { setLog(err.message, true); }
});

function showPicked(it) {
  const still = isStill(it);
  if (curImg() && still && !it.base_upload) { if (S.stillShow) { S.stillShow = null; draw(); } return; }
  if (curImg() && still) {                                         // 接着编辑的候选图 / 上传的图：大图 = 这张画面（局部重绘画在它上面）
    showMain(imgUrl(it, "base"), it.base_from && it.cands.includes(it.base_from) ? T`画面：接着编辑候选 #${it.cands.indexOf(it.base_from) + 1}` : T("画面：上传的图"));
    return repaintOv(it);
  }
  const name = it.pick || "base";
  showMain(imgUrl(it, name), it.pick ? T`已选 #${it.cands.indexOf(it.pick) + 1}` : T("原图 · 未选"));
}
function repaintOv(it) {                                           // 局部重绘范围叠在大图上（画范围时不显示）
  const ov = $("#imgRepaintOv"), on = !!(it && it.repaint && !S.shape && $("#imgMain").getAttribute("src") === imgUrl(it, "base"));
  ov.hidden = !on;
  if (on) { const u = `/api/p/${S.pid}/images/${it.id}/repaint.png?overlay=1&v=${it.repaint}_${it.base_v || ""}`; if (ov.getAttribute("src") !== u) ov.src = u; }
}
$("#imgRepaintDraw").onclick = (e) => { e.stopPropagation(); const it = curImg(); if (it && it.base_upload) { showPicked(it); openShapeMenu(e.currentTarget, ["repaint"]); } };
$("#imgFeather").oninput = () => { const v = +$("#imgFeather").value; $("#imgFeatherVal").textContent = v ? v + " px" : T("硬边"); };
$("#imgFeather").onchange = () => { const it = curImg(); if (it) saveGenOpts(it, { repaint_feather: +$("#imgFeather").value }); };
$("#imgRepaintClear").onclick = async () => {
  const it = curImg();
  if (!it) return;
  try { delete it.repaint; Object.assign(it, await api(`/api/p/${S.pid}/images/${it.id}/repaint`, { method: "DELETE" })); renderTabs(); setLog(T`${it.label}：清除了局部重绘范围`); }
  catch (err) { setLog(err.message, true); }
};
for (const id of ["#imgRefs", "#imgCands", "#imgExtra"]) $(id).addEventListener("mouseover", (e) => {
  const c = e.target.closest("[data-name]"), it = curAny();
  if (!c || !it || e.target.closest("textarea, button")) return;
  const n = c.dataset.name;
  if (it.kind === "bg") { showMain(panoSrc(n, 0), (bgCards().find((x) => x.name === n) || {}).label || T("背景图")); return; }
  if (curImg() && isStill(it) && !it.base_upload && n !== "bg" && !n.startsWith("ref_")) {   // 编辑首帧 / 尾帧时：候选直接显示在画幅里
    S.stillShow = imgUrl(it, n);
    return draw();
  }
  if (n.startsWith("ref_")) return showMain(imgUrl(it, n), T("额外参考图"));
  showMain(imgUrl(it, n) + (n === "bg" ? `&b=${esc(it.bg_local || "")}_${esc(S.d.panorama?.v || "")}_${esc(it.bg_choice || "default")}` : ""),
           n === "base" ? T("原图") : n === "bg" ? T("背景参考（全景图变换到这一帧，人都不在时的样子）") : T`候选 #${it.cands.indexOf(n) + 1}`);
});
for (const id of ["#imgRefs", "#imgCands", "#imgExtra"]) $(id).addEventListener("mouseleave", () => {
  const it = curAny();
  if (!it) return;
  if (it.kind === "bg") { renderBgMain(); return; }               // 背景图：缩回现在选用的那张
  showPicked(it);
});
$("#imgCands").addEventListener("click", async (e) => {            // 点一张 = 选用（同一标签只能选一张），再点已选的那张 = 取消
  const card = e.target.closest(".rc[data-pick]"), it = curAny();
  if (!card || !it) return;
  const ed = e.target.closest("[data-continue]");
  if (ed) return continueFrom(it, ed.dataset.continue);
  if (it.kind === "bg") {                                          // 背景图：点一张就换成它（背景图必须有一张在用）
    const use = card.dataset.pick;
    if ((S.d.panorama || {}).use === use) return;
    try {
      S.d.panorama = await api(`/api/p/${S.pid}/panorama/use`, { json: { use } });
      setLog(T`背景图现在用 ${card.querySelector(".num").textContent}`);
      renderTabs();
    } catch (err) { setLog(err.message, true); }
    return;
  }
  try {
    const r = await api(`/api/p/${S.pid}/images/${it.id}/pick`, { json: { name: it.pick === card.dataset.pick ? null : card.dataset.pick } });
    Object.assign(it, r);
    renderTabs();
  } catch (err) { setLog(err.message, true); }
});
// 图片生成用哪条路：ComfyUI（Qwen / 自己的工作流）或云端 API（只发输入图 + 提示词）。记在浏览器里；背景图标签也有（「补全背景」走平台）
let imgEngine = (() => { try { return localStorage.getItem("vps_img_engine") === "api" ? "api" : "comfy"; } catch { return "comfy"; } })();
function syncImgEngine() {
  const it = curImg(), api = imgEngine === "api" && !!it;
  document.body.classList.toggle("img-api", api);
  document.querySelectorAll("#imgEngineSw [data-eng]").forEach((b) => b.classList.toggle("on", b.dataset.eng === imgEngine));
  if (api && it.kind === "bg") api_info();                           // 背景图：提示词用「补全背景」里那个框
  else if (api) {
    if (document.activeElement !== $("#imgApiPrompt")) $("#imgApiPrompt").value = it.api_prompt || "";
    api_info();
  }
}
async function api_info() {
  try {
    const c = await api("/api/providers");
    if (!c.profile) { $("#imgApiInfo").innerHTML = `<span class="bad">${T("还没设云端平台：设置 → 云端 API")}</span>`; return; }
    $("#imgApiInfo").innerHTML = esc(c.profile.name || c.provider) +
      (c.key_set ? "" : ` · <span class="bad">${T("还没填 API key")}</span>`);
    if (document.activeElement !== $("#imgApiModel")) $("#imgApiModel").value = c.image_model;
    if (!c.key_set) return;
    const m = await api("/api/providers/models", { timeout: 25000 });
    $("#imgApiModels").innerHTML = m.models.map((x) => `<option value="${esc(x)}">`).join("");
    $("#imgApiModel").title = m.listed ? T("平台上的图片模型（点一下看列表）；也可以直接填模型名") : T("平台没给模型列表：直接填模型名（平台控制台里的模型 ID）");
  } catch {}
}
$("#imgApiModel").onchange = async () => {
  const v = $("#imgApiModel").value.trim();
  if (!v) return api_info();
  try {
    const c = await api("/api/providers", { json: { image_model: v } });
    if (typeof fillProviders === "function") fillProviders(c);
    setLog(T("API 模型改成 ") + v);
  } catch (err) { setLog(err.message, true); }
};
$("#imgEngineSw").addEventListener("click", (e) => {
  const b = e.target.closest("[data-eng]");
  if (!b) return;
  imgEngine = b.dataset.eng;
  try { localStorage.setItem("vps_img_engine", imgEngine); } catch {}
  syncImgEngine();
});
async function genApi(it) {
  const prompt = $("#imgApiPrompt").value.trim();
  if (!prompt) return setLog(T("先写提示词"), true);
  const n = +$("#imgCount").value, pid = S.pid;
  await submitOnce(() => api(`/api/p/${pid}/images/${it.id}/api_cands`, { json: { prompt, count: n, mentions: mentionsOf(prompt, imgMentionItems(true)) } }));
  it.api_prompt = prompt;
  S.genWait = { id: it.id, from: it.cands.length, total: n };
  renderRefTab(it);
  await watchJob();
  S.genWait = null;
  if (S.pid === pid) showTab("ires:" + it.id);                     // 等的时候换了项目：不跳
}
$("#imgGen").onclick = async () => {
  const it = curImg(), pid = S.pid;
  if (!it || submitBusy) return;
  if (document.body.classList.contains("img-api")) { try { await genApi(it); } catch (err) { setLog(err.message, true); } return; }
  try {
    const n = +$("#imgCount").value;
    await submitOnce(() => api(`/api/p/${pid}/images/${it.id}/cands`, { json: { desc: $("#imgDesc").value, sys: $("#imgSys").value, count: n,
                                                                  mentions: mentionsOf([$("#imgDesc").value, $("#imgSys").value], imgMentionItems(true)),
                                                                  use_bg: it.use_bg !== false && !!(S.d.panorama || it.bg_local),
                                                                  fill: (document.querySelector('input[name="imgFill"]:checked') || {}).value,
                                                                  img_close: +$("#imgClose").value, img_grow: +$("#imgGrow").value,
                                                                  img_edge: +$("#imgEdge").value, img_mgrow: +$("#imgMgrow").value, img_occ: $("#imgOcc").checked ? 1 : 0,
                                                                  pe: $("#imgPe").value !== "off", body: $("#imgBody").checked,
                                                                  body_parts: $("#imgBodyParts").value, seed: $("#imgSeed").value,
                                                                  mode: it.mode === "inpaint" ? "inpaint" : "gen" } }));
    S.genWait = { id: it.id, from: it.cands.length, total: n };    // 出一张显示一张：任务跑的时候 watchJob 每 2 秒刷新候选
    renderRefTab(it);
    await watchJob();
    S.genWait = null;
    if (S.pid === pid) showTab("ires:" + it.id);                   // 生成完：跳到这一张的图片结果（等的时候换了项目就不跳）
  } catch (err) { setLog(err.message, true); }
};
$("#imgDescVL").onclick = async () => {                           // Qwen-VL 看原图写描述
  const it = curImg();
  if (!it) return;
  if ($("#imgDesc").value.trim() && !(await uiConfirm(T("用 Qwen-VL 写的描述覆盖现在的描述？"), { ok: T("覆盖") }))) return;
  try { await api(`/api/p/${S.pid}/images/${it.id}/describe`, { method: "POST" }); $("#imgDesc").dataset.for = ""; await watchJob(); }
  catch (err) { setLog(err.message, true); }
};
$("#imgSysReset").onclick = () => { const it = curImg(); if (it) $("#imgSys").value = sysDefault(it); };

// 补图范围的选项（蒙版面板里「补图范围」那一组）和图片生成的设置：改了就存进这张图（不出图），叠加层跟着变
let genOptsTimer = 0, genOptsPending = null;                       // 还没发出去的改动：同一张图的合在一起发，快速连改不会丢
function saveGenOpts(it, fields, overlay) {
  Object.assign(it, fields);
  clearTimeout(genOptsTimer);
  if (genOptsPending && genOptsPending.it !== it) flushGenOpts();  // 换了一张图：先把上一张的发掉
  genOptsPending = { it, pid: S.pid, overlay: overlay || (genOptsPending || {}).overlay,
                     fields: { ...(genOptsPending ? genOptsPending.fields : {}), ...fields } };
  genOptsTimer = setTimeout(flushGenOpts, overlay ? 200 : 400);
}
async function flushGenOpts() {
  const p = genOptsPending;
  if (!p) return;
  genOptsPending = null; clearTimeout(genOptsTimer);
  try { await api(`/api/p/${p.pid}/images/${p.it.id}/gen_opts`, { json: p.fields }); }
  catch (err) { return setLog(err.message, true); }
  if (p.overlay && curImg() === p.it) { S.stillGen++; draw(); }
}
IMG_OPTS.forEach(([id, k]) => {
  $("#" + id).addEventListener("input", () => {
    const it = curImg();
    if (!it) return;
    $(`#${id}Val`).textContent = $("#" + id).value + " px";
    saveGenOpts(it, { [k]: +$("#" + id).value }, true);
  });
});
$("#maskPanel").addEventListener("click", (e) => {                  // 「推荐 N」：回到推荐值
  const b = e.target.closest(".rgRec"), it = curImg();
  if (!b || !it) return;
  e.preventDefault(); e.stopPropagation();
  const k = IMG_OPTS.find((x) => x[0] === b.dataset.k)[1];
  $("#" + b.dataset.k).value = b.dataset.v;
  $(`#${b.dataset.k}Val`).textContent = b.dataset.v + " px";
  saveGenOpts(it, { [k]: +b.dataset.v }, true);
});
$("#imgOcc").onchange = () => { const it = curImg(); if (it) saveGenOpts(it, { img_occ: $("#imgOcc").checked ? 1 : 0 }, true); };
document.querySelectorAll('input[name="imgFill"]').forEach((r) => (r.onchange = () => { const it = curImg(); if (it && r.checked) saveGenOpts(it, { fill: r.value }); }));
$("#imgPe").onchange = async () => {
  const it = curImg(), v = $("#imgPe").value;
  if (it) saveGenOpts(it, { pe: v !== "off" });
  if (v === "off" || v === "on" || v === S.peKind) return;
  try { await loadQwenModels(await api(`/api/qwen/pe/${encodeURIComponent(v)}`, { method: "POST" })); setLog(T("PE 已切换")); }
  catch (err) { setLog(err.message, true); }
};
$("#imgBody").onchange = () => { const it = curImg(); $("#imgBodyPartsRow").hidden = !$("#imgBody").checked; if (it) saveGenOpts(it, { body: $("#imgBody").checked }); };
$("#imgBodyParts").onchange = () => { const it = curImg(); if (it) saveGenOpts(it, { body_parts: $("#imgBodyParts").value }); };
$("#imgDesc").onchange = () => { const it = curImg(); if (it) saveGenOpts(it, { desc: $("#imgDesc").value }); };
$("#imgSys").onchange = () => { const it = curImg(); if (it) saveGenOpts(it, { sys: $("#imgSys").value }); };

// 背景图：候选列表 + 现在用的是哪张（和首帧一样，点一张 = 选用）
function bgCards() {
  const pn = (S.d && S.d.panorama) || {};
  const cs = pn.candidates || [];
  const has = (n) => !pn.files || pn.files.includes(n);           // 服务端会说哪些文件真的在
  const out = cs.slice().reverse().map((n) => ({ name: n, label: `#${cs.indexOf(n) + 1}` }));
  if (pn.filled && has("qwen_full")) out.push({ name: "qwen_full", label: T("Qwen 整张") });
  if (pn.filled && has("filled")) out.push({ name: "filled", label: T("只补洞") });
  if (pn.uploaded && has("upload")) out.push({ name: "upload", label: T("上传的") });
  return out;
}
const panoSrc = (name, w) => `/api/p/${S.pid}/panorama/${encodeURIComponent(name)}.png?v=${(S.d.panorama || {}).v}${w ? "&w=" + w : ""}`;
function renderBgMain() {                                          // 左边大图 = 现在选用的那张背景图
  const pn = S.d.panorama;
  if (!pn) { showMain("", T("还没有背景图：在右边点「提取背景」，再在第二步生成")); return; }
  if (curImg()?.kind === "bg") { showMain(panoSrc(pn.selected_extract || "plate", 0), T("所选提取结果")); return; }
  const list = bgCards(), cur = list.find((c) => c.name === pn.use) || list[0];
  if (cur) { showMain(panoSrc(cur.name, 0), T`背景图 · 现在用的是 ${cur.label}`); return; }
  if (!pn.filled) { showMain(panoSrc("plate", 0), T("提取结果（还没补全背景图：在第二步点「用提取结果补全背景」）")); return; }
  showMain("", T("还没有候选：在第二步点「用提取结果补全背景」"));
}





$("#imgModeSw").addEventListener("click", (e) => {                 // 填补 / 生成 二选一（存进这张图）
  const b = e.target.closest("[data-mode]"), it = curImg();
  if (!b || !it) return;
  const wasDefault = sysOf(it) === $("#imgSys").value;            // 基础提示没改过：换成新模式的默认
  const f = { mode: b.dataset.mode };
  Object.assign(it, f);
  if (wasDefault) { $("#imgSys").value = sysDefault(it); f.sys = $("#imgSys").value; }
  saveGenOpts(it, f);
  $$("#imgModeSw [data-mode]").forEach((x) => x.classList.toggle("on", x === b));
  $("#imgFeatherRow").hidden = !(it.base_upload && it.repaint) && !(isStill(it) && it.mode === "inpaint");
});

// 图片结果：左边大图看选用的（没选就最新的一张），右边列出这一张生成出来的全部候选；背景图也是同一套格式
function renderIres(it) {
  const n = it.kind === "bg" ? (S.d.panorama ? bgCards().length : 0) : it.cands.length;
  $("#iresKind").textContent = T`· ${imgLabel(it)} · ${n} 张`;
  $("#imgResPanel").classList.toggle("bg", it.kind === "bg");
  $("#iresHint").title = it.kind === "bg"                           // 用法说明放在标题的鼠标停留提示里
    ? T("背景图生成出来的都在这。点一张 = 换成这张（生成视频时用它）；鼠标移上去左边看大图")
    : T("生成出来的图都在这里。点一张 = 选用（生成视频时用它），再点一次取消；鼠标移上去左边看大图");
  if (it.kind === "bg") { renderPano(); renderBgResults(); renderIresInfo(it); return; }
  renderRefTab(it);
  renderIresInfo(it);
  if (!it.pick && it.cands.length) {
    const n = it.cands[it.cands.length - 1];
    showMain(imgUrl(it, n), T`候选 #${it.cands.length}（还没选用：点右边一张选用）`);
  }
  if (!it.cands.length) showMain("", T("还没有生成结果：到「图片编辑」里生成候选，或上传一张图"));
}
// 图片结果的生成信息：选用的那张（没选用就最新一张）是怎么出的 + 在文件夹中显示
const IMG_MODEL = { turbo: "Qwen 2.1 Turbo", full: T("Qwen 2.1 完整版"), int8: "Qwen 2.1 int8" };
const IMG_FILL = { grey: T("灰色"), bg: T("背景图"), trans: T("透明") };
function renderIresInfo(it) {
  const box = $("#iresInfo");
  if (it.kind === "bg") {
    const pn = S.d.panorama || {}, c = bgCards().find((x) => x.name === pn.use) || bgCards()[0];
    box.innerHTML = c ? `<div class="ri-head"><b>${esc(T("背景图 · ") + c.label)}</b>` +
      T`<button type="button" class="ghostbtn ri-reveal" data-reveal="background" data-name="${esc(c.name)}" title="在资源管理器里选中这张图">${IC("folder")} 在文件夹中显示</button></div>` +
      infoPrompt(pn.pe_prompt) : "";
    return;
  }
  const n = it.pick || it.cands[it.cands.length - 1];
  if (!n) { box.innerHTML = ""; return; }
  const ci = (it.cand_info || {})[n] || {}, seed = ci.seed ?? (it.cand_seed || {})[n];
  box.innerHTML = `<div class="ri-head"><b>${esc(T`候选 #${it.cands.indexOf(n) + 1}`)}${it.pick === n ? T(" · 已选用") : T(" · 最新")}</b>` +
    T`<button type="button" class="ghostbtn ri-reveal" data-reveal="image" data-name="${esc(n)}" title="在资源管理器里选中这张图">${IC("folder")} 在文件夹中显示</button></div>` +
    (ci.upload ? infoRows([[T("来源"), T("上传的图")], [T("时间"), ci.time]])
      : infoRows([
        [T("生成时间"), ci.time || ""], [T("模型"), IMG_MODEL[ci.model] || ci.model || ""],
        [T("方式"), ci.mode ? ({ inpaint: T("局部填补"), repaint: T("局部重绘"), api: T("云端 API") }[ci.mode] || T("整图重绘")) : ""],
        [T("空区填充"), IMG_FILL[ci.fill] || ""], ["Seed", seed], [T("提示词优化"), ci.pe === undefined ? "" : !ci.pe ? T("关") : ((S.pes || []).find((m) => m.key === ci.pe_model) || {}).label || ci.pe_model || T("开")],
        [T("参考图"), ci.refs ? T`${ci.refs} 张` : ""],
      ]) + (Object.keys(ci).length ? infoPrompt(ci.prompt) : T('<div class="sub">这张是旧版本生成的，没有记录详细参数</div>')));
}
$("#iresInfo").addEventListener("click", async (e) => {
  const it = curAny();
  if (!it) return;
  const b = e.target.closest("[data-reveal]");
  if (b) return revealOutput({ kind: b.dataset.reveal, name: b.dataset.name, iid: it.id });
});
// 结果行最右的编辑图标：这张候选当成这个标签的新画面（不再带视频图层、不能改回），回到图片编辑接着改（可以画范围局部重绘）
async function continueFrom(it, name) {
  const repaint = it.repaint;
  try {
    delete it.repaint;
    Object.assign(it, await api(`/api/p/${S.pid}/images/${it.id}/base_from_cand`, { json: { name } }));
    showTab(it.id);
    // 直接进局部重绘：画面上出现画笔工具栏，涂要改的地方，✓ / Enter 存；✕ / Esc = 不画，整张按提示词改
    const im = $("#imgMain"), go = () => { if (curImg() === it && !S.shape) startShape("brush", "repaint"); };
    if (im.complete && im.naturalWidth && im.getAttribute("src") === imgUrl(it, "base")) go(); else im.addEventListener("load", go, { once: true });
    setLog(T`${imgLabel(it)}：用候选 #${it.cands.indexOf(name) + 1} 当画面接着编辑：在图上涂要改的地方（✓ 存，✕ 不画 = 整张按提示词改），写好描述后点「生成候选」`);
  } catch (err) { if (repaint !== undefined) it.repaint = repaint; setLog(err.message, true); }
}
function renderBgResults() {                                        // 背景图的候选：和首帧一样的格子
  const pn = S.d && S.d.panorama;
  $("#imgStaleNote").hidden = true;
  if (!pn) {
    $("#imgCands").innerHTML = T('<div class="empty">还没有背景图：到「图片编辑」里提取背景，再在第二步生成候选</div>');
    showMain("", T("还没有背景图"));
    return;
  }
  const list = bgCards(), use = pn.use || "";
  $("#imgCands").innerHTML = list.length ? list.map((c) =>
    `<div class="rc ${c.name === use ? "on" : ""}" data-name="${esc(c.name)}" data-pick="${esc(c.name)}">` +
    `<img src="${panoSrc(c.name, 240)}"><span class="rc-t"><b class="num">${esc(c.label)}</b></span>` +
    `<span class="pickbtn">${c.name === use ? T("✓ 已选用") : T("选用")}</span></div>`).join("")
    : T('<div class="empty">还没有候选：点「用提取结果补全背景」</div>');
  const cur = list.find((c) => c.name === use) || list[0];
  if (cur) { showMain(panoSrc(cur.name, 0), T`背景图 · ${cur.name === use ? T("现在用的是") : T("最新的")} ${cur.label}`); return; }
  if (!pn.filled) { showMain(panoSrc("plate", 0), T("提取结果（还没补全背景图）")); return; }
  showMain("", T("还没有候选"));
}
$("#imgToRes").onclick = () => { const it = curImg(); if (it) showTab("ires:" + it.id); };
$("#panoToRes").onclick = () => { const it = curImg(); if (it) showTab("ires:" + it.id); };

// 图片编辑：上传一张要调整的图，代替从视频取的画面
$("#imgBaseUp").onclick = () => $("#imgBaseFile").click();
$("#imgBaseFile").onchange = async () => {
  const it = curImg(), f = $("#imgBaseFile").files[0];
  $("#imgBaseFile").value = "";
  if (!it || !f) return;
  try { Object.assign(it, await api(`/api/p/${S.pid}/images/${it.id}/base_upload`, { method: "POST", body: f })); setLog(T`${it.label}：用上传的图当画面（生成候选时按提示词改这张图）`); renderTabs(); }
  catch (err) { setLog(err.message, true); }
};
$("#imgBaseReset").onclick = async () => {
  const it = curImg();
  if (!it) return;
  try { await api(`/api/p/${S.pid}/images/${it.id}/base_reset`, { method: "POST" }); await watchJob(); }
  catch (err) { setLog(err.message, true); }
};
// 背景图：编辑里上传 = 代替中间层
$("#panoPlateUp").onclick = () => $("#panoPlateFile").click();
$("#panoPlateFile").onchange = async () => {
  const f = $("#panoPlateFile").files[0];
  $("#panoPlateFile").value = "";
  if (!f || !S.d) return;
  try {
    S.d.panorama = await api(`/api/p/${S.pid}/panorama/plate_upload`, { method: "POST", body: f });
    renderPano(); renderTabs();
    setLog(T("提取结果换成了上传的图，再点第二步补全背景图"));
  } catch (err) { setLog(err.message, true); }
};
addEventListener("pointerdown", (e) => {                           // 点浮层外面就收起（按下时收；打开是在松手的 click 里，不冲突）
  const box = $("#imgTabPick");
  if (!box.hidden && !box.contains(e.target)) box.hidden = true;
}, true);

// 切到图片编辑时：已有的首帧 / 尾帧，画面不是按现在的编辑取的就自动刷新（2026-11-16 用户定；和「原图」行的刷新按钮一样，
// 已选的候选保留）。接着编辑的图 / 上传的画面不动（那张图就是画面本身）。
let autoRefreshing = false;
async function autoRefreshBases() {
  if (!S.d || autoRefreshing) return;
  autoRefreshing = true;
  try {
    const { base_fresh } = await api(`/api/p/${S.pid}/images_sig`);
    const old = imgTabs().filter((t) => isStill(t) && !t.base_upload && !t.base_from && (base_fresh || {})[t.id] === false);
    for (const t of old) await api(`/api/p/${S.pid}/images/${t.id}/base_reset`, { method: "POST" });
    if (old.length) {
      const names = old.map((t) => imgLabel(t)).join(uiLanguage === "en" ? ", " : "、");
      setLog(uiLanguage === "en" ? `The video edit changed: refreshing ${names}` : `视频的编辑改过了：自动刷新${names}的画面`);
      await watchJob();
    }
  } catch (e) { setLog(e.message, true); }
  finally { autoRefreshing = false; }
}


$("#imgKeyFrame").onchange = async () => {                           // 关键帧改帧号：按新的那一帧重新取画面（旧候选不再列出）
  const it = curImg(), v = parseInt($("#imgKeyFrame").value, 10);
  if (!it || it.kind !== "key") return;
  if (!Number.isFinite(v) || v < 0 || v >= S.d.n) { $("#imgKeyFrame").value = it.frame ?? 0; return setLog(uiLanguage === "en" ? `Frame number must be 0–${S.d.n - 1}` : `帧号要在 0–${S.d.n - 1} 之间`, true); }
  if (v === it.frame) return;
  if (it.cands && it.cands.length && !(await uiConfirm(T("改帧号会重新取画面，这张关键帧现在的候选不再列出（文件留着）。继续？"), { ok: T("改帧号") }))) {
    $("#imgKeyFrame").value = it.frame ?? 0; return;
  }
  try {
    await api(`/api/p/${S.pid}/images/${it.id}/frame`, { json: { frame: v } });
    await watchJob();
    setProject(await api(`/api/p/${S.pid}`), true);
  } catch (err) { setLog(err.message, true); }
};
