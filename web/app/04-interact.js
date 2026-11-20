// 视频 PS 前端 4/9：画面上的鼠标操作、时间轴、播放、快捷键。
// 9 个文件是同一个全局作用域的普通脚本，按 index.html 里的顺序加载（原来是一个 app.js）。
// ---------- 画面上的鼠标操作：点选、移动、缩放、旋转 ----------
function toVideo(e) {
  const g = $("#gizmo").getBoundingClientRect(), k = kScale();
  return [(e.clientX - g.left) / k, (e.clientY - g.top) / k];
}
function inside(pt, poly) {
  let c = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < (xj - xi) * (pt[1] - yi) / (yj - yi) + xi) c = !c;
  }
  return c;
}
function invert(o, p) {
  if (o.parent_id) p = invert(obj(o.parent_id), p);   // 变换后坐标 → 原画面坐标
  const [cx, cy] = centerOf(o), tf = o.tf;
  const v = rotate([p[0] - cx - tf.x, p[1] - cy - tf.y], -tf.rot);
  return [cx + v[0] / tf.sx, cy + v[1] / tf.sy];
}
const alphaCache = new Map();   // "主体:帧" → 该帧图层的透明度（只缓存最近用过的几帧）
function alphaData(o, i) {
  const im = ghostFrames(o)[i];
  if (!im || !im.complete || !im.naturalWidth) return null;
  const key = o.id + ":" + i;
  if (!alphaCache.has(key)) {
    const c = document.createElement("canvas");
    c.width = im.naturalWidth; c.height = im.naturalHeight;
    const ctx = c.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(im, 0, 0);
    alphaCache.set(key, { w: c.width, h: c.height, d: ctx.getImageData(0, 0, c.width, c.height).data });
    if (alphaCache.size > 24) alphaCache.delete(alphaCache.keys().next().value);
  }
  return alphaCache.get(key);
}
function hitLocal(p) {   // 返回 id；null = 没点中；undefined = 图层帧还没加载好，判断不了
  for (const o of layersFrontToBack()) {
    if (!o.visible || S.i < o.start || S.i > o.end) continue;
    const a = alphaData(o, S.i);
    if (!a) return undefined;
    const s = invert(o, p), x = Math.round(s[0] * a.w / S.d.w), y = Math.round(s[1] * a.h / S.d.h);
    for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) {   // 边缘放宽几个像素
      const xx = x + dx, yy = y + dy;
      if (xx >= 0 && yy >= 0 && xx < a.w && yy < a.h && a.d[(yy * a.w + xx) * 4 + 3] > 40) return o.id;
    }
  }
  return null;
}
async function hitTest(p) {
  const id = hitLocal(p);
  if (id !== undefined) return id;
  return (await api(`/api/p/${S.pid}/hit/${S.d.current}/${S.i}?x=${p[0].toFixed(1)}&y=${p[1].toFixed(1)}`)).id;
}

$("#stage").addEventListener("dragstart", (e) => e.preventDefault());
$("#stage").addEventListener("pointerdown", async (e) => {
  if (!S.d || e.target.closest("#zoomBar, #shapeBar, #refineBar, #selBar, #tabSub")) return;   // 浮动工具条上的按钮：不当成画布上的操作
  if (e.button === 1 || (e.button === 0 && (S.space || S.tool === "hand"))) return startPan(e);   // 中键 / 空格+左键 / 抓手工具：平移画布
  if (pickOn() && (e.button === 0 || e.button === 2)) return pickDown(e);   // 点选识别：左键选中、右键排除
  if (e.button !== 0) return;
  if (S.refine) return refineDown(e);
  if (S.shape) return shapeDown(e);
  e.preventDefault();
  const fh = e.target.dataset?.fh;
  if (fh || e.target.classList?.contains("flabel")) { setFrameMode(true); return startFrameDrag(fh || "move", e); }
  if (S.frameMode) {
    const m = toVideo(e), f = getFrame();
    if (m[0] >= f.x && m[0] <= f.x + f.w && m[1] >= f.y && m[1] <= f.y + f.h) return startFrameDrag("move", e);
    setFrameMode(false);                                          // 点到画幅外：退出画幅编辑，照常选主体
  }
  S.pdown = true;
  const m0 = toVideo(e), handle = e.target.dataset?.h;
  const sel = selected().filter((o) => o.visible && S.i >= o.start && S.i <= o.end);
  const xspan = e.target.dataset?.xs, xside = e.target.dataset?.x;
  if ((xspan || xside) && sel.length === 1 && !sel[0].locked && curExt(sel[0])) {   // 拖主体扩绘区的箭头 / 拉开那条边的范围两头：拖的是一份拷贝，松手才存
    const o = sel[0], m = { ...curExt(o) }, base = extRect(o, S.i).base;
    S.xdrag = { o, span: xspan, side: xside, m, m0: JSON.stringify(m), base };
    try { $("#gizmo").setPointerCapture(e.pointerId); } catch {}
    return;
  }
  if (handle && sel.length === 1) return startDrag(handle, sel, m0, e);
  if (sel.some((o) => inside(m0, polygon(o, S.i)))) {             // 在选中主体的框里：直接移动
    if (!e.shiftKey && !e.ctrlKey && !e.metaKey) return startDrag("move", selected(), m0, e);
  }
  const id = await hitTest(m0);
  const add = e.shiftKey || e.ctrlKey || e.metaKey;                // 和图层列表一样：Shift / Ctrl 加选
  if (!id) { if (!add) select(null); return; }
  if (add) return select(id, true);
  if (!S.sel.has(id)) select(id);
  if (S.pdown) startDrag("move", selected(), m0, e);              // 命中检测返回前鼠标已松开：只选中，不开始拖动
});

function startDrag(kind, os, m0, e) {
  const ids = new Set(os.map(o=>o.id));
  os = os.filter(o => !o.locked && (!o.parent_id || !ids.has(o.parent_id)));
  if (!os.length) return;
  const o = os[0], b = frameBox(o, S.i);
  if (o.parent_id && kind !== "move") m0 = invert(obj(o.parent_id), m0);
  S.drag = { kind, os, m0, before: os.map(snap), tf0: os.map((x) => ({ ...x.tf })), box: b };
  if (kind === "rot") {
    S.drag.pivot = apply(o, at(b, 0.5, 0.5));
    if (o.parent_id) S.drag.pivot = invert(obj(o.parent_id), S.drag.pivot);
    S.drag.a0 = Math.atan2(m0[1] - S.drag.pivot[1], m0[0] - S.drag.pivot[0]);
  } else if (kind !== "move") {
    const [u, v] = HANDLES[kind];
    S.drag.hs = at(b, u, v);                                      // 拖的把手（原画面坐标）
    S.drag.q = at(b, kind.includes("w") ? 1 : kind.includes("e") ? 0 : 0.5,
                  kind.includes("n") ? 1 : kind.includes("s") ? 0 : 0.5);   // 对面的固定点
    S.drag.qOut = apply(o, S.drag.q);
    if (o.parent_id) S.drag.qOut = invert(obj(o.parent_id), S.drag.qOut);
  }
  try { $("#gizmo").setPointerCapture(e.pointerId); } catch {}
}

addEventListener("pointermove", (e) => {
  if (!S.d) return;
  if (S.refine) refineCursor(e);
  if (S.pan) return panMove(e);
  if (S.refine) return refineMove(e);
  if (S.shape) return shapeMove(e);
  if (S.fdrag) return frameMove(e);
  if (S.xdrag) {                                                 // 扩展量 = 鼠标到主体外框那条边的距离（只往外，不往里）
    const m = toVideo(e), X = S.xdrag, b = X.base;
    if (X.span) {                                                  // 范围：按外框的比例，两头至少留 5%
      const side = X.span[0], end = X.span[1], ex = X.m;
      let f = side === "l" || side === "r" ? (m[1] - b.y0) / (b.y1 - b.y0) : (m[0] - b.x0) / (b.x1 - b.x0);
      f = end === "0" ? Math.min(Math.max(f, 0), (ex[side + "1"] ?? 1) - 0.05) : Math.max(Math.min(f, 1), (ex[side + "0"] ?? 0) + 0.05);
      X.m[X.span] = Math.round(f * 100) / 100;
      drawGizmo();
      return;
    }
    const d = { t: b.y0 - m[1], b: m[1] - b.y1, l: b.x0 - m[0], r: m[0] - b.x1 }[X.side];
    X.m[X.side] = Math.max(0, Math.round(d));
    drawGizmo();
    return;
  }
  if (!S.drag) return hoverAt(e);
  const D = S.drag;
  let m = toVideo(e);
  if (D.kind !== "move" && D.os[0].parent_id) m = invert(obj(D.os[0].parent_id), m);
  if (D.kind === "move") {
    D.os.forEach((o, n) => { const a = o.parent_id ? invert(obj(o.parent_id), m) : m, b = o.parent_id ? invert(obj(o.parent_id), D.m0) : D.m0; o.tf.x = D.tf0[n].x + a[0] - b[0]; o.tf.y = D.tf0[n].y + a[1] - b[1]; });
  } else if (D.kind === "rot") {
    const o = D.os[0], t0 = D.tf0[0];
    let rot = t0.rot + (Math.atan2(m[1] - D.pivot[1], m[0] - D.pivot[0]) - D.a0) * 180 / Math.PI;
    rot = ((rot + 180) % 360 + 360) % 360 - 180;
    if (e.shiftKey) rot = Math.round(rot / 15) * 15;
    setTf(o, { ...t0, rot }, at(D.box, 0.5, 0.5), D.pivot);       // 以框中心为轴旋转
  } else {
    const o = D.os[0], t0 = D.tf0[0];
    const v = rotate([m[0] - D.qOut[0], m[1] - D.qOut[1]], -t0.rot);   // 鼠标相对固定点，换到图层自己的坐标轴
    const d = [D.hs[0] - D.q[0], D.hs[1] - D.q[1]];
    let sx = t0.sx, sy = t0.sy;
    const corner = D.kind.length === 2;
    if (corner && !e.shiftKey) {                                   // 四角：默认等比
      const cur = [d[0] * t0.sx, d[1] * t0.sy];
      const f = (v[0] * cur[0] + v[1] * cur[1]) / (cur[0] ** 2 + cur[1] ** 2 || 1);
      sx = t0.sx * f; sy = t0.sy * f;
    } else {
      if (D.kind.includes("e") || D.kind.includes("w")) sx = d[0] ? v[0] / d[0] : sx;
      if (D.kind.includes("n") || D.kind.includes("s")) sy = d[1] ? v[1] / d[1] : sy;
    }
    const lim = (s) => (Math.abs(s) < 0.05 ? 0.05 * (Math.sign(s) || 1) : s);
    setTf(o, { ...t0, sx: lim(sx), sy: lim(sy) }, D.q, D.qOut);   // 对角保持不动
  }
  previewStatus(T('正在调整位置，透明区域尚未更新；松开鼠标后计算…'));
  drawZones(); drawLayers(); drawGizmo(); renderProps();
});

function setTf(o, tf, srcPt, outPt) {   // 设新变换，同时调整平移，让 srcPt 仍落在 outPt
  const [cx, cy] = centerOf(o);
  const v = rotate([(srcPt[0] - cx) * tf.sx, (srcPt[1] - cy) * tf.sy], tf.rot);
  tf.x = outPt[0] - cx - v[0];
  tf.y = outPt[1] - cy - v[1];
  o.tf = tf;
}

addEventListener("pointerup", () => {
  S.pdown = false;
  if (S.xdrag) {
    const X = S.xdrag;
    S.xdrag = null;
    if (JSON.stringify(X.m) === X.m0) return draw();
    const patch = {};
    for (const k of [...EXT_SIDES, ...EXT_SIDES.flatMap((s) => [s + "0", s + "1"])]) if (X.m[k] !== undefined) patch[k] = X.m[k];
    return editExt(X.o, X.m.id, patch, T("主体扩绘区"));            // 视频编辑：一步撤销；图片编辑：存成这张图的单图覆盖
  }
  const D = S.drag;
  if (!D) return;
  S.drag = null;
  const after = D.os.map(snap);
  if (JSON.stringify(after) === JSON.stringify(D.before)) return draw();
  const label = { move: T("移动"), rot: T("旋转") }[D.kind] || T("缩放");
  S.undo.push({ before: D.before, after, label });
  S.redo = [];
  persist(after);
  afterChange();
});

let hoverTimer = null;
function hoverAt(e) {
  if (e.target.closest && !e.target.closest("#stage")) return;
  if (hoverTimer) return;                                       // 每帧最多算一次
  hoverTimer = requestAnimationFrame(async () => {
    hoverTimer = null;
    const id = await hitTest(toVideo(e)).catch(() => null);
    if (id !== S.hover) { S.hover = id; drawLayers(); drawGizmo(); }
    $("#stage").style.cursor = id ? "move" : "default";
  });
}
$("#stage").addEventListener("pointerleave", () => { if (S.hover) { S.hover = null; drawLayers(); drawGizmo(); } });

// ---------- 时间轴 ----------
const fx = (f) => (S.d && S.d.n > 1 ? (f / (S.d.n - 1)) * 100 : 0);
function drawTimeline() {
  if (!S.d) return;
  $("#range").style.left = fx(S.start) + "%";
  $("#range").style.width = Math.max(fx(S.end) - fx(S.start), 0.5) + "%";
  $("#playhead").style.left = fx(S.fi ?? S.i) + "%";
  $("#rangeText").textContent = T`帧范围 ${S.start}–${S.end} · ${((S.end - S.start + 1) / S.d.fps).toFixed(2)} s`;
}
function trackFrame(e) {
  const r = $("#track").getBoundingClientRect();
  return Math.round(Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1) * (S.d.n - 1));
}
$("#track").addEventListener("mousedown", (e) => {
  if (!S.d) return;
  const h = e.target.dataset.h;
  const move = (ev) => {
    const f = trackFrame(ev);
    if (h === "start") S.start = Math.min(f, S.end);
    else if (h === "end") S.end = Math.max(f, S.start);
    else S.i = f;
    draw();
  };
  move(e);
  const up = () => { removeEventListener("mousemove", move); removeEventListener("mouseup", up); };
  addEventListener("mousemove", move); addEventListener("mouseup", up);
});

// ---------- 播放：动画帧驱动；帧未备齐时缓冲，不让时钟跑远而画面卡在旧帧 ----------
let playTimer = null, playLast = 0, playBudget = 0;
function tick(now) {
  if (!S.playing) return;
  playBudget += Math.min(250, Math.max(0, now - playLast));
  playLast = now;
  const frameMs = 1000 / S.d.fps, span = S.end - S.start + 1;
  if (playBudget >= frameMs) {
    const steps = Math.max(1, Math.floor(playBudget / frameMs));
    const next = S.start + ((S.i - S.start + steps) % span + span) % span;
    const cached = typeof pcFrame === "function" && pcFrame(next);   // 整段缓存里有这一帧：直接播合成图
    const vid = cached ? PC_VID : S.compare ? "v0" : S.d.current;
    preload(vid);
    if (!cached) preloadZones(next);
    if (cached || frameReady(vid, next)) {
      S.i = next;
      playBudget %= frameMs;
      draw();
    } else {
      // 只积累一帧待播放时间，下一次继续等相邻帧，不追赶尚未下载的远帧。
      playBudget = frameMs;
      previewStatus(T('正在缓冲完整播放帧（含图层与透明区域），暂时保留上一帧…'));
    }
  }
  playTimer = requestAnimationFrame(tick);
}
function togglePlay() {
  if (!S.d) return;
  S.playing = !S.playing;
  const pb = $("#playBtn");
  if (pb) {
    // 图标按钮：只换图标和说明，不写文字（旧版是 ▶ / ❚❚ 文本）
    pb.innerHTML = typeof IC === "function" ? IC(S.playing ? "pause" : "play") : (S.playing ? "❚❚" : "▶");
    pb.setAttribute?.("aria-label", S.playing ? T("暂停") : T("播放"));
    pb.classList?.toggle("on", S.playing);
  }
  cancelAnimationFrame(playTimer);
  if (S.playing) {
    if (S.i < S.start || S.i >= S.end) S.i = S.start;
    playLast = performance.now(); playBudget = 0;
    draw(); // 先请求当前帧与播放缓冲窗口
    playTimer = requestAnimationFrame(tick);
  } else draw();
}
$("#playBtn").onclick = togglePlay;
const step = (d) => { S.i = Math.min(Math.max(S.i + d, 0), S.d.n - 1); draw(); };
const gotoEdge = (last) => {                                       // 跳到整段视频的第一帧 / 最后一帧
  if (!S.d) return;
  S.i = last ? S.d.n - 1 : 0;
  playBudget = 0;
  draw();
};
$("#firstFrameBtn").onclick = () => gotoEdge(false);
$("#lastFrameBtn").onclick = () => gotoEdge(true);

// ---------- 快捷键 ----------
addEventListener("keydown", (e) => {
  if (e.target.closest?.('.ui-menu,.ui-popover,.ui-dialog,#tabMenu,#layerMenu')) return;
  if (!S.d || ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
  if (S.shape && S.shape.view === "img") { shapeKey(e, e.ctrlKey || e.metaKey); return; }   // 局部重绘（图片上画）：只有画形状的快捷键
  const still = !!imgEditing();                                    // 首帧 / 尾帧：视频编辑停在这一帧，不能换帧、播放
  if (S.tab !== "video" && !still) return;                         // 资料卡 / 背景图 / 结果标签里没有视频的快捷键
  const ctrl = e.ctrlKey || e.metaKey;
  if (S.refine && refineKey(e, ctrl)) return;
  if (S.shape && shapeKey(e, ctrl)) return;
  if (ctrl && e.key.toLowerCase() === "z") { e.preventDefault(); return e.shiftKey ? redo() : undo(); }
  if (ctrl && e.key.toLowerCase() === "y") { e.preventDefault(); return redo(); }
  if (ctrl && (e.code === "BracketRight" || e.code === "BracketLeft")) {
    e.preventDefault();
    const up = e.code === "BracketRight";
    return zOrder(e.shiftKey ? (up ? "front" : "back") : (up ? "up" : "down"));
  }
  if (e.code === "Space") {                                        // 按住空格拖动 = 平移；单按 = 播放（松开时判断）
    if (e.target.closest?.("input, select, textarea, summary, a")) return;   // 焦点在输入框 / 勾选框上：空格照常按它
    const btn = e.target.closest?.("button, [role=button]");
    if (btn && btn.matches(":focus-visible")) return;              // 用键盘切到按钮上：空格照常按它
    if (btn) btn.blur();                                           // 鼠标点过的按钮还留着焦点：空格照常平移 / 播放
    e.preventDefault();
    if (!e.repeat) { S.space = true; S.spacePanned = false; $("#stage").classList.add("grab"); }
    return;
  }
  if (e.shiftKey && !ctrl && ["Digit0", "Digit1", "Digit2"].includes(e.code)) {
    e.preventDefault();
    return fitView({ Digit0: "fitframe", Digit1: "fitall", Digit2: "sel" }[e.code]);
  }
  if (ctrl && (e.key === "0" || e.key === "1")) { e.preventDefault(); return e.key === "0" ? fitView("fitframe") : zoomTo(1); }
  if (ctrl && (e.key === "=" || e.key === "+")) { e.preventDefault(); return zoomBy(1.25); }
  if (ctrl && e.key === "-") { e.preventDefault(); return zoomBy(0.8); }
  if (!ctrl && (e.key === "f" || e.key === "F")) return setFrameMode(!S.frameMode);
  if (e.key === "Escape") { if (S.frameMode) return setFrameMode(false); return select(null); }
  if (S.frameMode && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) {
    e.preventDefault();
    const n = e.shiftKey ? 10 : 1, f = { ...getFrame() };
    if (e.key === "ArrowLeft") f.x -= n; if (e.key === "ArrowRight") f.x += n;
    if (e.key === "ArrowUp") f.y -= n; if (e.key === "ArrowDown") f.y += n;
    return commitFrame(f, T("移动画幅"));
  }
  if (still && [",", ".", "i", "I", "o", "O"].includes(e.key)) return;
  if (e.key === "," ) return step(-1);
  if (e.key === ".") return step(1);
  const arrows = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
  if (arrows[e.key]) {
    e.preventDefault();
    const os = selected().filter((o) => !o.locked);
    if (!os.length) return still ? null : e.key === "ArrowLeft" ? step(-1) : e.key === "ArrowRight" ? step(1) : null;
    const n = e.shiftKey ? 10 : 1, [dx, dy] = arrows[e.key];
    return commit(os, (o) => { o.tf.x += dx * n; o.tf.y += dy * n; }, T("微调"));
  }
  if (!ctrl && (e.key === "h" || e.key === "H")) {
    const os = selected();
    if (os.length) commit(os, toggleVis, T("显示 / 隐藏"));
    return;
  }
  if (!ctrl && (e.key === "Delete" || e.key === "Backspace")) {   // 删除选中图层（会先问）
    if (selected().length && !imgEditing()) $("#delLayerBtn").click();
    return;
  }
  if (!ctrl && (e.key === "m" || e.key === "M")) return toggleZones();
  if (!ctrl && (e.key === "a" || e.key === "A")) return toggleMaskView();
  if (e.key === "i" || e.key === "I") { S.start = Math.min(S.i, S.end); return draw(); }
  if (e.key === "o" || e.key === "O") { S.end = Math.max(S.i, S.start); return draw(); }
  if ((e.key === "c" || e.key === "C") && !S.compare) { S.compare = true; draw(); }
});
addEventListener("keyup", (e) => {
  if (e.key === "c" || e.key === "C") { S.compare = false; draw(); }
  if (e.code === "Space" && S.space) {
    S.space = false;
    $("#stage").classList.remove("grab");
    if (!S.spacePanned && !imgEditing()) togglePlay();
  }
});
addEventListener("blur", () => { S.space = false; $("#stage").classList.remove("grab"); });

