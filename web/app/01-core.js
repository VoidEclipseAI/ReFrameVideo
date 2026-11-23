// 视频 PS 前端 1/9：全局状态 S、接口 api、项目 / 图层数据、帧缓存、变换数学。
// 9 个文件是同一个全局作用域的普通脚本，按 index.html 里的顺序加载（原来是一个 app.js）。
// 视频 PS —— 前端。画面全部用 jpg/png 帧，不直接加载 mp4（避免被 IDM 拦截）。
// 坐标约定：“视频坐标”是原视频像素；画面上显示时乘以 k = 显示宽度 / 视频宽度。
// 图层变换 tf：p' = c + t + R(rot)·diag(sx, sy)·(p − c)，c 为主体整段外框的中心。
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const S = {
  pid: null, d: null, i: 0, playing: false, start: 0, end: 0,
  sel: new Set(), hover: null, tint: false, cache: {}, tool: "compose",
  undo: [], redo: [], drag: null, bgVisible: true,
  view: { z: 0.4, x: 0, y: 0 }, frameMode: false, space: false,   // 画布视图：屏幕 = 视频坐标 × z + (x, y)
  tab: "video",                                                    // 顶部标签：video 或图片标签的 id
  stillGen: 0,                                                     // 首帧 / 尾帧：这张图的单图覆盖 / 补图范围选项改一次 +1（叠加层换缓存）
  stillShow: null,                                                 // 首帧 / 尾帧：画面上正在看的候选（图片地址）；null = 看编辑
  cmp: false, cmpSplit: 0.5,                                       // 拖动对比：左半边原视频，竖线可拖
  cmpSide: false,                                                  // 并排对比：左右各一整个画面，同时播放
};
// 「第 a–b 帧」：英文语序不同，词条拼不出来，按语言分开写（插进 T`` 里的值不会再被翻）
// 首帧 / 尾帧 / 关键帧：视频里某一帧的画面（按图层摆放取），图片编辑里同一套
const isStill = (it) => !!it && (it.kind === "first" || it.kind === "last" || it.kind === "key");
const FR = (a, b) => uiLanguage === "en" ? (b === undefined ? `frame ${a}` : `frames ${a}–${b}`) : (b === undefined ? `第 ${a} 帧` : `第 ${a}–${b} 帧`);
// 后端写进项目的结果名 / 内容是中文（「结果 12」「合成 person 1、空区蒙版」）：英文界面显示时换掉，数据不改
const resText = (t) => uiLanguage !== "en" ? (t ?? "") : String(t ?? "").replace(/^结果 (\d+)/, "Result $1").replace(/^一采 · /, "Pass 1 · ").replace(/^合成 /, "Composite: ").replace(/^Pass 1 · 合成 /, "Pass 1 · Composite: ")
  .replace(/空区蒙版/g, "empty-area mask").replace(/背景预填/g, "background prefill").replace(/保护蒙版/g, "protect mask").replace(/、/g, ", ");
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// 软件自己的确认 / 输入小窗（代替浏览器的 confirm / prompt：样式、语言和界面一致）。
// 返回 Promise：确认 = true（输入框 = 输入的文字），第二个按钮 = false，Esc / 点空白 / 关闭 = null（什么都不做）
function uiAsk(msg, { input = null, ok = T("确定"), no = T("取消"), danger = false } = {}) {
  return new Promise((resolve) => {
    const d = document.createElement("div");
    d.className = "ui-dialog ask-dlg";
    d.setAttribute("role", "dialog"); d.setAttribute("aria-modal", "true");
    d.innerHTML = `<div class="dlg-card"><div class="dlg-body"><p class="ask-msg"></p>${input !== null ? '<input class="ask-in" type="text">' : ""}</div>` +
      `<div class="dlg-foot"><span class="grow"></span><button type="button" class="ghostbtn ask-no"></button>` +
      `<button type="button" class="primary-sm ask-ok${danger ? " ask-danger" : ""}"></button></div></div>`;
    d.querySelector(".ask-msg").textContent = msg;
    d.querySelector(".ask-ok").textContent = ok;
    d.querySelector(".ask-no").textContent = no;
    const inp = d.querySelector(".ask-in");
    if (inp) inp.value = input;
    const done = (v) => { removeEventListener("keydown", key, true); d.remove(); resolve(v); };
    const yes = () => done(inp ? inp.value : true);
    const key = (e) => {                                            // 弹窗开着：快捷键都不往下传
      if (e.key === "Escape") { e.preventDefault(); done(null); }
      else if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); yes(); }
      else if (!inp || e.target !== inp) { if (e.key !== "Tab") e.preventDefault(); }
      e.stopPropagation();
    };
    addEventListener("keydown", key, true);
    d.querySelector(".ask-ok").onclick = yes;
    d.querySelector(".ask-no").onclick = () => done(inp ? null : false);
    d.addEventListener("pointerdown", (e) => { if (e.target === d) done(null); });
    document.body.append(d);
    (inp || d.querySelector(".ask-ok")).focus();
    if (inp) inp.select();
  });
}
const uiConfirm = (msg, opts) => uiAsk(msg, opts);

// 「正在加载」小窗（2026-11-22 用户要：打开长视频 / 切出选区时界面像死机）：慢操作超过 0.3 秒才弹，显示在做什么 + 已用时间，结束自动关。
// uiBusy(标题) 开、uiBusy(null) 关；uiBusyNote(当前步骤) 改下面那行；可以嵌套（计数），最后一个关了才收起。
const BUSY = { n: 0, el: null, timer: 0, tick: 0, t0: 0, title: "", note: "" };
function busyRender() {
  if (!BUSY.el) return;
  BUSY.el.querySelector(".busy-t").textContent = BUSY.title;
  const sec = Math.round((Date.now() - BUSY.t0) / 1000);
  BUSY.el.querySelector(".busy-s").textContent = (BUSY.note ? BUSY.note + " · " : "") + (uiLanguage === "en" ? `${sec} s elapsed` : `已用 ${sec} 秒`);
}
function uiBusy(title) {
  if (title == null) {
    if (BUSY.n > 0 && --BUSY.n > 0) return;
    clearTimeout(BUSY.timer); clearInterval(BUSY.tick);
    BUSY.el?.remove(); BUSY.el = null; BUSY.note = "";
    return;
  }
  BUSY.title = title;
  if (BUSY.n++ > 0) return busyRender();
  BUSY.t0 = Date.now();
  BUSY.timer = setTimeout(() => {
    const d = document.createElement("div");
    d.className = "ui-dialog busy-dlg";
    d.setAttribute("role", "alertdialog"); d.setAttribute("aria-busy", "true");
    d.innerHTML = '<div class="dlg-card"><span class="busy-spin"></span><div><div class="busy-t"></div><div class="busy-s"></div></div></div>';
    document.body.append(d);
    BUSY.el = d;
    busyRender();
    BUSY.tick = setInterval(busyRender, 1000);
  }, 300);
}
function uiBusyNote(note) { BUSY.note = note || ""; busyRender(); }
async function withBusy(title, fn) { uiBusy(title); try { return await fn(); } finally { uiBusy(null); } }
const uiPrompt = (msg, value = "", opts) => uiAsk(msg, { ...opts, input: String(value ?? "") });

const pendingApiWrites = new Set();
function api(path, opts = {}) {
  const write = opts.json !== undefined || (opts.method && opts.method !== "GET");
  const request = apiRequest(path, opts);
  if (write) {
    pendingApiWrites.add(request);
    uiLanguageWrites?.add(request);
    request.then(() => pendingApiWrites.delete(request), () => pendingApiWrites.delete(request));
  }
  return request;
}
// 顶栏「ComfyUI 引擎」旁边的小圆点：绿 = 连得上，红 = 连不上，灰 = 还不知道
function engDot(state) {
  const d = document.getElementById("engDot");
  if (!d) return;
  d.classList.remove("ok", "bad");
  if (state === "ok") d.classList.add("ok");
  else if (state === "bad") d.classList.add("bad");
  d.title = state === "ok" ? T("连得上") : state === "bad" ? T("连不上（后端或 ComfyUI 没响应）") : T("还没连过");
}

async function apiRequest(path, opts = {}) {
  if (opts.json !== undefined) {
    opts.method = opts.method || "POST";
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(opts.json);
    delete opts.json;
  }
  const ms = opts.timeout;                                   // 有些接口（ComfyUI 模型列表、生成）可能一直不回：别把界面挂死
  delete opts.timeout;
  const ctl = ms ? new AbortController() : null;
  if (ctl) { opts.signal = ctl.signal; }
  const timer = ctl ? setTimeout(() => ctl.abort(), ms) : null;
  try {
    const r = await fetch(path, opts);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || r.statusText);
    window.taskWindow?.request(path, opts, j, S);
    engDot("ok");
    return j;
  } catch (e) {
    if (e && e.name === "AbortError") { engDot("bad"); throw new Error(T("请求超时")); }
    if (e instanceof TypeError) engDot("bad");            // 根本连不上（后端没开 / 断网）
    throw e;
  } finally { if (timer) clearTimeout(timer); }
}

function setLog(text, err = false, taskLog = false) {
  text = uiMsg(text);                                              // 服务端的中文报错 / 日志：英文界面按句型翻（ui/i18n.js）
  $("#log").textContent = text;
  $("#log").classList.toggle("err", err);
  if (!taskLog) window.taskWindow?.notice(text, err);
}

// ---------- 项目 / 图层数据 ----------
const rootObjs = () => (S.d ? S.d.objects.filter(o => o.version === S.d.current) : []);
const objs = () => rootObjs().flatMap(o => [o, ...(o.parts || [])]);
const obj = (id) => objs().find(o => o.id === id);
const layersFrontToBack = () => rootObjs().slice().sort((a,b)=>(b.z??0)-(a.z??0)).flatMap(o=>[...(o.parts||[]).slice().sort((a,b)=>(b.z??0)-(a.z??0)),o]);
const isIdentity = (tf) => !tf || (!tf.x && !tf.y && tf.sx === 1 && tf.sy === 1 && !tf.rot);
const selected = () => [...S.sel].map(obj).filter(Boolean);

async function loadProjectList() {
  const all = await api("/api/projects");
  const list = all.filter((p) => !p.seq);                          // 卡片里不单独列长片段的各段：从长片段的分段页进去（下拉里仍有，启动时能恢复）
  const sq = await api("/api/sequences").catch(() => []), seqs = Array.isArray(sq) ? sq : [];   // 长片段（拿不到不影响普通项目）
  $("#projectSel").innerHTML = T('<option value="">— 打开已有项目 —</option>') +
    all.map((p) => `<option value="${esc(p.id)}">${esc(p.name)} (${esc(p.id)})</option>`).join("");
  if (S.pid) $("#projectSel").value = S.pid;
  let grid = $("#projGrid");                                       // 「打开」对话框里的项目卡片：首帧缩略图 + 名字 + 信息，点一下打开
  if (!grid) { grid = document.createElement("div"); grid.id = "projGrid"; ($("#projectSel").closest(".row") || $("#projectSel")).after(grid); }
  grid.innerHTML = seqs.map((s) => `<button type="button" class="projCard seqCard" data-sid="${esc(s.id)}" title="${esc(s.name)}">` +
    `<img loading="lazy" alt="" src="/api/s/${encodeURIComponent(s.id)}/strip.jpg?k=8">` +
    T`<b>${esc(s.name)}</b><span>长片段 · ${s.segments} 段 · ${(s.n / s.fps).toFixed(1)} 秒 · ${s.w}×${s.h}</span>` +
    `<span>${esc(s.time || "")} · ${esc(s.id)}</span>${delBtn()}</button>`).join("") + list.map((p) => `<button type="button" class="projCard ${p.id === S.pid ? "on" : ""}" data-pid="${esc(p.id)}" title="${esc(p.name)}">` +
    `<img loading="lazy" alt="" src="/api/p/${encodeURIComponent(p.id)}/v/${encodeURIComponent(p.current || "v0")}/frame/0.jpg">` +
    T`<b>${esc(p.name)}</b><span>${p.n ?? "?"} 帧 · ${p.w}×${p.h} · ${uiLanguage === "en" ? `${p.layers ?? 0} layer${p.layers === 1 ? "" : "s"}` : `${p.layers ?? 0} 个图层`}${p.results ? " · " + (uiLanguage === "en" ? `${p.results} result${p.results === 1 ? "" : "s"}` : `${p.results} 个结果`) : ""}</span>` +
    `<span>${esc(p.time || "")} · ${esc(p.id)}</span>${delBtn()}</button>`).join("") || T('<div class="sub">还没有项目</div>');
}
const delBtn = () => T`<span class="pc-del" role="button" title="删除（移到回收站：projects\\_回收站，要找回把文件夹挪回来）" aria-label="删除">${IC("trash")}</span>`;
async function deleteCard(c) {                                     // 项目页卡片上的删除：不真删，挪到 projects/_回收站
  const sid = c.dataset.sid, id = sid || c.dataset.pid, name = c.querySelector("b")?.textContent || id;
  const msg = sid ? T`把长片段「${name}」连同它已经在编辑的各段项目移到回收站？\n（文件夹挪到 projects\\_回收站，要找回把文件夹挪回来）`
                  : T`把项目「${name}」移到回收站？\n（文件夹挪到 projects\\_回收站，要找回把文件夹挪回来）`;
  if (!(await uiConfirm(msg, { ok: T("移到回收站"), danger: true }))) return;
  const tab = sid ? "seq:" + sid : id;
  const open = (window.uiProjectTabs?.list() || []).some((t) => t.pid === tab || (sid && t.seq === sid));
  try {
    await api(sid ? `/api/s/${encodeURIComponent(sid)}` : `/api/p/${encodeURIComponent(id)}`, { method: "DELETE" });   // 服务端先放掉它的对象再挪文件夹
    setLog(T`已移到回收站：${name}`);
    if (open) {                                                    // 开着的标签关掉（关掉最后一个会刷新页面，所以放在删完之后）
      const t = (window.uiProjectTabs?.list() || []).find((x) => x.pid === tab || (sid && x.seq === sid));
      if (t) await window.uiProjectTabs?.close(t.pid);
    }
  } catch (err) { setLog(err.message, true); }
  loadProjectList();
}
document.addEventListener("click", (e) => {
  const c = e.target.closest(".projCard");
  if (!c) return;
  if (e.target.closest(".pc-del")) { e.preventDefault(); e.stopPropagation(); return deleteCard(c); }
  if (c.dataset.sid) { $("#openDlg").hidden = true; return window.uiOpenSeq?.(c.dataset.sid); }   // 长片段：打开分段页
  $("#projectSel").value = c.dataset.pid;
  $("#projectSel").dispatchEvent(new Event("change"));
});

const projStash = new Map();                                       // 后台项目标签常驻（设置里选「最近 2 个」）：pid → 它的帧缓存
const keepProjects = () => { try { return localStorage.getItem("vps_keep_projects") === "2" ? 2 : 1; } catch { return 1; } };
function setProject(d, keep = false) {
  if (keep && S.pid && d.id !== S.pid) return;                     // 刷新当前项目的回包（keep）回来时已经换了项目：丢掉，别把界面拉回旧项目
  try { localStorage.setItem("vps_pid", d.id); } catch {}          // 记住上次打开的项目
  const old = S.pid, first = old !== d.id;
  const verChanged = !first && S.d && S.d.current !== d.current;
  if (first || verChanged) {                                       // 换项目 / 换版本：临时模式收掉（逐帧修整、画蒙版、点选拿着的是旧图层 / 旧版本）
    if (S.refine && typeof endRefine === "function") endRefine();
    if (S.shape && typeof endShape === "function") endShape();
    if (S.pick && typeof setSegMode === "function") setSegMode("text");
    S.extPending = null;
    S.vidI = undefined; S.inStill = false;                         // 视频编辑播放头（新建关键帧用）是上个项目 / 版本的
  }
  if (verChanged) { S.undo = []; S.redo = []; }                    // 撤销记录是上个版本的图层：换版本就清掉
  if (first && old && keepProjects() === 2)                         // 切走的项目：帧缓存收起来（只留最近 1 个后台项目）
    projStash.set(old, { cache: S.cache, ghost: { ...ghostCache }, alpha: new Map(alphaCache) });
  const back = first ? projStash.get(d.id) : null;
  if (first) for (const k of [...projStash.keys()]) if (k !== old) projStash.delete(k);   // 换项目时只留刚切走的那一个（加上当前 = 最近 2 个）
  S.pid = d.id; S.d = d;
  if (first || !keep) { S.start = 0; S.end = d.n - 1; }
  if (first) {                                                     // 换项目：按版本号 / 主体 id 存的帧缓存都是上个项目的（两个项目都叫 v0），清掉
    S.cache = back ? back.cache : {}; S.fi = null; S.fiVid = null; alphaCache.clear(); $("#cmpCv")._f = null;
    if (typeof pcDrop === "function") pcDrop();                     // 整段缓存的合成帧也是上个项目的，丢掉
    for (const k of Object.keys(ghostCache)) delete ghostCache[k];
    if (back) { Object.assign(ghostCache, back.ghost); for (const [k, v] of back.alpha) alphaCache.set(k, v); }
    for (const c of [$("#base"), $("#cmpCv")]) c.getContext("2d").clearRect(0, 0, c.width, c.height);   // 新项目的帧加载好之前不显示上个项目的画面
  }
  if (first) { S.sel.clear(); S.i = 0; S.undo = []; S.redo = []; S.frameMode = false; S.fit = "fitframe"; setTimeout(() => fitView("fitframe"), 0); }
  S.i = Math.min(S.i, d.n - 1);
  const live = new Set(objs().flatMap(o => [o.id, ...(o.parts || []).map(p => p.id)]));
  [...S.sel].forEach((id) => live.has(id) || S.sel.delete(id));
  preload(d.current);
  $("#empty").hidden = true;
  renderAll();
  try { window.uiProjectChanged?.(d); } catch {}                   // 新外壳：顶栏项目名 / 图层数 / 空状态
  if (first && typeof renderHistory === "function") renderHistory();   // 历史面板开着：换成这个项目的记录
}

const rev = () => ((S.d && S.d.rev) || "") + (S.pw && S.pw !== 960 ? "_w" + S.pw : "");   // 预览宽度改了：换缓存   // 预览帧重抽过（老项目修复帧数）就换一个值，浏览器不再用旧缓存
const frameUrl = (vid, i) => `/api/p/${S.pid}/v/${vid}/frame/${i}.jpg?r=${rev()}`;
// 整段缓存（07c-pcache.js）：这一帧若已经合成好成一张整帧图，就返回它，画面直接用；没有就 null，走原来的实时合成
const PC_VID = "__pc";                                   // 缓存帧在这个假版本号名下（不是真的视频版本）
let pcFrameHook = null;
const pcFrame = (i) => (pcFrameHook ? pcFrameHook(i) : null);
function previewImage(url, i) {
  const im = new Image();
  im._decoded = false;
  let retries = 0;
  const pid = S.pid;
  im.onload = async () => {
    try { if (im.decode) await im.decode(); } catch { im.onerror(); return; }
    im._decoded = true;
    if (pid === S.pid && (i === S.i || i === S.fi) && !S.playing) drawSoon();
  };
  im.onerror = () => {
    if (pid !== S.pid || retries++ >= 2) return;
    setTimeout(() => { if (pid === S.pid && Math.abs(i - S.i) <= 2) im.src = url; }, 500 * retries);
  };
  im.src = url;
  return im;
}
function lazyFrames(make, count) {
  return new Proxy(new Array(count), { get(arr, prop) {
    if (typeof prop === "string" && /^\d+$/.test(prop)) {
      const i = Number(prop);
      if (i < count && !(i in arr)) arr[i] = make(i);
    }
    return Reflect.get(arr, prop);
  }});
}
function previewIndices() {
  if (!S.playing) return [S.i, S.i + 1, S.i - 1, S.i + 2].filter(i => i >= 0 && i < S.d.n);
  const span = S.end - S.start + 1;
  const ahead = Math.min(span, 24, Math.max(6, Math.ceil(S.d.fps * 0.75)));
  return Array.from({ length: ahead }, (_, k) => S.start + ((S.i - S.start + k) % span + span) % span);
}
function preload(vid) {
  if (!S.cache[vid]) S.cache[vid] = lazyFrames(i => previewImage(frameUrl(vid, i), i), S.d.n);
  // 当前帧先请求，只预取紧邻帧，避免整段图片堵住参数更新。
  for (const i of previewIndices())
    if (i >= 0 && i < S.d.n) void S.cache[vid][i];
}

// ---------- 帧缓存：静止时少量邻帧；播放时向前缓冲，避免整段请求 ----------
const ghostCache = {};
const CLEAN_FIELDS = ["fill_holes", "despeckle", "smooth_t"];           // 服务端 project.CLEAN_OPTS
const cleanKey = (o) => CLEAN_FIELDS.map((k) => +(o[k] || 0)).join("-");
const cropKey = () => { const c = S.d && S.d.frame && S.d.frame.crop; return c ? `${c.t}-${c.b}` : ""; };
function ghostFrames(o) {
  const split = JSON.stringify((o.parts || []).map(p=>[p.id,p.edits,p.medits,p.grow]));
  const cl = cleanKey(o);                                            // 蒙版清理（填充孔洞 / 去除杂点 / 帧间平滑）
  // 图层 id 必须打头：修抠图存盘后（07-refine.js saveRefine）按「id:」前缀清掉这个图层的帧缓存。
  // 曾经把拆分信息放在最前面，前缀永远对不上，修完抠图画面不变（要刷新页面才看到），2026-11-15 修
  const key = `${o.id}:${split}:${o.edge || "sam"}:${o.grow ?? 0}:${o.alpha || ""}:${rev()}:${cropKey()}:${cl}`;
  if (!ghostCache[key]) {
    o = { ...o, medits: { ...o.medits } };
    const pid = S.pid, crop = cropKey();
    ghostCache[key] = lazyFrames(i => {
      if (i < o.start || i > o.end) return null;
      return previewImage(`/api/p/${pid}/ghost/${o.id}/${i}.png?a=${o.alpha || ""}&e=${o.edge || "sam"}&g=${o.grow ?? 0}&r=${rev()}&m=${(o.medits || {})[i] || 0}&c=${crop}&k=${cl}&split=${encodeURIComponent(split)}`, i);
    }, S.d.n);
  }

  for (const i of previewIndices())
    if (i >= 0 && i < S.d.n) void ghostCache[key][i];
  return ghostCache[key];
}

const overlayUrl = (i) => `/api/p/${S.pid}/overlay/${S.d.current}/${i}.png?tint=1&r=${rev()}`;
let ovSig = "", ovFrames = [];
function overlayFrame(i) {
  const sig = overlayUrl(0);
  if (sig !== ovSig) {
    ovSig = sig;
    ovFrames = lazyFrames(k => previewImage(overlayUrl(k), k), S.d.n);
  }
  return ovFrames[i];
}

function loadingText() {
  const pending = layerPreviewPending();
  if (pending) return pending + T('（参数已修改，正在同步画面）');
  if (S.drag) return T('正在调整位置，松开鼠标后更新透明区域…');
  let total = 0, done = 0;
  const check = im => { if(im){total++;if(ready(im))done++;} };
  check(S.cache[S.compare ? 'v0' : S.d.current]?.[S.i]);
  for (const o of objs()) if (o.visible || S.bgVisible) {
    check(ghostFrames(o)[S.i]);
    if(S.bgVisible && isHole(o))check(holeSamFrames(o)[S.i]);
  }
  if(done<total)return T`正在加载图层与透明遮罩 ${done}/${total}，当前画面尚未更新…`;
  if(!S.compare && !S.refine && (S.bgVisible||S.zones) && anyLifted() && !zoneDecoded.has(zoneKey(S.i, cellMode())))
    return zoneRetry.get(zoneKey(S.i, cellMode())) ? T('透明区域加载失败，正在重试；当前画面尚未更新…') : T('正在计算透明区域与重绘分区，当前画面尚未更新…');
  if (done >= total && S.fi !== S.i) return T("正在准备目标帧…");
  return done >= total ? "" : T`正在更新当前帧 ${done}/${total}`;
}
function previewStatus(text) {
  $('#loading').textContent=text;
  let badge=$('#previewStatus');
  if(!badge){badge=document.createElement('div');badge.id='previewStatus';badge.setAttribute('role','status');badge.setAttribute('aria-live','polite');$('#stage').append(badge);}
  badge.textContent=text;badge.hidden=!text;
  $('#stage').setAttribute('aria-busy',String(!!text));
}

// ---------- 变换数学 ----------
function centerOf(o) { const b = o.bbox; return [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]; }
function apply(o, p, tf = o.tf) {   // 原画面坐标 → 变换后坐标
  const [cx, cy] = centerOf(o), r = tf.rot * Math.PI / 180;
  const dx = (p[0] - cx) * tf.sx, dy = (p[1] - cy) * tf.sy;
  const out = [cx + tf.x + Math.cos(r) * dx - Math.sin(r) * dy, cy + tf.y + Math.sin(r) * dx + Math.cos(r) * dy];
  return o.parent_id ? apply(obj(o.parent_id), out) : out;
}
// 变换中心（锚点）：改大小 / 旋转时哪个点不动。九宫格 0..8 对应图层外框上的点（4 = 中心，原来的行为）；记在浏览器里
const ANCHORS = [[0, 0], [.5, 0], [1, 0], [0, .5], [.5, .5], [1, .5], [0, 1], [.5, 1], [1, 1]];
const anchorIdx = () => { try { const v = localStorage.getItem("vps_anchor"); return v !== null && +v >= 0 && +v < 9 ? +v : 4; } catch { return 4; } };
function applyOwn(o, p, tf) {                                     // 只算这个图层自己的变换（不乘父图层的）
  const [cx, cy] = centerOf(o), r = tf.rot * Math.PI / 180;
  const dx = (p[0] - cx) * tf.sx, dy = (p[1] - cy) * tf.sy;
  return [cx + tf.x + Math.cos(r) * dx - Math.sin(r) * dy, cy + tf.y + Math.sin(r) * dx + Math.cos(r) * dy];
}
function anchorFix(o, oldTf) {                                    // 改完 sx / sy / rot 之后调：把锚点挪回原来的位置（只改 x、y）
  const a = ANCHORS[anchorIdx()];
  if (a[0] === .5 && a[1] === .5) return;
  const b = o.bbox, p = [b[0] + (b[2] - b[0]) * a[0], b[1] + (b[3] - b[1]) * a[1]];
  const p0 = applyOwn(o, p, oldTf), p1 = applyOwn(o, p, o.tf);
  o.tf.x += p0[0] - p1[0]; o.tf.y += p0[1] - p1[1];
}
function ownDelta(o, d) {                                         // 画面上要挪 d（原画面像素）→ 这个图层自己的 x / y 要改多少（从属图层要除掉父图层的缩放 / 旋转）
  if (!o.parent_id) return d;
  const P = obj(o.parent_id), v = ownDelta(P, d), r = -P.tf.rot * Math.PI / 180;
  const w = [Math.cos(r) * v[0] - Math.sin(r) * v[1], Math.sin(r) * v[0] + Math.cos(r) * v[1]];
  return [w[0] / (P.tf.sx || 1), w[1] / (P.tf.sy || 1)];
}
const rotate = (v, deg) => { const r = deg * Math.PI / 180; return [Math.cos(r) * v[0] - Math.sin(r) * v[1], Math.sin(r) * v[0] + Math.cos(r) * v[1]]; };
function frameBox(o, i) {   // 当前帧的外框（原画面坐标）；该帧没有主体时用最近一帧
  const bs = o.bboxes || [];
  for (let d = 0; d < bs.length; d++) {
    if (bs[i - d]) return bs[i - d];
    if (bs[i + d]) return bs[i + d];
  }
  return o.bbox;
}
const HANDLES = {   // 把手：原框上的位置（0~1）和对应的固定点
  nw: [0, 0], n: [0.5, 0], ne: [1, 0], e: [1, 0.5], se: [1, 1], s: [0.5, 1], sw: [0, 1], w: [0, 0.5],
};
const at = (b, u, v) => [b[0] + (b[2] - b[0]) * u, b[1] + (b[3] - b[1]) * v];


function layerVisible(o) { return o.visible && (!o.parent_id || layerVisible(obj(o.parent_id))); }
// ---------- 跟随蒙版（扩大重绘范围，图层字段 ext_masks；服务端 regions.ext_masks 同一套规则）----------
// 每个 = {id, name, t, b, l, r, t0…r1, cont, start, end, off}；旧项目只有 box_ext = 一个所有帧都生效的跟随蒙版。
// S.extSel[图层 id] = 正在编辑的那个（画面上的橙色箭头、蒙版面板改的都是它）。
const EXT_SIDES = ["t", "b", "l", "r"];
function extList(o) {
  if (Array.isArray(o.ext_masks)) return o.ext_masks;
  const e = o.box_ext || {};
  return EXT_SIDES.some((k) => e[k] > 0) ? [{ ...e, id: "e1", name: T("主体扩绘区 1") }] : [];
}
const extOn = (m, i) => !m.off && EXT_SIDES.some((k) => m[k] > 0) && (i == null || ((m.start ?? 0) <= i && i <= (m.end ?? Infinity)));
const extEmpty = (m) => !EXT_SIDES.some((k) => m[k] > 0);
// 刚「添加」、还没拉开的那一个（S.extPending = {oid, m, tab}）：只在界面上，不存盘；拉开 / 填了数才存。
// 选别的图层、换面板组、换页时还是空的就自动取消（pruneExt）。
// extsOf(o) = 这个图层现在看到的主体扩绘区（图片编辑页是这张图的单图覆盖）+ 还没存的那一个。
function extsOf(o) {
  const L = extList(typeof imgEditing === "function" && imgEditing() ? effObj(o) : o), P = S.extPending;
  return P && P.oid === o.id && !L.some((m) => m.id === P.m.id) ? [...L, P.m] : L;
}
function curExt(o) {                                              // 正在编辑的那个（画面上的橙色箭头改它）；拖动中 = 拖动的那份
  if (S.xdrag && S.xdrag.o.id === o.id) return S.xdrag.m;
  const L = extsOf(o);
  return L.find((m) => m.id === (S.extSel || {})[o.id]) || L[0] || null;
}
function newExt(L) {                                              // 新的主体扩绘区：编号接着往下，默认所有帧、填充间隙开
  let k = 1; while (L.some((m) => m.id === "e" + k || m.name === T("主体扩绘区 ") + k || m.name === "向外延伸 " + k || m.name === T("延展区域 ") + k)) k++;
  return { id: "e" + k, name: T("主体扩绘区 ") + k, start: 0, end: S.d.n - 1, cont: true };
}
function pruneExt() {                                             // 空的、还没存的那一个：离开它就取消。返回有没有取消
  const P = S.extPending;
  if (!P) return false;
  if (!extEmpty(P.m)) { S.extPending = null; return false; }
  if (S.sel.size === 1 && S.sel.has(P.oid) && P.tab === S.tab && (typeof uiCurGroup !== "function" || uiCurGroup() === "mask")) return false;
  S.extPending = null;
  return true;
}

// 原位置要挖洞的图层：隐藏或动过。停用的固定蒙版（shape + off）当它不存在
function isHole(o) { return (!layerVisible(o) || !layerIdentity(o)) && !(o.shape && o.off) && !o.prefill && !o.protect; }   // 背景预填 / 保护蒙版图层不挖洞
// 眼睛 / H：普通图层 = 显示 / 隐藏；固定蒙版（本来就隐藏 = 空区）= 启用 / 停用，不会变成显示原视频像素
function toggleVis(x) { if (x.shape) x.off = x.off ? null : true; else x.visible = !x.visible; }
function layerIdentity(o) { return isIdentity(o.tf) && (!o.parent_id || layerIdentity(obj(o.parent_id))); }

// 提交任务的请求同一时间只发一个：双击「生成视频」「生成候选」不会排两个任务（发出去之前再点直接忽略）
let submitBusy = false;
async function submitOnce(fn) {
  if (submitBusy) return undefined;
  submitBusy = true;
  try { return await fn(); } finally { submitBusy = false; }
}
