// 视频 PS 前端 2/9：画面：draw、图层 / 空区绘制、原视频对比、重绘区叠加层、变换把手。
// 9 个文件是同一个全局作用域的普通脚本，按 index.html 里的顺序加载（原来是一个 app.js）。
// ---------- 画面 ----------
const V = () => S.sideView || S.view;   // 并排对比时整块画布被缩到右半边（S.sideView），平时就是普通视图
function stageRect() {   // 原画面在画布上的位置（相对 #stage，屏幕像素）
  if (!S.d) return { x: 0, y: 0, w: 1, h: 1 };
  const v = V();
  return { x: v.x, y: v.y, w: S.d.w * v.z, h: S.d.h * v.z };
}
const kScale = () => stageRect().w / S.d.w;

// 一帧的背景和所有图层必须来自同一帧：全部加载好才切换，否则继续显示上一帧（快速动作时不会错位）
const ready = (im) => !im || (im.complete && im.naturalWidth > 0 && im._decoded !== false);
function frameReady(vid, i) {
  let complete = ready(S.cache[vid][i]);
  if (S.compare) return complete;
  // Do not short-circuit: request every resource for this frame together.
  for (const o0 of objs()) {
    const o = effObj(o0);                                          // 首帧 / 尾帧：蒙版字段用这张图的单图覆盖值
    if (!(o.visible || S.bgVisible)) continue;
    if (!ready(ghostFrames(o)[i])) complete = false;
    if (S.bgVisible && isHole(o) && !ready(holeSamFrames(o)[i])) complete = false;
  }
  if (S.tint && !S.refine && !ready(overlayFrame(i))) complete = false;
  // Playback must obey the same atomic frame rule as stepping.
  if (!S.refine && layerPreviewPending()) complete = false;
  if (!S.refine && (S.bgVisible || S.zones) && (anyLifted() || imgEditing()) && !layerPreviewPending()) {
    if (!zoneLabels(i, cellMode())) complete = false;              // 按格时等这一帧的按格图，否则网格会在播放中消失
  }
  return complete;
}
function draw() {
  if (!S.d) return;
  S.sideLay = null; S.sideView = null;
  if (S.cmpSide && S.tab === "video") {
    S.sideLay = sideLayout();
    S.sideView = { z: S.sideLay.s, x: S.sideLay.vx, y: S.sideLay.vy };
  }
  const pcv = !!pcFrame(S.i);                                      // 整段缓存：这一帧已经有合成好的整帧图
  const vid = pcv ? PC_VID : S.compare ? "v0" : S.d.current;
  preload(vid);
  const bg = S.bgVisible || S.compare || !!S.refine;               // 调整抠图：一律显示原画面
  if (pcv || frameReady(vid, S.i)) { S.fi = S.i; S.fiVid = vid; }
  else {
    previewStatus(loadingText() || T('正在准备完整预览帧，当前画面尚未更新…'));
    return;
  }
  const im = S.cache[vid][S.fi], cvb = $("#base");
  if (ready(im) && im) {
    if (cvb.width !== im.naturalWidth || cvb.height !== im.naturalHeight) { cvb.width = im.naturalWidth; cvb.height = im.naturalHeight; }
    if (cvb._image !== im) { cvb.getContext("2d").drawImage(im, 0, 0); cvb._image = im; }
  }
  cvb.style.visibility = (bg || pcv) ? "visible" : "hidden";     // 隐藏背景：只剩抠出来的主体，底下是透明棋盘格（缓存帧本身就是整帧，照样要显示）
  const r = stageRect();
  const box = { left: r.x + "px", top: r.y + "px", width: r.w + "px", height: r.h + "px" };
  const crop = getFrame().crop, ck = r.h / S.d.h;                 // 裁切黑边：黑边那几行整个不显示（原画面、图层、叠加层、修图都看不到）
  $("#stage").style.setProperty("--ct", (crop ? crop.t * ck : 0) + "px");
  $("#stage").style.setProperty("--cb", (crop ? crop.b * ck : 0) + "px");
  const pb = pcv && im && im._pcBox;                              // 整段缓存帧：按它自己的范围摆（画幅扩大时比原视频大）
  Object.assign($("#base").style, pb ? { left: r.x + pb.x0 * ck + "px", top: r.y + pb.y0 * ck + "px",
    width: (pb.x1 - pb.x0) * ck + "px", height: (pb.y1 - pb.y0) * ck + "px" } : box);
  $("#base").classList.toggle("pc-wide", !!pb);
  Object.assign($("#ov").style, box);
  Object.assign($("#bgChecker").style, { left: r.x + "px", top: r.y + "px", width: r.w + "px", height: r.h + "px" });
  const fr = getFrame(), fc = $("#frameChecker");                 // 画幅里原视频外的部分：空白（透明），生成时才补
  const vv = V();                                                 // 可能是并排对比的缩小视图
  fc.hidden = S.compare;
  Object.assign(fc.style, { left: vv.x + fr.x * vv.z + "px", top: vv.y + fr.y * vv.z + "px",
                            width: fr.w * vv.z + "px", height: fr.h * vv.z + "px" });
  $("#bgChecker").hidden = bg || pcv;
  $("#ov").hidden = S.compare || !S.tint || !!S.refine || pcv;
  Object.assign($("#zoneCv").style, { left: vv.x + fr.x * vv.z + "px", top: vv.y + fr.y * vv.z + "px",   // 叠加层按画幅大小（扩图区也标）
                                       width: fr.w * vv.z + "px", height: fr.h * vv.z + "px" });
  const ss = $("#stillShow"), showing = !!(S.stillShow && imgEditing());   // 首帧 / 尾帧：鼠标移到候选上，画幅里直接显示那张候选
  Object.assign(ss.style, { left: vv.x + fr.x * vv.z + "px", top: vv.y + fr.y * vv.z + "px", width: fr.w * vv.z + "px", height: fr.h * vv.z + "px" });
  if (showing && ss.getAttribute("src") !== S.stillShow) ss.src = S.stillShow;
  ss.hidden = !showing;
  if (pcv) {                                                       // 缓存帧已经把图层、透明区、叠加层合成在图上，不用再单独画
    $("#zoneCv").hidden = true;
    $("#zoneLegend").hidden = true;
    $("#layers").hidden = true;
  } else drawZones();
  if (S.tint && !S.compare && !pcv) {
    const overlay = overlayFrame(S.fi), cv = $("#ov");
    if (ready(overlay)) {
      if (cv.width !== overlay.naturalWidth || cv.height !== overlay.naturalHeight) { cv.width = overlay.naturalWidth; cv.height = overlay.naturalHeight; }
      const ctx = cv.getContext("2d"); ctx.clearRect(0, 0, cv.width, cv.height); ctx.drawImage(overlay, 0, 0);
    }
  }
  drawMaskView(box);
  $("#counter").textContent = `${S.fi ?? S.i} / ${S.d.n - 1}` + (S.compare ? T("（原视频）") : "");
  previewStatus(pcv || S.compare ? '' : loadingText());
  if (!pcv) drawLayers();
  drawCmp(r);
  placeRefineCanvas();
  placeShapeCanvas();
  drawFrame();
  drawGizmo();
  drawTimeline();
  $("#zoomSel").options[0].text = Math.round(V().z * 100) + "%";
  if (typeof pcSync === "function") pcSync();                      // 缓存按钮跟着图层状态变（已缓存 / 重新缓存）
  if (typeof uiAfterDraw === "function") uiAfterDraw();            // 新外壳：选中浮动条 / 图层数（只动界面）
}

// ---------- 原视频 / 当前 对比：①拖动对比（左半边原视频，竖线可拖）②并排对比（左右各一整个，同时播放）----------
function sideLayout() {   // 并排对比：两个完整画面左右排开，缩放到刚好放得下
  const [W, H] = stageSize();
  const fr = getFrame();
  const pad = 16, gap = 22, lab = 26;
  const availW = Math.max(80, W - pad * 2 - gap), availH = Math.max(60, H - pad * 2 - lab);
  const s = Math.min(availW / Math.max(1, S.d.w + fr.w), availH / Math.max(1, Math.max(fr.h, S.d.h)));
  const vw = S.d.w * s, vh = S.d.h * s, fw = fr.w * s, fh = fr.h * s;
  const maxH = Math.max(fh, vh), rowW = vw + gap + fw;
  const fx = Math.round(pad + Math.max(0, (W - pad * 2 - rowW) / 2) + vw + gap);
  const fy = Math.round(pad + lab + (availH - maxH) / 2 + (maxH - fh) / 2);
  const vx = fx - fr.x * s, vy = fy - fr.y * s;                     // 右半边里“原视频那块”的位置
  const lx = Math.round(vx - gap - vw), ly = Math.round(vy);
  return { s, vw, vh, vx, vy, fw, fh, fx, fy, lx, ly, rowR: Math.round(fx + fw) };
}

function drawCmp(r) {
  const wrap = $("#cmpWrap"), cv = $("#cmpCv"), line = $("#cmpLine"), tags = $("#cmpTags");
  const can = S.tab === "video" && !!S.d;
  const side = can && S.cmpSide, wipe = can && !S.compare && S.cmp && !side;
  document.body.classList.toggle("cmp-on", wipe);
  document.body.classList.toggle("cmp-side", side);
  wrap.hidden = tags.hidden = !(wipe || side);
  line.hidden = !wipe;
  $("#cmpBtn").classList.toggle("on", wipe);
  $("#cmpBtn").setAttribute("aria-pressed", wipe ? "true" : "false");
  $("#cmpBtn").title = wipe ? T("拖动对比中：再点一下关掉") : T("拖动对比：左原视频、右当前，拖中间竖线");
  $("#cmpSideBtn").classList.toggle("on", side);
  $("#cmpSideBtn").setAttribute("aria-pressed", side ? "true" : "false");
  $("#cmpSideBtn").title = side ? T("并排对比中：再点一下关掉") : T("并排对比：左右各一整个画面");
  if (!(wipe || side)) return;
  preload("v0");
  const im = S.cache["v0"][S.fi];                                 // 两边用同一帧，才对得上
  if (ready(im) && im) {
    if (cv.width !== im.naturalWidth || cv.height !== im.naturalHeight) {
      cv.width = im.naturalWidth; cv.height = im.naturalHeight; cv._f = null;
    }
    if (cv._f !== S.fi) { cv._f = S.fi; cv.getContext("2d").drawImage(im, 0, 0); }
  }
  const L = S.sideLay;
  if (side && L) {                                                 // 并排：左一整个原视频，右一整个现在的画面
    Object.assign(wrap.style, { left: L.lx + "px", top: L.ly + "px", width: L.vw + "px", height: L.vh + "px" });
    Object.assign(cv.style, { width: L.vw + "px", height: L.vh + "px" });
    Object.assign(tags.style, { left: L.lx + "px", top: Math.max(4, L.ly - 26) + "px", width: (L.rowR - L.lx) + "px" });
    $("#stage").style.setProperty("--cmpsplit", "0px");
    return;
  }
  const split = Math.round(r.w * S.cmpSplit);
  Object.assign(wrap.style, { left: r.x + "px", top: r.y + "px", width: split + "px", height: r.h + "px" });
  Object.assign(cv.style, { width: r.w + "px", height: r.h + "px" });
  Object.assign(line.style, { left: r.x + split + "px", top: r.y + "px", height: r.h + "px" });
  Object.assign(tags.style, { left: r.x + "px", top: r.y + 8 + "px", width: r.w + "px" });
  $("#stage").style.setProperty("--cmpsplit", split + "px");
}

// 主体轮廓重绘为负数：把图层画布里贴边的 r 像素换成棋盘格（腐蚀 = 往各方向挪动后取交集）
let shrinkTile = null;
function checkerTile() {
  if (!shrinkTile) {
    shrinkTile = document.createElement("canvas"); shrinkTile.width = shrinkTile.height = 16;
    const c = shrinkTile.getContext("2d");
    c.fillStyle = "#cdcdcd"; c.fillRect(0, 0, 16, 16);
    c.fillStyle = "#969696"; c.fillRect(0, 0, 8, 8); c.fillRect(8, 8, 8, 8);
  }
  return shrinkTile;
}
// 主体局部保留（keep_masks）：形状按这一帧主体抠图的外接框摆上去（和生成一样跟着主体走），范围外的主体像素换成棋盘格
const keepImgs = new Map(), alphaBoxes = new WeakMap(), keepTmp = [document.createElement("canvas"), document.createElement("canvas")];
function keepImg(id) {
  const k = S.pid + "/" + id;
  let im = keepImgs.get(k);
  if (!im) { im = new Image(); im.onload = () => { for (const el of $$("#layers canvas.lyr")) el._paintKey = null; drawSoon(); }; im.src = `/api/p/${S.pid}/keep/${id}.png`; keepImgs.set(k, im); }
  return im;
}
const keepOn = (o) => (o.keep_masks || []).filter((m) => !m.off && (m.start == null || S.i >= m.start) && (m.end == null || S.i <= m.end));
function alphaBox(img) {                                             // 抠图（alpha > 127）的外接框，按图片缓存
  if (alphaBoxes.has(img)) return alphaBoxes.get(img);
  const c = { width: img.naturalWidth, height: img.naturalHeight }, d = readPixels(img);
  let x0 = c.width, y0 = c.height, x1 = -1, y1 = -1;
  for (let y = 0, k = 3; y < c.height; y++) for (let xx = 0; xx < c.width; xx++, k += 4) if (d[k] > 127) { if (xx < x0) x0 = xx; if (xx > x1) x1 = xx; if (y < y0) y0 = y; if (y > y1) y1 = y; }
  const b = x1 < 0 ? null : { x: x0, y: y0, w: x1 - x0 + 1, h: y1 - y0 + 1 };
  alphaBoxes.set(img, b);
  return b;
}
function keepCut(el, src, o) {
  const ims = keepOn(o).map((m) => keepImg(m.id)).filter((im) => im.complete && im.naturalWidth);
  const b = ims.length && alphaBox(src);
  if (!b) return;
  const [rc, bc] = keepTmp, W = el.width, H = el.height;
  for (const t of keepTmp) { t.width = W; t.height = H; }
  const r = rc.getContext("2d");
  r.imageSmoothingEnabled = false;
  for (const im of ims) r.drawImage(im, b.x, b.y, b.w, b.h);
  const x = bc.getContext("2d");
  x.drawImage(el, 0, 0);
  x.globalCompositeOperation = "destination-out"; x.drawImage(rc, 0, 0);
  x.globalCompositeOperation = "source-in"; x.fillStyle = x.createPattern(checkerTile(), "repeat"); x.fillRect(0, 0, W, H);
  x.globalCompositeOperation = "source-over";
  const c = el.getContext("2d");
  c.globalCompositeOperation = "destination-in"; c.drawImage(rc, 0, 0);
  c.globalCompositeOperation = "source-over"; c.drawImage(bc, 0, 0);
}
// 往里收 r 像素：在 CPU 上一次算完（读一次像素 → 横竖两遍取最小 → 写一次）。原来往各方向挪着画十几次再取交集，
// 一层一帧十几毫秒，主体轮廓重绘为负数的项目播放会卡（SVG feMorphology 滤镜在 canvas 里走软件路径更慢）
let shrinkA = null, shrinkB = null;
function shrinkEdge(el, src, r) {
  if (r < 0.5) return;
  const W = el.width, H = el.height, N = W * H, R = Math.max(1, Math.round(r));
  const d = readPixels(src);
  if (!shrinkA || shrinkA.length !== N) { shrinkA = new Uint8Array(N); shrinkB = new Uint8Array(N); }
  const A = shrinkA, B = shrinkB;
  let x0 = W, x1 = -1, y0 = H, y1 = -1;                              // 只算图层的外接框（图层通常只占画面一小块）
  for (let y = 0, k = 0; y < H; y++) for (let x = 0; x < W; x++, k++) {
    const a = d[k * 4 + 3]; A[k] = a;
    if (a) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
  }
  if (x1 < 0) return;
  for (let y = y0; y <= y1; y++) {                                   // 横向取最小（框外 / 画面外都是透明）
    const row = y * W;
    for (let x = x0; x <= x1; x++) {
      if (x - R < x0 || x + R > x1) { B[row + x] = 0; continue; }
      let m = A[row + x - R];
      for (let xx = x - R + 1; xx <= x + R; xx++) { const v = A[row + xx]; if (v < m) m = v; }
      B[row + x] = m;
    }
  }
  const out = new ImageData(W, H), q = out.data;
  for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {    // 竖向取最小 = 收完的 alpha；收掉的那圈画成棋盘格
    const k = y * W + x, o = k * 4, a = d[o + 3];
    if (!a) continue;
    let e = 0;
    if (y - R >= y0 && y + R <= y1) {
      e = B[k - R * W];
      for (let yy = y - R + 1; yy <= y + R; yy++) { const v = B[yy * W + x]; if (v < e) e = v; }
    }
    if (e >= a) { q[o] = d[o]; q[o + 1] = d[o + 1]; q[o + 2] = d[o + 2]; q[o + 3] = a; continue; }
    const g = ((x >> 3) + (y >> 3)) & 1 ? 0x96 : 0xcd, t = e / a;  // 和 checkerTile 一样的灰格子
    q[o] = d[o] * t + g * (1 - t); q[o + 1] = d[o + 1] * t + g * (1 - t); q[o + 2] = d[o + 2] * t + g * (1 - t); q[o + 3] = a;
  }
  el.getContext("2d").putImageData(out, 0, 0);
}

function drawLayers() {
  const box = $("#layers");
  const r = stageRect();
  Object.assign(box.style, { left: r.x + "px", top: r.y + "px", width: r.w + "px", height: r.h + "px" });
  box.hidden = S.compare;
  const k = r.w / S.d.w;
  // 调整抠图时所有图层回到原位、不挖洞：只看这一帧原画面 + 正在改的抠图，清楚看到抠进了什么；保存 / 退出后恢复
  const list = S.refine ? [] : layersFrontToBack().slice().reverse().map(effObj).filter(o=>layerVisible(o));   // 从后往前画
  const holes = S.bgVisible && !S.refine ? objs().map(effObj).filter(isHole) : [];

  const ids = new Set([...list.map((o) => o.id), "holes"]);
  [...box.children].forEach((el) => ids.has(el.dataset.id) || el.remove());
  // 空区属于背景，画在所有图层下面。用 canvas 同步绘制（图层帧已预加载），每一帧都保证画出来
  let cv = box.querySelector('canvas[data-id="holes"]');
  if (!cv) { cv = document.createElement("canvas"); cv.dataset.id = "holes"; cv.className = "holes"; }
  if (box.firstElementChild !== cv) box.insertBefore(cv, box.firstElementChild);
  drawHoles(cv, holes);
  for (const [index, o] of list.entries()) {
    let el = box.querySelector(`canvas.lyr[data-id="${o.id}"]`);
    if (!el) { el = document.createElement("canvas"); el.className = "lyr"; el.dataset.id = o.id; }
    if (box.children[index + 1] !== el) box.insertBefore(el, box.children[index + 1] || null); // 顺序未变时不移动 DOM 节点
    // 抠图的收缩 / 扩展只管图层带走哪些像素：新位置贴的就是扩 / 缩之后的抠图（2026-11-10「按 PS 的做法」）
    const fg = ghostFrames(o)[S.fi], fr = fg;
    el.hidden = !fr;
    const shrink = Math.max(0, -(+o.ring_redraw || 0));          // 主体轮廓重绘为负数：身上贴边那一圈留空（棋盘格），和生成一样
    const keepKey = keepOn(o).map((m) => m.id + (keepImg(m.id).complete ? "+" : "-")).join();   // 主体局部保留：这一帧生效的那些
    const paintKey = [fr, !!(S.growLine && S.sel.has(o.id)), shrink, keepKey];
    if (ready(fr) && fr && (!el._paintKey || paintKey.some((v, i) => v !== el._paintKey[i]))) {
      if (el.width !== fr.naturalWidth || el.height !== fr.naturalHeight) { el.width = fr.naturalWidth; el.height = fr.naturalHeight; }
      const c = el.getContext("2d");
      c.clearRect(0, 0, el.width, el.height);
      c.drawImage(fr, 0, 0);
      if (shrink) shrinkEdge(el, fr, shrink * el.width / S.d.w);
      if (keepKey) keepCut(el, fr, o);
      if (S.growLine && S.sel.has(o.id) && ready(fg) && fg) {   // 调收缩 / 扩展：青 = 原始抠图边，黄 = 现在的边（跟着图层变换走）
        const f0 = ghostFrames({ ...o, grow: 0 })[S.fi];
        if (ready(f0) && f0) c.drawImage(edgeOf(f0, [0, 230, 255]), 0, 0, el.width, el.height);
        c.drawImage(edgeOf(fg, [255, 212, 0]), 0, 0, el.width, el.height);
      }
      el._paintKey = paintKey;
    }
    const origin = apply(o, [0,0]), xAxis = apply(o,[1,0]), yAxis = apply(o,[0,1]);
    el.style.transformOrigin = "0 0";
    el.style.transform = `matrix(${xAxis[0]-origin[0]},${xAxis[1]-origin[1]},${yAxis[0]-origin[0]},${yAxis[1]-origin[1]},${origin[0]*k},${origin[1]*k})`;
    el.style.opacity = o.opacity ?? 1;
    el.classList.toggle("sel", S.sel.has(o.id));
    el.classList.toggle("hover", S.hover === o.id && !S.sel.has(o.id));
  }
}

// 抠图的轮廓线（透明通道的边，约 2 像素宽），按图片缓存
const edgeCache = new WeakMap();
function edgeOf(im, rgb) {
  const key = rgb.join();
  let m = edgeCache.get(im);
  if (!m) edgeCache.set(im, (m = {}));
  if (m[key]) return m[key];
  const W = im.naturalWidth, H = im.naturalHeight, cv = document.createElement("canvas");
  cv.width = W; cv.height = H;
  const ctx = cv.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(im, 0, 0);
  const src = ctx.getImageData(0, 0, W, H).data, out = new ImageData(W, H), q = out.data;
  const inn = (x, y) => x >= 0 && y >= 0 && x < W && y < H && src[(y * W + x) * 4 + 3] > 127;
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const a = inn(x, y);
    if (a === inn(x + 1, y) && a === inn(x - 1, y) && a === inn(x, y + 1) && a === inn(x, y - 1)) continue;
    const k = (y * W + x) * 4;
    q[k] = rgb[0]; q[k + 1] = rgb[1]; q[k + 2] = rgb[2]; q[k + 3] = 255;
  }
  ctx.putImageData(out, 0, 0);
  return (m[key] = cv);
}

// 原位置空出来的地方（移走 / 隐藏的图层留下的空区）：一律透明棋盘格。空就是空，预览里不铺背景图，只有生成时才补
const holeTmp = document.createElement("canvas");
let checkerPat = null;
// 挖空使用 SAM 与当前柔边的并集，覆盖强度必须为 100%。
// 负外扩只收缩前景，不能让已识别的原主体从背景重新露出来。
function holeSamFrames(o) {
  return ghostFrames({ ...o, edge: "sam", grow: Math.max(0, o.grow ?? 0) });
}
const opaqueHoleCache = new WeakMap();
function opaqueHole(im, sam) {
  if (im === sam) return sam; // SAM 硬遮罩已经是全不透明，无需逐像素读回。
  let variants = opaqueHoleCache.get(im);
  if (!variants) { variants = new WeakMap(); opaqueHoleCache.set(im, variants); }
  if (variants.has(sam)) return variants.get(sam);
  const c = document.createElement("canvas"); c.width = im.naturalWidth; c.height = im.naturalHeight;
  const x = c.getContext("2d", { willReadFrequently: true });
  x.drawImage(im, 0, 0); x.drawImage(sam, 0, 0, c.width, c.height);
  const pixels = x.getImageData(0, 0, c.width, c.height);
  for (let k = 3; k < pixels.data.length; k += 4) pixels.data[k] = pixels.data[k] > 0 ? 255 : 0;
  x.putImageData(pixels, 0, 0);
  variants.set(sam, c);
  return c;
}
function drawHoles(cv, holes) {
  // 新遮罩尚未到达时保留上一张洞图，不先清空而短暂露出原人物。
  if (holes.some(o => !ready(ghostFrames(o)[S.fi]) || !ready(holeSamFrames(o)[S.fi]))) return;
  const first = holes.map((o) => ghostFrames(o)[S.fi]).find((im) => im && im.naturalWidth);
  const W = first ? first.naturalWidth : 960, H = first ? first.naturalHeight : Math.round(960 * S.d.h / S.d.w);
  if (cv.width !== W || cv.height !== H) { cv.width = W; cv.height = H; holeTmp.width = W; holeTmp.height = H; }
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, W, H);
  const zm = S.bgVisible && !S.refine && !S.drag && !S.xdrag && !layerPreviewPending() ? zoneLabels(S.fi) : null;
  if (!holes.length && !zm) return;
  if (!checkerPat) {                                        // 棋盘格图案
    const t = document.createElement("canvas"); t.width = t.height = 16;
    const c = t.getContext("2d");
    c.fillStyle = "#cdcdcd"; c.fillRect(0, 0, 16, 16);
    c.fillStyle = "#969696"; c.fillRect(0, 0, 8, 8); c.fillRect(8, 8, 8, 8);
    checkerPat = ctx.createPattern(t, "repeat");
  }
  const tc = holeTmp.getContext("2d");
  for (const o of holes) {
    const im = ghostFrames(o)[S.fi];
    if (!im || !im.naturalWidth) continue;
    tc.globalCompositeOperation = "source-over";
    tc.clearRect(0, 0, W, H);
    const sam = holeSamFrames(o)[S.fi];
    if (!sam || !sam.naturalWidth) continue;
    tc.drawImage(opaqueHole(im, sam), 0, 0, W, H);           // 原位置用完全不透明的洞，不沿用柔边透明度
    tc.globalCompositeOperation = "source-in";
    tc.fillStyle = checkerPat; tc.fillRect(0, 0, W, H);      // 空白（透明棋盘格）
    ctx.drawImage(holeTmp, 0, 0);
  }
  if (zm) {
    const m = maskCanvas(zm, [2], W, H);
    tc.globalCompositeOperation = "source-over";
    tc.clearRect(0, 0, W, H);
    const fr = getFrame(), q = zm.w / fr.w;                // 区号图是画幅大小：只取原视频那一块
    tc.drawImage(m, -fr.x * q, -fr.y * q, S.d.w * q, S.d.h * q, 0, 0, W, H);
    tc.globalCompositeOperation = "source-in";
    tc.fillStyle = checkerPat;
    tc.fillRect(0, 0, W, H);
    ctx.drawImage(holeTmp, 0, 0);
  }
}

// ---------- 重绘范围（服务端按当前保存的图层算，和生成用的是同一套分区）----------
// 区号（regions.py）：0 保留区，1 重绘（不是空区），2 空区（粗剪涂灰，一定重绘）
S.zoneMode = 0; S.zones = false; S.zoneGen = 0;                                  // 默认不显示重绘范围（算得慢，2026-11-09 用户定），M 打开
const zoneImgs = new Map(), zoneDecoded = new Map(), zoneRetry = new Map();
let zoneInFlight = 0;
// 首帧 / 尾帧标签：只有这一帧，叠加层 = 这张图的补图范围（服务端按视频编辑 + 单图覆盖 + 图片遮罩选项算，和出候选时一样）
const cellMode = () => S.zoneMode === 2 && !imgEditing();          // 正在看“按格”：画面、预取、等待都用同一套图
function dropZoneCache() {                                          // 画幅/融合带变了：分区叠加层按新几何重取
  zoneImgs.clear(); zoneDecoded.clear(); zoneRetry.clear();
  S.zoneGen = (S.zoneGen || 0) + 1;
}
const frameSig = () => { const f = S.d && S.d.frame; return f ? `${f.x},${f.y},${f.w},${f.h}` : ""; };   // 画幅几何也决定分区图尺寸，缓存键里必须带
const zoneKey = (i, cells = false) => imgEditing() ? `${S.pid}:still:${imgEditing().id}:${frameSig()}:${S.zoneGen}:${S.stillGen}` : `${S.pid}:${S.d.current}:${frameSig()}:${S.zoneGen}:${i}${cells ? ":cells" : ""}`;
function trimZoneCache() {
  // Evict matching decoded/image entries together. Clearing only decoded data
  // leaves an already-loaded Image that can never fire onload again.
  let bytes = 0;
  for (const z of zoneDecoded.values()) bytes += z.length * 9; // labels + two RGBA canvases
  for (const [key, z] of zoneDecoded) {
    if (zoneDecoded.size <= 96 && bytes <= 96 * 1024 * 1024) break;
    if ([zoneKey(S.fi), zoneKey(S.i), zoneKey(S.fi, true), zoneKey(S.i, true)].includes(key)) continue;
    zoneDecoded.delete(key); zoneImgs.delete(key); zoneRetry.delete(key);
    bytes -= z.length * 9;
  }
}
function preloadZones(i) {
  if (!S.d || S.compare || S.refine || !(S.bgVisible || S.zones) || !(anyLifted() || imgEditing()) || layerPreviewPending()) return;
  // Reserve one of two slots for the exact target; no whole-clip request burst.
  const cells = cellMode();                                        // 看“按格”时预取的也必须是按格图，否则切帧时网格会空
  zoneLabels(i, cells);
  if (imgEditing()) return;
  const span = S.end - S.start + 1;
  const ahead = cells && S.playing ? 10 : 6;                       // 按格播放多领先几帧，网格不会在换帧时消失
  for (let k = 1; k <= ahead && k < span && zoneInFlight < 2; k++)
    zoneLabels(S.start + ((i - S.start + k) % span + span) % span, cells);
}
const anyLifted = () => {
  if (!S.d) return false;
  const f = getFrame();
  return f.x !== 0 || f.y !== 0 || f.w !== S.d.w || f.h !== S.d.h ||
    objs().some((o) => isHole(o) || extList(o).some((m) => extOn(m)));
};
// 蒙版视图（A）：选中图层这一帧的蒙版（和生成用的同一份：清理、收缩 / 扩展、手动修改都算上），黑底白色，摆在原视频位置
const mvTmp = document.createElement("canvas");
function maskViewObj() {
  const os = selected();
  return S.maskView && os.length === 1 && !os[0].shape ? os[0] : null;
}
function drawMaskView(box) {
  const cv = $("#maskViewCv"), o = maskViewObj();
  cv.hidden = !o || S.compare || !!S.refine || S.tab !== "video";
  $("#maskView").classList.toggle("on", !!S.maskView);
  if (cv.hidden) return;
  Object.assign(cv.style, box);
  const im = ghostFrames(o)[S.fi];
  const W = im && im.naturalWidth || cv.width || 960, H = im && im.naturalHeight || cv.height || Math.round(960 * S.d.h / S.d.w);
  if (cv.width !== W || cv.height !== H) { cv.width = mvTmp.width = W; cv.height = mvTmp.height = H; }
  const ctx = cv.getContext("2d");
  ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, H);
  if (!ready(im) || !im || !im.naturalWidth) return;              // 这一帧图层没出现（或还在加载）= 全黑
  const tc = mvTmp.getContext("2d");
  tc.globalCompositeOperation = "source-over"; tc.clearRect(0, 0, W, H); tc.drawImage(im, 0, 0, W, H);
  tc.globalCompositeOperation = "source-in"; tc.fillStyle = "#fff"; tc.fillRect(0, 0, W, H);
  ctx.drawImage(mvTmp, 0, 0);
}
function toggleMaskView() {
  S.maskView = !S.maskView;
  if (S.maskView && !maskViewObj()) setLog(T("Alpha 视图：先选中一个图层"));
  draw();
}
function toggleZones() {
  S.zoneMode = ((S.zoneMode || 0) + 1) % 3;
  S.zones = S.zoneMode !== 0;
  $("#zoneBtn").classList.toggle("on", S.zones);
  draw();
}
// 读一张图的像素：同一块 CPU 画布反复用（willReadFrequently：不放 GPU，getImageData 不用每次从显卡读回，快很多）
const readCv = document.createElement("canvas"), readCtx = readCv.getContext("2d", { willReadFrequently: true });
function readPixels(img) {
  const W = img.naturalWidth || img.width, H = img.naturalHeight || img.height;
  if (readCv.width !== W || readCv.height !== H) { readCv.width = W; readCv.height = H; } else readCtx.clearRect(0, 0, W, H);
  readCtx.drawImage(img, 0, 0);
  return readCtx.getImageData(0, 0, W, H).data;
}
function zoneLabels(i, cells = false) {                                           // 这一帧的区号图（Uint8Array，预览分辨率）；还没加载好 = null
  if (!S.d || !(anyLifted() || imgEditing())) return null;
  const key = zoneKey(i, cells);
  if (zoneDecoded.has(key)) return zoneDecoded.get(key);
  if (!zoneImgs.has(key)) {
    if (zoneInFlight >= 2) return null; // 分区计算不能挤占播放帧的加载队列
    zoneInFlight++;
    const im = new Image();
    im.onload = () => {
      zoneInFlight--;
      if (key !== zoneKey(i, cells)) { zoneImgs.delete(key); return; }
      const W = im.naturalWidth, H = im.naturalHeight, d = readPixels(im), z = new Uint8Array(W * H);
      for (let k = 0; k < z.length; k++) z[k] = Math.round(d[k * 4] / 60);
      z.cells = cells; z.w = W; z.h = H;
      zoneDecoded.set(key, z);
      // Prepare the two normal display surfaces before their playback deadline.
      maskCanvas(z, [2]);
      if (S.zones) zonePattern(z, S.zoneFocus);
      trimZoneCache();
      if (S.playing) preloadZones(S.i);
      if (!S.playing && key === zoneKey(i, cells)) drawSoon(); // 也唤醒等待中但尚未呈现的 S.i；释放并发名额后重试当前帧
    };
    im.onerror = () => {                                           // 请求失败（服务端忙、断线）：过一秒重新请求，不让这一帧一直空着
      zoneInFlight--;
      zoneImgs.delete(key);
      const n = (zoneRetry.get(key) || 0) + 1;
      zoneRetry.set(key, n);
      if (cells || n <= 5) setTimeout(() => { if (key === zoneKey(i, cells) && Math.abs(i - S.fi) <= 10) draw(); }, Math.min(5000, 1000 * n));
    };
    const still = imgEditing();
    im.src = still ? `/api/p/${S.pid}/images/${still.id}/region?g=${S.zoneGen}_${S.stillGen}_${rev()}&t=${zoneRetry.get(key) || 0}`
                   : `/api/p/${S.pid}/zones/${i}.png?mode=${cells ? "cells" : "pixels"}&g=${S.zoneGen}&v=${S.d.current}&r=${rev()}&t=${zoneRetry.get(key) || 0}`;
    zoneImgs.set(key, im);
  }
  return null;
}
function maskCanvas(z, k, W, H) {                                  // 区号在 k 里的地方不透明，其余透明（每帧只算一次）
  const ks = [].concat(k), ck = "m" + ks.join("_");
  if (z[ck]) return z[ck];
  const c = document.createElement("canvas"); c.width = z.w; c.height = z.h;
  const x = c.getContext("2d"), img = x.createImageData(z.w, z.h);
  for (let p = 0; p < z.length; p++) if (ks.includes(z[p])) img.data[p * 4 + 3] = 255;
  x.putImageData(img, 0, 0);
  z[ck] = c;
  return c;
}
const HINT0 = $("#hint").textContent;
function drawZones() {
  const cv = $("#zoneCv"), show = S.zones && !S.compare && !S.refine && (S.tab === "video" || !!imgEditing()) && !S.stillShow;
  $("#hint").textContent = !S.zones && S.d && anyLifted()                    // 关掉了重绘范围：提醒怎么打开
    ? T("按 M 显示重绘范围，按 A 看选中图层的抠图") : HINT0;
  $("#zoneBtn").classList.toggle("on", S.zones);
  document.body.classList.toggle("zones-on", show);              // 画幅里原视频外的部分（扩图）也标成完全重绘
  $("#zoneLegend").hidden = !show;
  const cells = S.zoneMode === 2 && !imgEditing();
  $("#zlPixels").hidden = cells;
  $("#zlCells").hidden = !cells;
  if (!show) { cv.hidden = true; return; }
  if (!anyLifted() && !imgEditing()) { cv.hidden = true; $("#zlWait").textContent = T("· 图层都没动过，没有要重绘的地方"); return; }
  const pending = layerPreviewPending();
  if (S.drag || S.xdrag) { cv.hidden = true; $("#zlWait").textContent = T('· 正在调整，松开鼠标后更新透明区域'); return; }
  if (pending) { cv.hidden = true; $("#zlWait").textContent = "· " + pending; return; }
  const z = zoneLabels(S.fi, cells);
  if (!z) {
    cv.hidden = true;
    $("#zlWait").textContent = T("· 当前帧分区计算中…");
    return;
  }
  $("#zlWait").textContent = cells ? T("· 预览轮廓可能差半格") : (S.fi !== S.i ? T("· 正在准备下一帧…") : T("· 像素"));
  cv.hidden = false;
  if (cv.width !== z.w || cv.height !== z.h) { cv.width = z.w; cv.height = z.h; }
  const cx = cv.getContext("2d");
  cx.clearRect(0, 0, cv.width, cv.height);
  cx.drawImage(zonePattern(z, S.zoneFocus), 0, 0);
  for (let k = 1; k <= (S.playing ? 0 : 1); k++) if (S.fi + k < S.d.n) zoneLabels(S.fi + k, cells);   // 顺手预取后面几帧（播放时多取一些）
}
function zonePattern(z, focus = null) {                            // 这一帧的红斜线（空区）/ 蓝点（重绘）/ 绿点（主体）图案：只画一次，之后直接贴
  z.pats = z.pats || {};                                           // focus：只亮这一类区（0 = 都调暗，看清人本身），别的调暗
  if (z.pats[focus]) return z.pats[focus];
  const c = document.createElement("canvas"); c.width = z.w; c.height = z.h;
  const x = c.getContext("2d"), img = x.createImageData(z.w, z.h), d = img.data;
  for (let y = 0, p = 0; y < z.h; y++) for (let xx = 0; xx < z.w; xx++, p++) {
    const k = z[p];
    if (!k) continue;
    const edge = (xx > 0 && z[p - 1] !== k) || (xx < z.w - 1 && z[p + 1] !== k) ||          // 区域边界：实线描边
                 (y > 0 && z[p - z.w] !== k) || (y < z.h - 1 && z[p + z.w] !== k);
    let r, g, b, a;
    if (z.cells) { r = k === 3 ? 40 : k === 4 ? 255 : 226; g = k === 3 ? 210 : k === 4 ? 205 : 75; b = k === 3 ? 90 : k === 4 ? 45 : 74; a = edge ? 255 : 100; }
    else if (k === 2) { const on = ((xx + y) % 8) < 3; r = 226; g = 75; b = 74; a = edge ? 255 : on ? 220 : 50; }   // 红斜线（底下是透明格）
    else if (k === 4) { const on = xx % 6 < 2 && y % 6 < 2; r = 240; g = 160; b = 160; a = edge ? 200 : on ? 230 : 70; }   // 浅红点：融合带（重绘，不涂灰）
    else if (k === 3) { const on = xx % 6 < 2 && y % 6 < 2; r = 40; g = 210; b = 90; a = edge ? 255 : on ? 230 : 40; }   // 绿点：主体（不重绘）
    else { const on = xx % 6 < 2 && y % 6 < 2; r = 55; g = 138; b = 221; a = edge ? 255 : on ? 255 : 95; }  // 蓝色调 + 蓝点
    if (focus != null && k !== focus) a = Math.round(a * 0.12);
    d[p * 4] = r; d[p * 4 + 1] = g; d[p * 4 + 2] = b; d[p * 4 + 3] = a;
  }
  x.putImageData(img, 0, 0);
  z.pats[focus] = c;
  return c;
}

function polygon(o, i) {
  const b = frameBox(o, i);
  return [[0, 0], [1, 0], [1, 1], [0, 1]].map(([u, v]) => apply(o, at(b, u, v)));
}

function drawGizmo() {
  const g = $("#gizmo");
  const r = stageRect();
  Object.assign(g.style, { left: r.x + "px", top: r.y + "px", width: r.w + "px", height: r.h + "px" });
  g.setAttribute("viewBox", `0 0 ${r.w} ${r.h}`);
  if (!S.d || S.compare) { g.innerHTML = ""; return; }
  const k = r.w / S.d.w;
  const pts = (ps) => ps.map((p) => `${p[0] * k},${p[1] * k}`).join(" ");
  let html = "";
  const h = S.hover && !S.sel.has(S.hover) ? obj(S.hover) : null;
  if (h && h.visible && (S.fi ?? S.i) >= h.start && (S.fi ?? S.i) <= h.end) html += `<polygon class="hoverbox" points="${pts(polygon(h, (S.fi ?? S.i)))}"/>`;
  const sel = selected().filter((o) => o.visible && (S.fi ?? S.i) >= o.start && (S.fi ?? S.i) <= o.end);
  for (const o of sel) html += `<polygon class="box ${o.locked ? "locked" : ""}" points="${pts(polygon(o, (S.fi ?? S.i)))}"/>`;
  for (const o of selected().filter((o) => o.shape && !o.off && (S.fi ?? S.i) >= o.start && (S.fi ?? S.i) <= o.end))   // 选中的固定蒙版（本来隐藏）：画出轮廓，方便找到它
    html += `<polygon class="box locked" points="${pts(polygon(o, (S.fi ?? S.i)))}"/>`;
  if (sel.length === 1 && !sel[0].locked) {
    const o = sel[0], b = frameBox(o, (S.fi ?? S.i));
    const top = apply(o, at(b, 0.5, 0));
    const up = rotate([0, -28 / k * Math.sign(o.tf.sy || 1)], o.tf.rot);   // 旋转把手：顶边中点再往外 28 像素
    const rp = [top[0] + up[0], top[1] + up[1]];
    html += `<line class="stem" x1="${top[0] * k}" y1="${top[1] * k}" x2="${rp[0] * k}" y2="${rp[1] * k}"/>`;
    html += `<circle class="rot" data-h="rot" cx="${rp[0] * k}" cy="${rp[1] * k}" r="5"/>`;
    for (const [name, [u, v]] of Object.entries(HANDLES)) {
      const p = apply(o, at(b, u, v));
      const cur = handleCursor(name, o.tf.rot);
      html += `<rect class="h" data-h="${name}" x="${p[0] * k - 4}" y="${p[1] * k - 4}" width="8" height="8" style="cursor:${cur}"/>`;
    }
    const c = apply(o, at(b, 0.5, 0.5));
    html += `<circle class="pivot" cx="${c[0] * k}" cy="${c[1] * k}" r="3"/>`;
    const X = extRect(o, (S.fi ?? S.i)), e = curExt(o) || {};       // 正在编辑的主体扩绘区：橙色虚线 + 四条边外的箭头；没有主体扩绘区就不画（先在蒙版面板「添加」）
    if (e.id && $("#extList").offsetParent) {                       // 只在蒙版组开着时画（换组 = 退出编辑扩绘区）
    if (["t", "b", "l", "r"].some((s) => e[s])) html += `<rect class="extbox" x="${X.x0 * k}" y="${X.y0 * k}" width="${(X.x1 - X.x0) * k}" height="${(X.y1 - X.y0) * k}"/>`;
    const mx = (X.x0 + X.x1) / 2 * k, my = (X.y0 + X.y1) / 2 * k, g = 16, s = 7;
    const arrow = { t: [mx, X.y0 * k - g, 0, -1], b: [mx, X.y1 * k + g, 0, 1], l: [X.x0 * k - g, my, -1, 0], r: [X.x1 * k + g, my, 1, 0] };
    for (const [side, [ax, ay, dx, dy]] of Object.entries(arrow)) {
      const p = [[ax + dx * s, ay + dy * s], [ax - dx * s + dy * s, ay - dy * s + dx * s], [ax - dx * s - dy * s, ay - dy * s - dx * s]];
      html += T`<polygon class="xh" data-x="${side}" points="${p.map((q) => q.join(",")).join(" ")}" style="cursor:${dx ? "ew" : "ns"}-resize"><title>拖动：往${{ t: T("上"), b: T("下"), l: T("左"), r: T("右") }[side]}拉出主体扩绘区（跟着主体走）</title></polygon>`;
    }
    const B = X.base;                                               // 拉开的边：沿边的范围（外框高 / 宽的比例），两头两个圆点可拖
    for (const side of ["t", "b", "l", "r"].filter((s) => e[s])) {
      const [a, z] = [e[side + "0"] ?? 0, e[side + "1"] ?? 1], v = side === "l" || side === "r";
      const pt = (f) => v ? [(side === "r" ? X.x1 : X.x0) * k, (B.y0 + (B.y1 - B.y0) * f) * k] : [(B.x0 + (B.x1 - B.x0) * f) * k, (side === "b" ? X.y1 : X.y0) * k];
      const [p0, p1] = [pt(a), pt(z)];
      html += `<line class="xspan" x1="${p0[0]}" y1="${p0[1]}" x2="${p1[0]}" y2="${p1[1]}"/>`;
      const out = { t: [0, -1], b: [0, 1], l: [-1, 0], r: [1, 0] }[side];   // 圆点外侧放一个双向小箭头，提示可以沿这条边拖
      for (const [end, q] of [["0", p0], ["1", p1]]) {
        const tip = T`<title>拖动：调整${v ? T("高度") : T("宽度")}（这条边只在这一段扩大）</title>`, cur = `cursor:${v ? "ns" : "ew"}-resize`;
        html += `<circle class="xs" data-xs="${side + end}" cx="${q[0]}" cy="${q[1]}" r="5" style="${cur}">${tip}</circle>`;
        const cx = q[0] + out[0] * 16, cy = q[1] + out[1] * 16, L = 9, W = 4;
        const d = v ? `M${cx},${cy - L} l${W},${W} h${-W / 2} v${2 * L - 2 * W} h${W / 2} l${-W},${W} l${-W},${-W} h${W / 2} v${-(2 * L - 2 * W)} h${-W / 2} z`
                    : `M${cx - L},${cy} l${W},${-W} v${W / 2} h${2 * L - 2 * W} v${-W / 2} l${W},${W} l${-W},${W} v${-W / 2} h${-(2 * L - 2 * W)} v${W / 2} z`;
        html += `<path class="xsa" data-xs="${side + end}" d="${d}" style="${cur}">${tip}</path>`;
      }
    }
    }
  }
  if (pickOn()) html += pickSvg(k);                                   // 点选识别的点
  g.innerHTML = html;
}

function extRect(o, i) {   // 扩展框：这一帧主体（变换后）的外框，四边各加上扩展量（原画面像素）
  const ps = polygon(o, i), e = curExt(o) || {};
  const xs = ps.map((p) => p[0]), ys = ps.map((p) => p[1]);
  const b = { x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) };
  return { ...b, base: b, x0: b.x0 - (e.l || 0), y0: b.y0 - (e.t || 0), x1: b.x1 + (e.r || 0), y1: b.y1 + (e.b || 0) };
}

function handleCursor(name, rot) {   // 把手方向随旋转角变化，光标也跟着转
  const dirs = ["n", "ne", "e", "se", "s", "sw", "w", "nw"];
  const idx = (dirs.indexOf(name) + Math.round(((rot % 360) + 360) % 360 / 45)) % 8;
  return ["ns-resize", "nesw-resize", "ew-resize", "nwse-resize"][idx % 4];
}

