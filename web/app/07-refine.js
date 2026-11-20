// 视频 PS 前端 7/9：图层 ⋯ 菜单、调整当前帧蒙版（画笔 / 橡皮 / 魔棒 / 平移）。
// 9 个文件是同一个全局作用域的普通脚本，按 index.html 里的顺序加载（原来是一个 app.js）。
// ---------- 图层 ⋯ 菜单 ----------
function openLayerMenu(o, anchor) {
  const m = $("#layerMenu"), r = anchor.getBoundingClientRect();
  m.innerHTML = `<div data-act="refine">${uiLanguage === "en" ? `Frame touch-up (frame ${S.i})` : `逐帧修整（第 ${S.i} 帧）`}</div>` + T`
    <div data-act="rename">改名…</div>
    <div data-act="visible">${o.visible ? T("隐藏这一层") : T("显示这一层")}</div>
    <div data-act="lock">${o.locked ? T("解锁") : T("锁定")}</div>
    <div class="sep"></div>
    <div data-act="front">置顶</div>
    <div data-act="back">置底</div>
    <div class="sep"></div>
    <div data-act="delete" class="danger">删除图层</div>`;
  m.hidden = false;
  m.style.left = Math.min(r.left, innerWidth - 230) + "px";
  m.style.top = Math.min(r.bottom + 4, innerHeight - 260) + "px";
  m.onclick = async (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (!act) return;
    m.hidden = true;
    if (act === "refine") return startRefine(o.id);
    if (act === "rename") {
      const name = await uiPrompt(T("图层名字"), o.name);
      if (name && name.trim() && name.trim() !== o.name) commit([o], (x) => { x.name = name.trim(); }, T("改名"));
      return;
    }
    if (act === "visible") return commit([o], toggleVis, o.shape ? T("启用 / 停用蒙版") : T("显示 / 隐藏"));
    if (act === "lock") return commit([o], (x) => { x.locked = !x.locked; }, T("锁定"));
    if (act === "front" || act === "back") return zOrder(act);
    if (act === "delete") {
      if (!(await uiConfirm(T`删除图层「${o.name}」？`, { ok: T("删除"), danger: true }))) return;
      try {
        const d = await api(`/api/p/${S.pid}/obj/${o.id}`, { method: "DELETE" });
        S.sel.clear();
        setProject(d, true);                                   // 用服务端返回的项目：这一层立刻从列表消失，原视频里的人和背景自然露出来（融入背景）
      }
      catch (err) { setLog(T("删除失败：") + err.message, true); }
    }
  };
}
addEventListener("pointerdown", (e) => { if (!e.target.closest("#layerMenu") && !e.target.closest(".more") && !e.target.closest(".part-add") && !e.target.closest("#addLayerBtn")) $("#layerMenu").hidden = true; }, true);

// ---------- 调整当前帧抠图：在 SAM / MatAnyone 的结果上补和擦，只改这一帧 ----------
// 底图 = 这一帧原始的抠图结果（按图层的边缘模式，含 MatAnyone 柔边）；修改单独记两张图：补上 / 擦掉。
// 没动过的地方完全保留原始结果。显示 = 底图 − 擦掉 + 补上，用图层颜色半透明盖在原画面上。
const rfCv = $("#refineCv");
const rfCtx = rfCv.getContext("2d");
const mkCv = () => { const c = document.createElement("canvas"); return [c, c.getContext("2d", { willReadFrequently: true })]; };
const [rfBase, rfBaseCtx] = mkCv(), [rfAdd, rfAddCtx] = mkCv(), [rfErase, rfEraseCtx] = mkCv();
async function startRefine(oid) {
  if (S.playing) togglePlay();
  if (S.frameMode) setFrameMode(false);
  select(null);
  S.refine = { oid, i: S.i, dirty: false, undo: [], tool: S.refine?.tool || "add" };
  setRefineTool(S.refine.tool);
  $("#stage").classList.add("refining");
  $("#refineBar").hidden = false;
  rfCv.hidden = false;
  await loadRefineFrame();
}
const loadImg = async (url) => { const im = new Image(); im.src = url; await im.decode(); return im; };
async function loadRefineFrame() {
  const R = S.refine, o = obj(R.oid), i = S.i;
  R.i = i; R.dirty = false; R.undo = [];
  $("#rfName").textContent = o.name;
  $("#rfFrame").textContent = FR(i);
  let base, ed;
  try {
    [base, ed] = await Promise.all([
      loadImg(`/api/p/${S.pid}/obj/${R.oid}/base/${i}.png?e=${o.edge || "sam"}&g=${o.grow ?? 0}&t=${Date.now()}`),
      loadImg(`/api/p/${S.pid}/obj/${R.oid}/edits/${i}.png?t=${Date.now()}`)]);
  } catch (e) { setLog(T("读取这一帧的抠图失败（服务端是不是没重启？）"), true); return endRefine(); }
  if (!S.refine || S.refine.i !== i) return;
  const W = S.d.w, H = S.d.h;
  for (const c of [rfCv, rfBase, rfAdd, rfErase]) { c.width = W; c.height = H; }
  const [cr, cg, cb] = [1, 3, 5].map((k) => parseInt(o.color.slice(k, k + 2), 16));
  rfBaseCtx.drawImage(base, 0, 0, W, H);                       // 灰度（白 = 主体）→ 图层颜色 + 透明度
  let d = rfBaseCtx.getImageData(0, 0, W, H), p = d.data;
  for (let k = 0; k < p.length; k += 4) { p[k + 3] = p[k]; p[k] = cr; p[k + 1] = cg; p[k + 2] = cb; }
  rfBaseCtx.putImageData(d, 0, 0);
  rfAddCtx.drawImage(ed, 0, 0, W, H);                          // 红 = 补上，绿 = 擦掉
  d = rfAddCtx.getImageData(0, 0, W, H); p = d.data;
  const er = new ImageData(W, H), q = er.data;
  for (let k = 0; k < p.length; k += 4) {
    const add = p[k] > 127, erase = p[k + 1] > 127;
    p[k] = cr; p[k + 1] = cg; p[k + 2] = cb; p[k + 3] = add ? 255 : 0;
    q[k + 3] = erase ? 255 : 0;
  }
  rfAddCtx.putImageData(d, 0, 0);
  rfEraseCtx.putImageData(er, 0, 0);
  composeRefine();
  draw();
}
function composeRefine() {
  rfCtx.globalCompositeOperation = "source-over";
  rfCtx.clearRect(0, 0, rfCv.width, rfCv.height);
  rfCtx.drawImage(rfBase, 0, 0);
  rfCtx.globalCompositeOperation = "destination-out";
  rfCtx.drawImage(rfErase, 0, 0);
  rfCtx.globalCompositeOperation = "source-over";
  rfCtx.drawImage(rfAdd, 0, 0);
}
function placeRefineCanvas() {
  if (!S.refine) return;
  const r = stageRect();
  Object.assign(rfCv.style, { left: r.x + "px", top: r.y + "px", width: r.w + "px", height: r.h + "px" });
}
const rfTool = (e) => {
  const t = S.refine.tool;
  if (t === "add" || t === "erase") return e && e.altKey ? (t === "add" ? "erase" : "add") : t;
  return t;
};
const rfSize = () => +$("#rfSize").value;
function refineCursor(e) {
  const c = $("#brushCursor"), st = $("#stage").getBoundingClientRect(), d = rfSize() * S.view.z;
  const inside = e.clientX >= st.left && e.clientX <= st.right && e.clientY >= st.top && e.clientY <= st.bottom;
  c.hidden = !inside || !!e.target.closest?.("#refineBar, #zoomBar") || S.refine.tool === "wand" || S.refine.tool === "move";
  c.classList.toggle("erase", rfTool(e) === "erase");
  Object.assign(c.style, { width: d + "px", height: d + "px", left: e.clientX - st.left - d / 2 + "px", top: e.clientY - st.top - d / 2 + "px" });
}
function strokeOn(ctx, a, b, op) {
  ctx.globalCompositeOperation = op;
  ctx.strokeStyle = ctx.fillStyle = obj(S.refine.oid).color;
  ctx.lineWidth = rfSize();
  ctx.lineCap = ctx.lineJoin = "round";
  ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
  ctx.beginPath(); ctx.arc(b[0], b[1], rfSize() / 2, 0, Math.PI * 2); ctx.fill();
}
function refineStroke(a, b, tool) {   // 后画的覆盖先画的：补上时清掉同位置的擦除，擦掉时清掉同位置的补上
  const [on, off] = tool === "erase" ? [rfEraseCtx, rfAddCtx] : [rfAddCtx, rfEraseCtx];
  strokeOn(on, a, b, "source-over");
  strokeOn(off, a, b, "destination-out");
  composeRefine();
}
function refineSnap() {
  const R = S.refine;
  R.undo.push([rfAddCtx.getImageData(0, 0, rfAdd.width, rfAdd.height), rfEraseCtx.getImageData(0, 0, rfErase.width, rfErase.height)]);
  if (R.undo.length > 20) R.undo.shift();
}
function refineDown(e) {
  if (e.target.closest("#refineBar")) return;
  e.preventDefault();
  const R = S.refine;
  if (!rfAdd.width) return;                                     // 这一帧还没读出来
  const p = toVideo(e);
  if (R.tool === "wand") { refineSnap(); refineWand(p, e.altKey); R.dirty = true; return; }
  if (R.tool === "move") { R.moving = { start: p, src: effMask(), dx: 0, dy: 0 }; try { $("#stage").setPointerCapture(e.pointerId); } catch {} return; }
  refineSnap();
  R.drawing = { last: p, tool: rfTool(e) };
  refineStroke(p, p, R.drawing.tool);
  R.dirty = true;
  try { $("#stage").setPointerCapture(e.pointerId); } catch {}
}
function refineMove(e) {
  const R = S.refine, M = R.moving;
  if (M) {                                                       // 平移：拖的时候只挪显示，松手才写进补上 / 擦掉
    const p = toVideo(e);
    M.dx = Math.round(p[0] - M.start[0]); M.dy = Math.round(p[1] - M.start[1]);
    rfCtx.clearRect(0, 0, rfCv.width, rfCv.height);
    rfCtx.drawImage(M.src.cv, M.dx, M.dy);
    return;
  }
  const D = R.drawing;
  if (!D) return;
  const p = toVideo(e);
  refineStroke(D.last, p, D.tool);
  D.last = p;
}
addEventListener("pointerup", () => {
  const R = S.refine;
  if (!R) return;
  R.drawing = null;
  if (!R.moving) return;
  const M = R.moving;
  R.moving = null;
  if (M.dx || M.dy) { refineSnap(); applyMask(M.src.m, M.dx, M.dy); R.dirty = true; } else composeRefine();
});
const rgbOf = (col) => [1, 3, 5].map((k) => parseInt(col.slice(k, k + 2), 16));
// 现在的抠图（底图 − 擦掉 + 补上，二值）：m = 1 是主体；cv = 现在显示的那张（平移预览用）
function effMask() {
  const W = rfCv.width, H = rfCv.height, a = rfCtx.getImageData(0, 0, W, H).data, m = new Uint8Array(W * H);
  for (let k = 0; k < m.length; k++) m[k] = a[k * 4 + 3] > 127 ? 1 : 0;
  const cv = document.createElement("canvas");
  cv.width = W; cv.height = H; cv.getContext("2d").drawImage(rfCv, 0, 0);
  return { m, cv };
}
// 把「想要的抠图」（m 挪 dx, dy）写回成补上 / 擦掉两张图：想要 − 底图 = 补上，底图 − 想要 = 擦掉
function applyMask(m, dx, dy) {
  const W = rfCv.width, H = rfCv.height, b = rfBaseCtx.getImageData(0, 0, W, H).data;
  const add = new ImageData(W, H), er = new ImageData(W, H), [cr, cg, cb] = rgbOf(obj(S.refine.oid).color);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const sx = x - dx, sy = y - dy, want = sx >= 0 && sy >= 0 && sx < W && sy < H && m[sy * W + sx];
    const k = y * W + x, base = b[k * 4 + 3] > 127;
    if (want && !base) { add.data[k * 4] = cr; add.data[k * 4 + 1] = cg; add.data[k * 4 + 2] = cb; add.data[k * 4 + 3] = 255; }
    if (!want && base) er.data[k * 4 + 3] = 255;
  }
  rfAddCtx.putImageData(add, 0, 0);
  rfEraseCtx.putImageData(er, 0, 0);
  composeRefine();
}
// 魔棒：在这一帧原画面上，从点的地方往外找颜色差（各通道最大差）≤ 容差、连成一片的像素
function refineWand(p, sub) {
  const W = rfCv.width, H = rfCv.height, im = S.cache[S.d.current][S.refine.i];
  if (!ready(im) || !im) return setLog(T("这一帧画面还没读出来"), true);
  const tc = document.createElement("canvas");
  tc.width = W; tc.height = H;
  const tx = tc.getContext("2d", { willReadFrequently: true });
  tx.drawImage(im, 0, 0, W, H);
  const px = tx.getImageData(0, 0, W, H).data, tol = +$("#rfTol").value;
  const x0 = Math.round(p[0]), y0 = Math.round(p[1]);
  if (x0 < 0 || y0 < 0 || x0 >= W || y0 >= H) return;
  const s = (y0 * W + x0) * 4, r0 = px[s], g0 = px[s + 1], b0 = px[s + 2];
  const sel = new Uint8Array(W * H), stack = [y0 * W + x0];
  sel[y0 * W + x0] = 1;
  let n = 0;
  while (stack.length) {
    const k = stack.pop(); n++;
    const x = k % W, y = (k - x) / W;
    for (const q of [x > 0 ? k - 1 : -1, x < W - 1 ? k + 1 : -1, y > 0 ? k - W : -1, y < H - 1 ? k + W : -1]) {
      if (q < 0 || sel[q]) continue;
      const j = q * 4;
      if (Math.max(Math.abs(px[j] - r0), Math.abs(px[j + 1] - g0), Math.abs(px[j + 2] - b0)) > tol) continue;
      sel[q] = 1; stack.push(q);
    }
  }
  const sc = document.createElement("canvas");
  sc.width = W; sc.height = H;
  const sd = new ImageData(W, H), [cr, cg, cb] = rgbOf(obj(S.refine.oid).color);
  for (let k = 0; k < sel.length; k++) if (sel[k]) { sd.data[k * 4] = cr; sd.data[k * 4 + 1] = cg; sd.data[k * 4 + 2] = cb; sd.data[k * 4 + 3] = 255; }
  sc.getContext("2d").putImageData(sd, 0, 0);
  const [on, off] = sub ? [rfEraseCtx, rfAddCtx] : [rfAddCtx, rfEraseCtx];   // 和画笔一样：后做的覆盖先做的
  on.globalCompositeOperation = "source-over"; on.drawImage(sc, 0, 0);
  off.globalCompositeOperation = "destination-out"; off.drawImage(sc, 0, 0); off.globalCompositeOperation = "source-over";
  composeRefine();
  setLog(T`魔棒${sub ? T("去掉") : T("加上")}了 ${n} 像素（容差 ${tol}）`);
}
function refineUndo() {
  const u = S.refine.undo.pop();
  if (!u) return;
  rfAddCtx.putImageData(u[0], 0, 0);
  rfEraseCtx.putImageData(u[1], 0, 0);
  composeRefine();
  S.refine.dirty = true;
}
function refineReset() {
  refineSnap();
  rfAddCtx.clearRect(0, 0, rfAdd.width, rfAdd.height);
  rfEraseCtx.clearRect(0, 0, rfErase.width, rfErase.height);
  composeRefine();
  S.refine.dirty = true;
}
async function saveRefine() {
  const R = S.refine;
  if (!R || !R.dirty) return true;
  const W = rfAdd.width, H = rfAdd.height;
  const a = rfAddCtx.getImageData(0, 0, W, H).data, e = rfEraseCtx.getImageData(0, 0, W, H).data;
  const out = new ImageData(W, H), q = out.data;
  for (let k = 0; k < q.length; k += 4) { q[k] = a[k + 3] > 127 ? 255 : 0; q[k + 1] = e[k + 3] > 127 ? 255 : 0; q[k + 3] = 255; }
  const tmp = document.createElement("canvas");
  tmp.width = W; tmp.height = H;
  tmp.getContext("2d").putImageData(out, 0, 0);
  const blob = await new Promise((res) => tmp.toBlob(res, "image/png"));
  try {
    const o = await api(`/api/p/${S.pid}/obj/${R.oid}/edits/${R.i}`, { method: "POST", body: blob, headers: { "Content-Type": "image/png" } });
    Object.assign(obj(R.oid), o);
    for (const k of Object.keys(ghostCache)) if (k.startsWith(R.oid + ":")) delete ghostCache[k];   // 只有改过的帧地址变了，其余走浏览器缓存
    for (const k of [...alphaCache.keys()]) if (k.startsWith(R.oid + ":")) alphaCache.delete(k);
    R.dirty = false;
    setLog(uiLanguage === "en" ? `Saved matte edits for “${o.name}”, frame ${R.i}` : `已保存「${o.name}」第 ${R.i} 帧的抠图修改`);
    renderLayers();
    return true;
  } catch (err) { setLog(T("保存失败：") + err.message + T("（服务端是不是没重启？）"), true); return false; }
}
function endRefine() {
  S.refine = null;
  $("#stage").classList.remove("refining", "rf-wand", "rf-move");
  $("#refineBar").hidden = true;
  $("#brushCursor").hidden = true;
  rfCv.hidden = true;
  draw();
}
async function refineGo(d) {
  if (!(await saveRefine())) return;
  S.i = Math.min(Math.max(S.i + d, 0), S.d.n - 1);
  draw();
  await loadRefineFrame();
}
function setRefineTool(t) {
  S.refine.tool = t;
  $("#rfBrush").classList.toggle("on", t === "add");
  $("#rfEraser").classList.toggle("on", t === "erase");
  $("#rfWand").classList.toggle("on", t === "wand");
  $("#rfMove").classList.toggle("on", t === "move");
  $("#rfSizeBox").hidden = t === "wand" || t === "move";
  $("#rfTolBox").hidden = t !== "wand";
  $("#stage").classList.toggle("rf-wand", t === "wand");
  $("#stage").classList.toggle("rf-move", t === "move");
  if (t === "wand" || t === "move") $("#brushCursor").hidden = true;
}
function refineKey(e, ctrl) {   // 返回 true = 修图模式处理了这个键
  const k = e.key;
  if (ctrl && k.toLowerCase() === "z") { e.preventDefault(); refineUndo(); return true; }
  if (k === "Escape") { cancelRefine(); return true; }
  if (k === "Enter") { saveRefine().then((ok) => ok && endRefine()); return true; }
  if (k === "b" || k === "B") { setRefineTool("add"); return true; }
  if (k === "e" || k === "E") { setRefineTool("erase"); return true; }
  if (k === "w" || k === "W") { setRefineTool("wand"); return true; }
  if (k === "v" || k === "V") { setRefineTool("move"); return true; }
  if (k === "[" || k === "]") { const r = $("#rfSize"); r.value = +r.value + (k === "]" ? 1 : -1) * Math.max(2, Math.round(r.value * 0.15)); $("#rfSizeVal").textContent = r.value; return true; }
  if (k === "," || k === ".") { refineGo(k === "," ? -1 : 1); return true; }
  if (e.code === "Space") { e.preventDefault(); if (!e.repeat) { S.space = true; S.spacePanned = true; $("#stage").classList.add("grab"); } return true; }   // 修图时空格只用来平移，不播放
  if (["h", "H", "Delete", "Backspace", "f", "F", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(k)) return true;   // 其他编辑快捷键暂停
  return false;
}
$("#rfReset").onclick = () => refineReset();
$("#rfBrush").onclick = () => setRefineTool("add");
$("#rfEraser").onclick = () => setRefineTool("erase");
$("#rfWand").onclick = () => setRefineTool("wand");
$("#rfMove").onclick = () => setRefineTool("move");
$("#rfTol").oninput = () => ($("#rfTolVal").textContent = $("#rfTol").value);
$("#rfSize").oninput = () => ($("#rfSizeVal").textContent = $("#rfSize").value);
$("#rfUndo").onclick = () => refineUndo();
$("#rfPrev").onclick = () => refineGo(-1);
$("#rfNext").onclick = () => refineGo(1);
async function cancelRefine() {                                   // ✕ / Esc：有没存的改动先问
  if (S.refine && S.refine.dirty && !(await uiConfirm(T("放弃这次修整里没保存的改动？"), { ok: T("放弃"), no: T("继续修整"), danger: true }))) return;
  if (S.refine) endRefine();
}
$("#rfCancel").onclick = () => cancelRefine();
$("#rfSave").onclick = async () => { if (await saveRefine()) endRefine(); };
$("#refineBar").addEventListener("pointerdown", (e) => e.stopPropagation());

