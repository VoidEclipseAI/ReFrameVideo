// 视频 PS 前端 7b：空区蒙版。在画面上画方块 / 圆形等（矩形 / 椭圆 / 多边形 / 套索 / 画笔）= 纯空区，生成时和空区一样补。
// 每画完一次（Enter）建一个新的固定蒙版图层「空区蒙版 N」（默认所有帧，时间轴拖色条两端改帧范围；默认隐藏 + 锁定，眼睛 = 启用 / 停用；服务端 /shape）。
// 按住 Alt 画 = 从形状里去掉。Enter = 完成（多边形没闭合时先闭合），Esc = 取消，Ctrl+Z = 撤销一步。
const shCv = $("#shapeCv"), shCtx = shCv.getContext("2d");
const [shMask, shMaskCtx] = mkCv();                                 // 已经画好的形状（原视频大小，白 = 在里面）
const SHAPE_TOOLS = { rect: T("矩形"), ellipse: T("椭圆"), poly: T("多边形"), lasso: T("套索"), brush: T("画笔") };
// 局部重绘（kind = repaint）：画在图片编辑看大图那块（#imgMain，接着编辑的候选图 / 上传的图）上，坐标 = 图片像素
const shHome = shCv.parentNode;
const shapeImg = () => !!(S.shape && S.shape.view === "img");
const shapeScreenW = () => shCv.getBoundingClientRect().width || stageRect().w;
const SHAPE_KIND_NAME = { hole: T("空区蒙版"), prefill: T("背景预填"), protect: T("保护蒙版"), keep: T("主体局部保留"), repaint: T("局部重绘") };
function startShape(tool, kind, oid) {
  if (!S.d) return;
  if (S.shape && shapeDrawn()) return setLog(T("先建好或取消正在画的蒙版（✓ / ✕）"), true);   // 别把已经画了的悄悄清掉
  const img = kind === "repaint", im = $("#imgMain");
  if (img && !im.naturalWidth) return;
  if (!img) {
    if (S.playing) togglePlay();
    if (S.refine) endRefine();
    if (S.frameMode) setFrameMode(false);
    if (kind !== "keep") select(null);                              // 主体局部保留：画在选中的主体身上，保持选中
  }
  S.shape = { tool, kind: kind || "hole", oid, view: img ? "img" : "stage", undo: [], pts: null, drag: null, cur: null };   // kind：hole = 空区蒙版，prefill = 背景预填，protect = 保护蒙版，keep = 主体局部保留，repaint = 局部重绘
  const W = img ? im.naturalWidth : S.d.w, H = img ? im.naturalHeight : S.d.h;
  shCv.width = shMask.width = W; shCv.height = shMask.height = H;
  shMaskCtx.clearRect(0, 0, W, H);
  $("#shapeBar .bar-title").textContent = SHAPE_KIND_NAME[S.shape.kind] || "";
  if (img) {                                                       // 挪到看大图那块上，接着改已经画过的范围
    $("#imgBox").append(shCv, $("#shapeBar"));
    shCv.style.pointerEvents = "auto";
    $("#imgRepaintOv").hidden = true;
    const it = curImg();
    if (it && it.repaint) {
      const old = new Image();
      old.onload = () => { if (shapeImg()) { shMaskCtx.drawImage(old, 0, 0, W, H); composeShape(); } };
      old.src = `/api/p/${S.pid}/images/${it.id}/repaint.png?t=${Date.now()}`;
    }
  }
  setShapeTool(tool);
  $("#stage").classList.add("shaping");
  $("#shapeBar").hidden = false;
  shCv.hidden = false;
  placeShapeCanvas();
  draw();
}
function setShapeTool(tool) {
  if (!S.shape) return;
  S.shape.tool = tool; S.shape.pts = null; S.shape.drag = null;
  $$("#shapeBar [data-shape]").forEach((b) => b.classList.toggle("on", b.dataset.shape === tool));
  $("#shapeSizeBox").hidden = tool !== "brush";
  $("#shapeHint").textContent = {
    rect: T("拖出一个框"), ellipse: T("拖出一个椭圆"), lasso: T("按住拖出任意形状，松开自动闭合"),
    poly: T("点几个点；双击、点回第一个点或按 Enter 闭合"), brush: T("按住涂抹"),
  }[tool] + T(" · 按住 Alt = 去掉 · 可以画好几块");
  composeShape();
}
function endShape() {
  const img = shapeImg();
  S.shape = null;
  if (img) { shHome.append(shCv, $("#shapeBar")); shCv.style.pointerEvents = ""; if (typeof renderTabs === "function") renderTabs(); }
  $("#stage").classList.remove("shaping");
  $("#shapeBar").hidden = true;
  shCv.hidden = true;
  draw();
}
function placeShapeCanvas() {
  if (!S.shape) return;
  if (shapeImg()) {
    const im = $("#imgMain");
    return Object.assign(shCv.style, { left: im.offsetLeft + "px", top: im.offsetTop + "px", width: im.offsetWidth + "px", height: im.offsetHeight + "px" });
  }
  const r = stageRect();
  Object.assign(shCv.style, { left: r.x + "px", top: r.y + "px", width: r.w + "px", height: r.h + "px" });
}
const shapePt = (e) => {
  if (shapeImg()) {
    const r = shCv.getBoundingClientRect();
    return [Math.max(0, Math.min(shCv.width, (e.clientX - r.left) / r.width * shCv.width)), Math.max(0, Math.min(shCv.height, (e.clientY - r.top) / r.height * shCv.height))];
  }
  const [x, y] = toVideo(e); return [Math.max(0, Math.min(S.d.w, x)), Math.max(0, Math.min(S.d.h, y))];
};
shCv.addEventListener("pointerdown", (e) => { if (shapeImg()) shapeDown(e); });
shCv.addEventListener("pointermove", (e) => { if (shapeImg()) shapeMove(e); });
shCv.addEventListener("dblclick", (e) => { if (shapeImg() && S.shape.tool === "poly") { e.preventDefault(); closePoly(); } });
addEventListener("resize", () => { if (shapeImg()) placeShapeCanvas(); });
function shapePath(ctx, kind, pts) {                               // 把一个形状画成路径（原视频坐标）
  ctx.beginPath();
  if (kind === "rect" || kind === "ellipse") {
    const [[x0, y0], [x1, y1]] = pts, x = Math.min(x0, x1), y = Math.min(y0, y1), w = Math.abs(x1 - x0), h = Math.abs(y1 - y0);
    if (kind === "rect") ctx.rect(x, y, w, h);
    else ctx.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2);
  } else {
    pts.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.closePath();
  }
}
function pushShapeUndo() {
  const R = S.shape;
  R.undo.push(shMaskCtx.getImageData(0, 0, shMask.width, shMask.height));
  if (R.undo.length > 30) R.undo.shift();
}
function commitShape(kind, pts, erase) {                           // 一个形状画进蒙版（Alt = 去掉）
  if (kind !== "brush" && (pts.length < (kind === "rect" || kind === "ellipse" ? 2 : 3))) return;
  if ((kind === "rect" || kind === "ellipse") && (Math.abs(pts[1][0] - pts[0][0]) < 2 || Math.abs(pts[1][1] - pts[0][1]) < 2)) return;
  pushShapeUndo();
  shMaskCtx.globalCompositeOperation = erase ? "destination-out" : "source-over";
  shMaskCtx.fillStyle = "#fff";
  shapePath(shMaskCtx, kind, pts);
  shMaskCtx.fill();
  shMaskCtx.globalCompositeOperation = "source-over";
}
function brushDab(a, b, erase) {
  const r = +$("#shapeSize").value / 2;
  shMaskCtx.globalCompositeOperation = erase ? "destination-out" : "source-over";
  shMaskCtx.strokeStyle = shMaskCtx.fillStyle = "#fff";
  shMaskCtx.lineWidth = r * 2; shMaskCtx.lineCap = "round";
  shMaskCtx.beginPath(); shMaskCtx.moveTo(a[0], a[1]); shMaskCtx.lineTo(b[0], b[1]); shMaskCtx.stroke();
  shMaskCtx.globalCompositeOperation = "source-over";
}
function composeShape() {                                          // 显示：已画好的形状（青色半透明）+ 正在画的轮廓
  if (!S.shape) return;
  const W = shCv.width, H = shCv.height, R = S.shape;
  shCtx.clearRect(0, 0, W, H);
  shCtx.globalAlpha = 0.45; shCtx.drawImage(shMask, 0, 0);
  shCtx.globalCompositeOperation = "source-in"; shCtx.fillStyle = "#18c8e8"; shCtx.fillRect(0, 0, W, H);
  shCtx.globalCompositeOperation = "source-over"; shCtx.globalAlpha = 1;
  const k = W / shapeScreenW();                                    // 线宽按屏幕 2 像素
  shCtx.lineWidth = 2 * k; shCtx.strokeStyle = R.erase ? "#ff5a5a" : "#ffd400"; shCtx.setLineDash([6 * k, 4 * k]);
  if (R.drag && (R.tool === "rect" || R.tool === "ellipse")) { shapePath(shCtx, R.tool, [R.drag.p0, R.cur]); shCtx.stroke(); }
  if (R.drag && R.tool === "lasso" && R.drag.pts.length > 1) { shapePath(shCtx, "lasso", R.drag.pts); shCtx.stroke(); }
  if (R.pts && R.pts.length) {
    shCtx.beginPath();
    R.pts.forEach(([x, y], i) => (i ? shCtx.lineTo(x, y) : shCtx.moveTo(x, y)));
    if (R.cur) shCtx.lineTo(R.cur[0], R.cur[1]);
    shCtx.stroke();
    shCtx.setLineDash([]); shCtx.fillStyle = "#ffd400";
    for (const [x, y] of R.pts) { shCtx.beginPath(); shCtx.arc(x, y, 4 * k, 0, Math.PI * 2); shCtx.fill(); }
  }
  shCtx.setLineDash([]);
}
function closePoly() {
  const R = S.shape;
  if (R && R.pts && R.pts.length >= 3) commitShape("poly", R.pts, R.erase);
  if (R) R.pts = null;
  composeShape();
}
function shapeDown(e) {
  e.preventDefault();
  const R = S.shape, p = shapePt(e);
  R.erase = e.altKey;
  if (R.tool === "poly") {
    if (!R.pts) R.pts = [];
    const k = shCv.width / shapeScreenW(), f = R.pts[0];
    if (R.pts.length >= 3 && Math.hypot(p[0] - f[0], p[1] - f[1]) < 10 * k) return closePoly();   // 点回第一个点：闭合
    R.pts.push(p);
    return composeShape();
  }
  if (R.tool === "brush") { pushShapeUndo(); brushDab(p, p, R.erase); }
  R.drag = { p0: p, last: p, pts: [p] };
  R.cur = p;
  try { (shapeImg() ? shCv : $("#gizmo")).setPointerCapture(e.pointerId); } catch {}
  composeShape();
}
function shapeMove(e) {
  const R = S.shape, p = shapePt(e);
  R.cur = p;
  if (R.drag) {
    if (R.tool === "brush") { brushDab(R.drag.last, p, R.erase); R.drag.last = p; }
    if (R.tool === "lasso") R.drag.pts.push(p);
  }
  if (R.drag || R.pts) composeShape();
}
addEventListener("pointerup", () => {
  const R = S.shape;
  if (!R || !R.drag) return;
  if (R.tool === "rect" || R.tool === "ellipse") commitShape(R.tool, [R.drag.p0, R.cur], R.erase);
  if (R.tool === "lasso") commitShape("lasso", R.drag.pts, R.erase);
  R.drag = null;
  composeShape();
});
$("#gizmo").addEventListener("dblclick", (e) => { if (S.shape && S.shape.tool === "poly") { e.preventDefault(); closePoly(); } });
function shapeKey(e, ctrl) {                                       // 图形蒙版模式下的快捷键；返回 true = 已处理
  if (e.key === "Escape") { e.preventDefault(); if (S.shape.pts) { S.shape.pts = null; composeShape(); } else cancelShape(); return true; }
  if (e.key === "Enter") { e.preventDefault(); if (S.shape.pts) closePoly(); else finishShape(); return true; }
  if (ctrl && e.key.toLowerCase() === "z") { e.preventDefault(); shapeUndo(); return true; }
  return e.code !== "Space";                                       // 其余快捷键（移动图层、换帧等）在画形状时不用；空格照常平移
}
function shapeUndo() {
  const R = S.shape;
  if (!R) return;
  if (R.pts && R.pts.length) { R.pts.pop(); if (!R.pts.length) R.pts = null; return composeShape(); }
  const d = R.undo.pop();
  if (d) { shMaskCtx.putImageData(d, 0, 0); composeShape(); }
}
async function finishShape() {
  const R = S.shape;
  if (!R) return;
  if (R.pts) closePoly();
  const W = shMask.width, H = shMask.height, d = shMaskCtx.getImageData(0, 0, W, H).data;
  const out = document.createElement("canvas"); out.width = W; out.height = H;
  const oc = out.getContext("2d"), od = oc.createImageData(W, H);
  let n = 0;
  for (let k = 0; k < d.length; k += 4) { const on = d[k + 3] > 127 ? 255 : 0; n += on ? 1 : 0; od.data[k] = od.data[k + 1] = od.data[k + 2] = on; od.data[k + 3] = 255; }
  if (!n && R.kind !== "repaint") return setLog(T("还没有画任何形状"), true);
  oc.putImageData(od, 0, 0);
  const blob = await new Promise((r) => out.toBlob(r, "image/png"));
  if (R.kind === "repaint") {                                      // 局部重绘：存到这个图片标签（画空 = 清除）
    const it = curImg();
    if (!it) return endShape();
    try {
      const url = `/api/p/${S.pid}/images/${it.id}/repaint`;
      const r = n ? await api(url, { method: "POST", body: blob }) : await api(url, { method: "DELETE" });
      delete it.repaint; Object.assign(it, r);
      endShape();
      setLog(it.repaint ? T`${it.label}：局部重绘范围占画面 ${it.repaint}%，出候选时只重绘这块` : T`${it.label}：清除了局部重绘范围（出候选时整张按提示词改）`);
    } catch (err) { setLog(err.message, true); }
    return;
  }
  if (R.kind === "keep") {                                         // 主体局部保留：服务端倒回原视频坐标存形状，条目加进图层（能撤销）
    try {
      const o = obj(R.oid);
      const r = await api(`/api/p/${S.pid}/obj/${R.oid}/keep?frame=${S.i}`, { method: "POST", body: blob });
      endShape();
      const no = Math.max(0, ...(o.keep_masks || []).map((m) => +((m.name || "").match(/(\d+)$/) || [0, 0])[1])) + 1;
      const name = `主体局部保留 ${no}`;                              // 存进项目的名字（不翻译）
      commit([o], (x) => { x.keep_masks = [...(x.keep_masks || []).map((m) => ({ ...m })), { id: r.id, name, frame: r.frame }]; }, T("新建主体局部保留"));
      if (!S.sel.has(o.id) || S.sel.size !== 1) select(o.id);
      setLog(T`新建了「${name}」：范围里保留，「${o.name}」在范围外的部分生成时重绘（画面上是棋盘格）`);
    } catch (err) { setLog(err.message, true); }
    return;
  }
  try {
    const prefill = R.kind === "prefill", protect = R.kind === "protect";
    const data = await api(`/api/p/${S.pid}/shape${prefill || protect ? "?kind=" + R.kind : ""}`, { method: "POST", body: blob });
    endShape();
    setProject(data, true);
    S.zoneGen = (S.zoneGen || 0) + 1;                              // 空区变了：叠加层 / 空区重新取
    zoneDecoded.clear(); zoneImgs.clear(); zoneRetry.clear();
    drawSoon();
    setLog(protect ? T("新建了一个保护蒙版图层：画的地方一律不重绘，保留原样。时间轴上拖它的色条两端改帧范围") : prefill ? T("新建了一个背景预填图层：范围里的空区生成时用背景图填，不涂灰。时间轴上拖它的色条两端改帧范围")
                   : T("新建了一个空区蒙版图层：画的地方是空区，生成时补。时间轴上拖它的色条两端改帧范围"));
  } catch (err) { setLog(err.message, true); }
}
// 选图形蒙版的画法：图层面板的「+」三种都列；蒙版面板「手绘蒙版」的按钮只列那一种
function openShapeMenu(btn, kinds = ["bgswap", "hole", "prefill", "protect"]) {
  const m = $("#layerMenu"), r = btn.getBoundingClientRect();
  const tools = (kind) => Object.entries(SHAPE_TOOLS).map(([k, v]) => `<div data-shape="${k}" data-kind="${kind}">${v}</div>`).join("");
  const head = {
    hole: T('<div class="note" title="画的地方当空区，生成时重绘；每画一次一个图层">空区蒙版</div>'),
    prefill: T('<div class="note" title="范围里本来就是空区的地方，生成时用背景图填（不涂灰），仍然重绘；要先在图片编辑里做好背景图">背景预填</div>'),
    protect: T('<div class="note" title="画的地方一律不重绘，保留原样（招牌、字幕、某张脸等），哪怕落在空区或融合带里">保护蒙版</div>'),
    keep: T('<div class="note" title="画在选中的主体身上：范围里保留，这个主体在范围外的部分重绘">主体局部保留</div>'),
    repaint: T('<div class="note" title="圈出要改的地方：出候选时只重绘这块，其余像素不动">局部重绘</div>'),
  };
  const bgItem = T('<div data-bgswap title="主体以外的地方全部当空区，生成时整片重画背景；主体保持原样。时间轴上拖色条两端可以只换其中一段">替换背景</div>');
  m.innerHTML = kinds.map((k) => k === "bgswap" ? bgItem : head[k] + tools(k)).join('<div class="sep"></div>');
  m.hidden = false;
  m.style.maxHeight = innerHeight - 16 + "px"; m.style.overflowY = "auto";   // 三组画法：放不下就往上翻，再不行就滚动
  const mh = m.offsetHeight;
  if (btn.closest("#toolbar")) {                                    // 左边栏的「画蒙版」：菜单贴在按钮右边，不盖住工具栏
    m.style.left = r.right + 6 + "px";
    m.style.top = Math.max(8, Math.min(r.top, innerHeight - mh - 8)) + "px";
  } else {
    m.style.left = Math.min(r.left, innerWidth - 230) + "px";
    m.style.top = (r.bottom + 4 + mh <= innerHeight - 8 ? r.bottom + 4 : Math.max(8, r.top - mh - 4)) + "px";
  }
  m.onclick = (ev) => {
    if (ev.target.closest("[data-bgswap]")) { m.hidden = true; return addBgSwap(); }
    const b = ev.target.closest("[data-shape]");
    if (!b) return;
    m.hidden = true;
    startShape(b.dataset.shape, b.dataset.kind, b.dataset.kind === "keep" ? (selected()[0] || {}).id : undefined);
  };
}
// 替换背景（2026-11-18 用户定）：建一个整幅的空区蒙版图层「替换背景 N」。空洞不压看得见的主体，所以 = 主体以外全部重画
async function addBgSwap() {
  if (!S.pid) return;
  try {
    const data = await api(`/api/p/${S.pid}/shape?kind=bgswap`, { method: "POST" });
    genChecked[S.pid + "bg"] = false;                              // 旧背景图会把旧背景画回来：视频生成也取消勾选（服务端已经把首帧 / 尾帧的背景图关掉）
    setProject(data, true);
    S.zoneGen = (S.zoneGen || 0) + 1;
    zoneDecoded.clear(); zoneImgs.clear(); zoneRetry.clear();
    drawSoon();
    setLog(T("新建了「替换背景」图层：主体以外全是空区，生成时整片重画背景，主体保持原样。首帧 / 尾帧和视频生成已自动取消勾选旧背景图。时间轴上拖它的色条两端可以只换其中一段；眼睛 = 启用 / 停用"));
  } catch (err) { setLog(err.message, true); }
}
$("#addLayerBtn").onclick = (e) => { e.stopPropagation(); openShapeMenu(e.currentTarget); };
$("#msecShape .shape-new").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-kind]");
  if (!b) return;
  e.stopPropagation();
  if (b.dataset.kind === "bgswap") return addBgSwap();
  openShapeMenu(b, [b.dataset.kind]);
});
$("#shapeBar").addEventListener("click", (e) => { const b = e.target.closest("[data-shape]"); if (b) setShapeTool(b.dataset.shape); });
$("#shapeUndo").onclick = shapeUndo;
$("#shapeClear").onclick = () => { if (!S.shape) return; pushShapeUndo(); shMaskCtx.clearRect(0, 0, shMask.width, shMask.height); S.shape.pts = null; composeShape(); };
$("#shapeOk").onclick = finishShape;
async function cancelShape() {                                    // ✕ / Esc：已经画了东西先问
  if (shapeDrawn() && !(await uiConfirm(T("放弃正在画的蒙版？"), { ok: T("放弃"), no: T("继续画"), danger: true }))) return;
  if (S.shape) endShape();
}
$("#shapeCancel").onclick = () => cancelShape();
$("#shapeSize").oninput = () => { $("#shapeSizeVal").textContent = $("#shapeSize").value; };

// ---------- 切右边面板组 = 退出正在用的临时模式（用户定 2026-11-17）----------
// 画幅拖动、点选识别直接退出；逐帧修整有改动先保存再退出；画蒙版画了东西先弹窗问（建好 / 丢掉 / 取消切换），没画就直接退出。
function shapeDrawn() {
  const R = S.shape;
  if (!R) return false;
  if (R.pts && R.pts.length) return true;
  const d = shMaskCtx.getImageData(0, 0, shMask.width, shMask.height).data;
  for (let k = 3; k < d.length; k += 4) if (d[k] > 127) return true;
  return false;
}
let leaveTo;
function leaveModes(to) {                                          // to = 要切到的组：切到画幅组不退画幅拖动、切到图层组不退点选
  if (S.shape) endShape();
  if (S.frameMode && to !== "frame") setFrameMode(false);
  if (S.pick && to !== "layers") setSegMode("text");
  if (S.refine) {
    if (S.refine.dirty) saveRefine().then((ok) => ok && endRefine());   // 保存失败：留在修整里（报错已经显示）
    else endRefine();
  }
}
window.uiLeaveGroup = (from, to) => {
  if (shapeDrawn()) {
    leaveTo = to;
    const nm = SHAPE_KIND_NAME[S.shape.kind] || "";
    $("#leaveShapeMsg").textContent = uiLanguage === "en" ? `Switching panels exits mask drawing. What should happen to the ${nm} you just drew?`
      : `切换面板会退出画蒙版。刚画的「${nm}」要怎么处理？`;
    $("#leaveShapeDlg").hidden = false;
    return false;
  }
  leaveModes(to);
  return true;
};
$("#leaveShapeKeep").onclick = async () => {
  $("#leaveShapeDlg").hidden = true;
  await finishShape();
  if (S.shape) return;                                             // 没建成（比如什么都没画）：留在这里
  leaveModes(leaveTo);
  window.uiOpenGroup(leaveTo, true);
};
$("#leaveShapeDrop").onclick = () => { $("#leaveShapeDlg").hidden = true; leaveModes(leaveTo); window.uiOpenGroup(leaveTo, true); };
$("#leaveShapeCancel").onclick = () => { $("#leaveShapeDlg").hidden = true; };
