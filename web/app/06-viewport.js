// 视频 PS 前端 6/9：无限画布（缩放 / 平移）、输出画幅。
// 9 个文件是同一个全局作用域的普通脚本，按 index.html 里的顺序加载（原来是一个 app.js）。
// ---------- 无限画布：缩放 / 平移 ----------
const stageSize = () => { const s = $("#stage"); return [s.clientWidth, s.clientHeight]; };
const ZMIN = 0.02, ZMAX = 16;
let drawQueued = false;
function drawSoon() {
  if (drawQueued) return;
  drawQueued = true;
  requestAnimationFrame(() => { drawQueued = false; draw(); });
}
function zoomAt(z, sx, sy) {   // 以屏幕点 (sx, sy) 为中心缩放：鼠标下的那一点不动
  if (S.cmpSide) return;                                          // 并排对比时固定全览，缩放留给退出后
  S.fit = null;                                                    // 手动缩放过：窗口变化时保持你调的位置
  z = Math.min(Math.max(z, ZMIN), ZMAX);
  const v = S.view, wx = (sx - v.x) / v.z, wy = (sy - v.y) / v.z;
  v.x = sx - wx * z; v.y = sy - wy * z; v.z = z;
  drawSoon();
}
function zoomBy(f) { const [W, H] = stageSize(); zoomAt(S.view.z * f, W / 2, H / 2); }
function zoomTo(z) { const [W, H] = stageSize(); zoomAt(z, W / 2, H / 2); }
// 图片编辑（首帧 / 尾帧）不显示播放条和时间轴，画面区域会变高；适应时扣掉它们的高度，
// 画面和视频编辑停在同一个位置、同样大小，切换时不跳。高度在视频编辑时量（隐藏时量不到）。
let videoChromeH = 0;
function fitHeight(H) {
  if (document.body.dataset.tab !== "still") {
    const gap = parseFloat(getComputedStyle($("#stage").parentElement).rowGap) || 0;
    videoChromeH = ["#transport", "#timeline"].reduce((h, s) => { const e = $(s); return e && e.offsetParent ? h + e.offsetHeight + gap : h; }, 0);
    return H;
  }
  return Math.max(H - videoChromeH, 1);
}
function fitRect(b, pad = 56) {   // b = [x0, y0, x1, y1]（视频坐标）
  const [W, H0] = stageSize();
  const H = fitHeight(H0);
  if (W < 2 * pad + 20 || H < 2 * pad + 20) { setTimeout(() => fitRect(b, pad), 200); return; }   // 画面区域还没显示出来：等有了尺寸再适应
  const z = Math.min((W - 2 * pad) / Math.max(b[2] - b[0], 1), (H - 2 * pad) / Math.max(b[3] - b[1], 1));
  S.view.z = Math.min(Math.max(z, ZMIN), ZMAX);
  S.view.x = W / 2 - (b[0] + b[2]) / 2 * S.view.z;
  S.view.y = H / 2 - (b[1] + b[3]) / 2 * S.view.z;
  drawSoon();
}
const unionBox = (bs) => bs.reduce((a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])]);
function fitView(kind) {
  if (!S.d) return;
  S.fit = kind === "sel" ? null : kind;                            // 记住“适应”模式：画面区域大小变了自动重新适应
  const f = getFrame(), fr = [f.x, f.y, f.x + f.w, f.y + f.h], vid = [0, 0, S.d.w, S.d.h];
  if (kind === "fitframe") return fitRect(fr);
  if (kind === "fitall") return fitRect(unionBox([fr, vid]));
  if (kind === "sel") {
    const bs = selected().map((o) => { const ps = polygon(o, S.i); return [Math.min(...ps.map((p) => p[0])), Math.min(...ps.map((p) => p[1])), Math.max(...ps.map((p) => p[0])), Math.max(...ps.map((p) => p[1]))]; });
    return fitRect(bs.length ? unionBox(bs) : fr);
  }
}
$("#stage").addEventListener("wheel", (e) => {
  if (!S.d) return;
  e.preventDefault();
  const r = $("#stage").getBoundingClientRect(), sx = e.clientX - r.left, sy = e.clientY - r.top;
  const k = e.deltaMode === 1 ? 33 : 1;
  if (e.shiftKey && !e.ctrlKey) { S.fit = null; S.view.x -= (e.deltaY || e.deltaX) * k; return drawSoon(); }       // Shift+滚轮：左右平移
  if (!e.ctrlKey && Math.abs(e.deltaX) > Math.abs(e.deltaY)) {                                        // 触控板左右滑：平移
    S.fit = null; S.view.x -= e.deltaX * k; S.view.y -= e.deltaY * k; return drawSoon();
  }
  zoomAt(S.view.z * Math.exp(-e.deltaY * k * (e.ctrlKey ? 0.01 : 0.0015)), sx, sy);                // 滚轮 / 双指捏合：缩放
}, { passive: false });
function startPan(e) {
  e.preventDefault();
  if (S.cmpSide) return;
  S.pan = { sx: e.clientX, sy: e.clientY, x: S.view.x, y: S.view.y };
  S.spacePanned = true;
  $("#stage").classList.add("panning");
  try { $("#stage").setPointerCapture(e.pointerId); } catch {}
}
function panMove(e) {
  S.fit = null;
  S.view.x = S.pan.x + e.clientX - S.pan.sx;
  S.view.y = S.pan.y + e.clientY - S.pan.sy;
  drawSoon();
}
$("#zoomBar").addEventListener("pointerdown", (e) => e.stopPropagation());
$("#zoomIn").onclick = () => zoomBy(1.25);
$("#zoomOut").onclick = () => zoomBy(0.8);
$("#zoomSel").onchange = (e) => {
  const v = e.target.value;
  e.target.value = "cur";
  if (v.startsWith("fit") || v === "sel") fitView(v); else zoomTo(+v);
};
$("#fitBtn").onclick = () => fitView("fitframe");
// 分屏对比：按钮开关；竖线拖动能挪分界（左边原视频 / 右边当前）
$("#cmpBtn").onclick = () => {                 // 拖动对比（竖线可拖）
  S.cmpSide = false; S.cmp = !S.cmp;
  if (S.cmp) preload("v0");
  draw();
};
$("#cmpSideBtn").onclick = () => {             // 并排对比（左右各一整个，同时播放）
  S.cmp = false; S.cmpSide = !S.cmpSide;
  if (S.cmpSide) preload("v0");
  draw();
};
(() => {
  const ln = $("#cmpLine");
  let on = false;
  ln.addEventListener("pointerdown", (e) => {
    on = true; e.preventDefault();
    try { ln.setPointerCapture(e.pointerId); } catch {}
  });
  ln.addEventListener("pointermove", (e) => {
    if (!on) return;
    const st = $("#stage").getBoundingClientRect(), r = stageRect();
    S.cmpSplit = Math.min(0.94, Math.max(0.06, (e.clientX - st.left - r.x) / Math.max(1, r.w)));
    draw();
  });
  ln.addEventListener("pointerup", () => { on = false; });
  ln.addEventListener("pointercancel", () => { on = false; });
})();
addEventListener("resize", () => S.d && drawSoon());
// 画面区域大小一变（窗口、侧栏、切换标签）：还在“适应”模式就重新适应，视频不会缩在角落里
new ResizeObserver(() => {                                         // 不用 requestAnimationFrame：页面在后台时它会暂停
  const [W, H] = stageSize();
  if (S.d && S.fit && W > 0 && H > 0) fitView(S.fit);
}).observe($("#stage"));

// ---------- 画幅（输出画面）----------
const RATIOS = { "16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1, "4:3": 4 / 3, "4:5": 4 / 5, "1.85:1": 1.85, "2.39:1": 2.39 };
const ratioOf = (r) => (r === "orig" ? S.d.w / S.d.h : RATIOS[r] || 0);   // 0 = 自由
function getFrame() { return S.d.frame || { x: 0, y: 0, w: S.d.w, h: S.d.h, ratio: "orig" }; }
function setFrameMode(on) {
  S.frameMode = on;
  $("#frameBtn").classList.toggle("on", on);
  $("#frameEdit").classList.toggle("on", on);
  if (on) select(null);
  drawFrame();
}
let frameTimer = null;
function applyFrame(f) {
  if (!("crop" in f) && S.d.frame && S.d.frame.crop) f = { ...f, crop: S.d.frame.crop };   // 裁切黑边一直保留，只有还原 / 撤销才去掉
  const sig = `${S.d.frame ? [S.d.frame.x, S.d.frame.y, S.d.frame.w, S.d.frame.h, cropKey()].join(",") : ""}`;
  S.d.frame = { ...f };
  // 裁切黑边 / 改画幅大小或位置：分区叠加层是按画幅几何算的，旧图会被拉伸错位，先丢掉，等新画幅存好再取
  const geomChanged = `${[S.d.frame.x, S.d.frame.y, S.d.frame.w, S.d.frame.h, cropKey()].join(",")}` !== sig;
  if (geomChanged) {
    for (const k of Object.keys(ghostCache)) delete ghostCache[k];
    alphaCache.clear();
    dropZoneCache();
  }
  clearTimeout(frameTimer);
  frameTimer = setTimeout(() => api(`/api/p/${S.pid}/frame`, { json: S.d.frame }).catch((e) => setLog(e.message, true)).then(() => {
    if (!geomChanged) return;
    dropZoneCache();                                              // 服务端已按新画幅算分区：换一份新的，别用存之前取到的那份
    draw();
  }), 300);
  renderFrameProps();
  draw();
}
function commitFrame(f, label, before = null) {
  before = before || { crop: null, ...getFrame() };
  f = { ...f, x: Math.round(f.x), y: Math.round(f.y), w: Math.max(16, Math.round(f.w)), h: Math.max(16, Math.round(f.h)) };
  if (JSON.stringify(f) === JSON.stringify(before)) return;
  S.undo.push({ frame: true, before, after: f, label });
  S.redo = [];
  applyFrame(f);
}
function withRatio(f, ratio, keep = "w") {   // 按比例改尺寸，保持中心
  const r = ratioOf(ratio);
  if (!r) return { ...f, ratio };
  const cx = f.x + f.w / 2, cy = f.y + f.h / 2;
  let w = f.w, h = f.h;
  if (keep === "w") h = w / r; else w = h * r;
  return { x: cx - w / 2, y: cy - h / 2, w, h, ratio };
}

function drawFrame() {
  const svg = $("#frameSvg"), [W, H] = stageSize();
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  if (!S.d || S.compare) { svg.innerHTML = ""; svg._frameHtml = null; return; }
  const v = V(), f = getFrame();
  const X = (x) => v.x + x * v.z, Y = (y) => v.y + y * v.z;
  const x0 = X(f.x), y0 = Y(f.y), fw = f.w * v.z, fh = f.h * v.z;
  let h = "";
  if ($("#frameDim").checked) h += `<path class="fdim" fill-rule="evenodd" d="M-99999 -99999H99999V99999H-99999Z M${x0} ${y0}h${fw}v${fh}h${-fw}Z"/>`;
  const same = f.x === 0 && f.y === 0 && f.w === S.d.w && f.h === S.d.h;
  if (!same) h += `<rect class="videdge" x="${X(0)}" y="${Y(0)}" width="${S.d.w * v.z}" height="${S.d.h * v.z}"/>`;   // 原视频边界
  if (S.frameMode) for (const t of [1 / 3, 2 / 3]) {                                                                // 三分线
    h += `<line class="third" x1="${x0 + fw * t}" y1="${y0}" x2="${x0 + fw * t}" y2="${y0 + fh}"/>`;
    h += `<line class="third" x1="${x0}" y1="${y0 + fh * t}" x2="${x0 + fw}" y2="${y0 + fh * t}"/>`;
  }
  h += `<rect class="fborder ${S.frameMode ? "on" : ""}" x="${x0}" y="${y0}" width="${fw}" height="${fh}"/>`;
  if (S.frameMode) h += `<rect class="fhit" data-fh="move" x="${x0}" y="${y0}" width="${fw}" height="${fh}"/>`;
  const rt = f.ratio && f.ratio !== "free" && f.ratio !== "orig" ? f.ratio : (f.w / f.h).toFixed(2) + ":1";
  h += T`<text class="flabel ${S.frameMode ? "on" : ""}" x="${x0}" y="${y0 - 7}">画幅 ${f.w}×${f.h} · ${rt}</text>`;
  if (S.frameMode) for (const [name, [u, vv]] of Object.entries(HANDLES)) {
    h += `<rect class="fh" data-fh="${name}" x="${x0 + fw * u - 4.5}" y="${y0 + fh * vv - 4.5}" width="9" height="9" style="cursor:${handleCursor(name, 0)}"/>`;
  }
  if (svg._frameHtml !== h) { svg.innerHTML = h; svg._frameHtml = h; }
}

function startFrameDrag(kind, e) {
  e.preventDefault();
  const f0 = { ...getFrame() };
  S.fdrag = { kind, m0: toVideo(e), f0, before: { ...f0 } };
  try { $("#stage").setPointerCapture(e.pointerId); } catch {}
}
function frameMove(e) {
  const D = S.fdrag, f0 = D.f0, m = toVideo(e), k = D.kind;
  const dx = m[0] - D.m0[0], dy = m[1] - D.m0[1], tol = 8 / S.view.z;   // 吸附距离：屏幕上 8 像素
  const snapTo = (val, targets) => { for (const t of targets) if (Math.abs(val - t) < tol) return t; return val; };
  const xs = [0, S.d.w], ys = [0, S.d.h];
  let x0 = f0.x, y0 = f0.y, x1 = f0.x + f0.w, y1 = f0.y + f0.h;
  if (k === "move") {
    let nx = f0.x + dx, ny = f0.y + dy;
    const sx0 = snapTo(nx, xs), sx1 = snapTo(nx + f0.w, xs), scx = snapTo(nx + f0.w / 2, [S.d.w / 2]);
    nx = sx0 !== nx ? sx0 : sx1 !== nx + f0.w ? sx1 - f0.w : scx - f0.w / 2;
    const sy0 = snapTo(ny, ys), sy1 = snapTo(ny + f0.h, ys), scy = snapTo(ny + f0.h / 2, [S.d.h / 2]);
    ny = sy0 !== ny ? sy0 : sy1 !== ny + f0.h ? sy1 - f0.h : scy - f0.h / 2;
    S.d.frame = { ...f0, x: Math.round(nx), y: Math.round(ny) };
  } else {
    if (k.includes("w")) x0 = snapTo(x0 + dx, xs);
    if (k.includes("e")) x1 = snapTo(x1 + dx, xs);
    if (k.includes("n")) y0 = snapTo(y0 + dy, ys);
    if (k.includes("s")) y1 = snapTo(y1 + dy, ys);
    if (e.altKey) {                                                // Alt：以中心为基准，对边同时动
      if (k.includes("w")) x1 = f0.x + f0.w - (x0 - f0.x);
      if (k.includes("e")) x0 = f0.x - (x1 - f0.x - f0.w);
      if (k.includes("n")) y1 = f0.y + f0.h - (y0 - f0.y);
      if (k.includes("s")) y0 = f0.y - (y1 - f0.y - f0.h);
    }
    let w = Math.max(x1 - x0, 16), h = Math.max(y1 - y0, 16);
    const r = ratioOf(f0.ratio) || (e.shiftKey ? f0.w / f0.h : 0);
    if (r) {                                                       // 锁比例
      if (k.length === 2) { if (w / f0.w > h / f0.h) h = w / r; else w = h * r; }
      else if (k === "e" || k === "w") h = w / r;
      else w = h * r;
    }
    const cx = f0.x + f0.w / 2, cy = f0.y + f0.h / 2;
    if (e.altKey) { x0 = cx - w / 2; y0 = cy - h / 2; }
    else {
      x0 = k.includes("w") ? f0.x + f0.w - w : k.includes("e") ? f0.x : cx - w / 2;
      y0 = k.includes("n") ? f0.y + f0.h - h : k.includes("s") ? f0.y : cy - h / 2;
    }
    S.d.frame = { x: Math.round(x0), y: Math.round(y0), w: Math.round(w), h: Math.round(h), ratio: f0.ratio };
  }
  drawFrame();
  renderFrameProps();
}
addEventListener("pointerup", () => {
  if (S.pan) { S.pan = null; $("#stage").classList.remove("panning"); }
  if (S.fdrag) {
    const D = S.fdrag;
    S.fdrag = null;
    const after = { ...getFrame() };
    S.d.frame = D.before;
    commitFrame(after, D.kind === "move" ? T("移动画幅") : T("画幅大小"), D.before);
  }
});

// 画面的上下左右边（裁切黑边后 = 有画面的那几行），四边扩展按它算
function picEdges() {
  const c = getFrame().crop;
  return { t: c ? c.t : 0, b: S.d.h - (c ? c.b : 0), l: 0, r: S.d.w };
}
function frameVals(f) {                                           // 画幅面板每个框的值
  const e = picEdges();
  return { x: f.x, y: f.y, w: f.w, h: f.h, t: e.t - f.y, b: f.y + f.h - e.b, l: e.l - f.x, r: f.x + f.w - e.r };
}
function frameWith(k, v) {                                        // 改一个框 → 新画幅（X/Y/宽/高照比例；四边扩展改成自由比例）
  const f = { ...getFrame() }, e = picEdges(), r = ratioOf(f.ratio);
  if (k === "x" || k === "y" || k === "w" || k === "h") {
    f[k] = v;
    if (r && k === "w") f.h = f.w / r;
    if (r && k === "h") f.w = f.h * r;
    return f;
  }
  const bot = f.y + f.h, right = f.x + f.w;
  if (k === "t") f.y = e.t - v, f.h = bot - f.y;
  if (k === "b") f.h = e.b + v - f.y;
  if (k === "l") f.x = e.l - v, f.w = right - f.x;
  if (k === "r") f.w = e.r + v - f.x;
  f.ratio = "free";
  return f;
}
function renderFrameProps() {
  if (!S.d) return;
  const f = getFrame(), vals = frameVals(f);
  $("#frameRatio").value = f.ratio || "free";
  $$("#framePanel [data-f]").forEach((el) => { if (document.activeElement !== el) el.value = Math.round(vals[el.dataset.f]); });
  $("#frameCropText").textContent = f.crop ? T`已去掉：上 ${f.crop.t} · 下 ${f.crop.b} px` : T("没有去黑边");
  $("#frameLetterbox").hidden = !!f.crop;
  $("#frameCropOff").hidden = !f.crop;
  $("#frameSize").textContent = `${Math.round(f.w)} × ${Math.round(f.h)}`;
  const e = picEdges();                                            // 原视频有画面的部分（去黑边后不含黑边）
  const ix = Math.max(0, Math.min(f.x + f.w, e.r) - Math.max(f.x, e.l)), iy = Math.max(0, Math.min(f.y + f.h, e.b) - Math.max(f.y, e.t));
  const out = 1 - (ix * iy) / (f.w * f.h);
  const cut = 1 - (ix * iy) / ((e.r - e.l) * (e.b - e.t));
  $("#frameInfo").textContent = out < 0.001 && cut < 0.001 ? T("画幅和原视频一样，没有扩也没有裁")
    : (out >= 0.001 ? (uiLanguage === "en" ? `${(out * 100).toFixed(0)}% of the frame is not in the video and gets painted in when generating. `
                                           : `画幅里 ${(out * 100).toFixed(0)}% 是原视频没有的，生成视频时补画。`) : "") +
      (cut >= 0.001 ? (uiLanguage === "en" ? `${(cut * 100).toFixed(0)}% of the video is outside the frame and gets cropped.`
                                           : `原视频有 ${(cut * 100).toFixed(0)}% 在画幅外，会被裁掉。`) : "");
  frameMap(f, e);
}
function frameMap(f, e) {                                          // 示意图：蓝 = 原视频，白框 = 画幅，框里斜线 = 要补画的地方
  const x0 = Math.min(0, f.x), y0 = Math.min(0, f.y), x1 = Math.max(S.d.w, f.x + f.w), y1 = Math.max(S.d.h, f.y + f.h);
  const W = 260, H = 150, k = Math.min(W / (x1 - x0), H / (y1 - y0)), ox = (W - (x1 - x0) * k) / 2, oy = (H - (y1 - y0) * k) / 2;
  const X = (v) => (ox + (v - x0) * k).toFixed(1), Y = (v) => (oy + (v - y0) * k).toFixed(1), L = (v) => (v * k).toFixed(1);
  const bars = (e.t > 0 ? `<rect x="${X(0)}" y="${Y(0)}" width="${L(S.d.w)}" height="${L(e.t)}" class="fm-bar"/>` : "") +
               (e.b < S.d.h ? `<rect x="${X(0)}" y="${Y(e.b)}" width="${L(S.d.w)}" height="${L(S.d.h - e.b)}" class="fm-bar"/>` : "");
  $("#frameMap").innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" aria-hidden="true">
    <defs><pattern id="fmHatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" class="fm-hatch"/></pattern>
      <clipPath id="fmClip"><rect x="${X(f.x)}" y="${Y(f.y)}" width="${L(f.w)}" height="${L(f.h)}"/></clipPath></defs>
    <rect x="${X(f.x)}" y="${Y(f.y)}" width="${L(f.w)}" height="${L(f.h)}" fill="url(#fmHatch)"/>
    <rect x="${X(0)}" y="${Y(0)}" width="${L(S.d.w)}" height="${L(S.d.h)}" class="fm-video"/>${bars}
    <rect x="${X(f.x)}" y="${Y(f.y)}" width="${L(f.w)}" height="${L(f.h)}" class="fm-frame"/>
  </svg><div class="fm-legend"><span><i class="lg-video"></i>${T("原视频")} ${S.d.w}×${S.d.h}</span><span><i class="lg-frame"></i>${T("画幅")}</span>` +
    (f.x < 0 || f.y < e.t || f.x + f.w > S.d.w || f.y + f.h > e.b ? `<span><i class="lg-hatch"></i>${T("要补画")}</span>` : "") + `</div>`;
}
$("#frameBtn").onclick = () => setFrameMode(!S.frameMode);
$("#frameEdit").onclick = () => setFrameMode(!S.frameMode);
$("#frameRatio").onchange = (e) => { if (S.d) commitFrame(withRatio(getFrame(), e.target.value), T("画幅比例")); };
$$("#framePanel [data-f]").forEach((el) => (el.onchange = () => {
  if (!S.d || el.value === "") return;
  commitFrame(frameWith(el.dataset.f, +el.value), "tblr".includes(el.dataset.f) ? T("四边扩展") : T("画幅"));
}));
// 拖标签调数值：按住「X」「上」这些字左右拖（Shift 快 10 倍）；拖的时候实时改画幅，松手记一步撤销
$$("#framePanel .scrub").forEach((lab) => {
  const inp = lab.parentElement.querySelector("input[data-f]");
  lab.addEventListener("click", (e) => e.preventDefault());
  lab.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || !S.d) return;
    e.preventDefault();
    if (document.activeElement && document.activeElement.matches("input, textarea")) document.activeElement.blur();
    const k = inp.dataset.f, x0 = e.clientX, v0 = +inp.value || 0, before = { crop: null, ...getFrame() };
    let moved = false, last = null;
    lab.setPointerCapture(e.pointerId);
    const move = (ev) => {
      const dx = ev.clientX - x0;
      if (!moved && Math.abs(dx) < 2) return;
      moved = true;
      inp.value = Math.round(v0 + dx * (ev.shiftKey ? 10 : 1));
      last = frameWith(k, +inp.value);                            // 和画面上拖画幅一样：拖的时候只改画幅、只重绘画幅，松手才提交（缓存只清一次）
      S.d.frame = { ...last, x: Math.round(last.x), y: Math.round(last.y), w: Math.max(16, Math.round(last.w)), h: Math.max(16, Math.round(last.h)) };
      drawFrame();
      renderFrameProps();
    };
    const up = () => {
      lab.removeEventListener("pointermove", move); lab.removeEventListener("pointerup", up);
      if (moved && last) { const after = { ...getFrame() }; S.d.frame = before; commitFrame(after, "tblr".includes(k) ? T("四边扩展") : T("画幅"), before); }
      else if (!moved) inp.focus();
    };
    lab.addEventListener("pointermove", move); lab.addEventListener("pointerup", up);
  });
});
$("#frameCropOff").onclick = () => {                              // 取消裁切黑边：画幅不动，黑边那几行重新算原视频
  if (!S.d || !getFrame().crop) return;
  commitFrame({ ...getFrame(), crop: null }, T("取消裁切黑边"));
  setLog(T("已取消裁切黑边（画幅没变；要连画幅一起还原点「还原」）"));
};
$("#frameReset").onclick = () => { if (!S.d) return; commitFrame({ x: 0, y: 0, w: S.d.w, h: S.d.h, ratio: "orig", crop: null }, T("还原画幅")); fitView("fitframe"); };
$("#frameLetterbox").onclick = async () => {                      // 裁切黑边：检测上下黑边，确认后画幅 = 中间有画面的部分
  if (!S.d) return;
  setLog(T("正在检测上下黑边…"));
  try {
    const r = await api(`/api/p/${S.pid}/letterbox`);
    if (!r.top && !r.bottom) return setLog(T("没检测到上下黑边"));
    const f = { x: 0, y: r.top, w: r.w, h: r.h - r.top - r.bottom, ratio: "free", crop: { t: r.top, b: r.bottom } };   // crop：黑边里的东西不属于任何图层、看不到
    if (!(await uiConfirm(T`检测到黑边：上 ${r.top} px、下 ${r.bottom} px。\n画幅改成 ${f.w}×${f.h}（去掉黑边）？`, { ok: T("去掉黑边") }))) return setLog(T("没有改画幅"));
    const same = JSON.stringify(getFrame()) === JSON.stringify(f);
    commitFrame(f, T("裁切黑边"));
    if (same) { dropZoneCache(); draw(); }                         // 画幅没变（已经去过黑边）：也要重取分区，别留着压扁的旧叠加层
    fitView("fitframe");
    setLog(T`画幅已去掉黑边：${f.w}×${f.h}（上 ${r.top} px、下 ${r.bottom} px）。可以撤销，或点「还原」回到原视频大小`);
  } catch (e) { setLog(e.message, true); }
};
$("#frameCenter").onclick = () => { if (!S.d) return; const f = getFrame(); commitFrame({ ...f, x: (S.d.w - f.w) / 2, y: (S.d.h - f.h) / 2 }, T("画幅居中")); };
$("#frameDim").onchange = () => drawFrame();

