// 视频 PS 前端 7c/9：整段缓存。
// 图层多了以后，每播放一帧都要现等「背景 + 每个图层的像素 + 空区 + 叠加层 + 分区」全部到齐，
// 一帧十几个请求，播放就会一帧一帧地卡。这里一次把每一帧合成成一张图：播放时一帧只画一张图。
// 图层（位置、抠图、显隐、版本、画幅…）一变，签名就变，缓存作废，需要重新点一次。

let pcCache = null;                                     // {sig, from, to, imgs: [Image 或 null × n]}
let pcBusy = false, pcCancel = false;

function pcSig() {                                      // 影响画面的一切；任何一项变了，缓存就不能再用
  if (!S.d) return "";
  const fr = S.d.frame || { x: 0, y: 0, w: S.d.w, h: S.d.h };
  return JSON.stringify([
    S.pid, S.d.current, rev(), S.pw, S.bgVisible, S.tint, S.zones, S.zoneMode, S.zoneFocus ?? null, S.growLine || false,
    [fr.x, fr.y, fr.w, fr.h, fr.crop ? [fr.crop.t, fr.crop.b] : 0],
    objs().map((o) => [o.id, o.version, o.visible, o.opacity ?? 1, o.grow ?? 0, o.alpha || "", o.edge || "",
      o.start, o.end, o.z ?? 0, o.mask, [o.tf.x, o.tf.y, o.tf.sx, o.tf.sy, o.tf.rot],
      o.medits || 0, o.ext_masks || o.box_ext || 0, o.hole_rect || 0, o.box_redraw || 0, o.edge_fill || 0, o.off || 0, cleanKey(o), o.hole_dilate || 0, o.hole_t || 0, o.protect || 0, o.ring_redraw || 0, o.keep_masks || 0]),
  ]);
}
const pcRange = () => ({ from: Math.max(0, Math.min(S.start, S.end)), to: Math.min(S.d.n - 1, Math.max(S.start, S.end)) });
const pcSame = () => !!(pcCache && S.d && pcCache.sig === pcSig());

// 这一帧有没有缓存好的合成图（对比、修图、首尾帧这些模式下不用缓存，走原来的实时画面）
function pcFrameOf(i) {
  if (!pcCache || !S.d || pcCache.sig !== pcSig()) return null;
  if (S.compare || S.cmp || S.cmpSide || S.refine || S.stillShow) return null;
  if (typeof imgEditing === "function" && imgEditing()) return null;
  if (!pcCache.imgs[i]) return null;
  if (!S.cache[PC_VID]) pcInstall();
  return pcCache.imgs[i];
}
pcFrameHook = pcFrameOf;                                  // 01-core 里的 pcFrame(i) 用它（文件加载顺序问题）

function pcInstall() {                                  // 让 draw() 里 S.cache[PC_VID][i] 拿到合成图
  if (S.d) S.cache[PC_VID] = lazyFrames((k) => (pcCache && pcCache.imgs[k]) || null, S.d.n);
}
function pcDrop() { pcCache = null; if (S.d) delete S.cache[PC_VID]; }

// ---------- 合成一帧 ----------
let pcCheckerTmp = null;
function pcChecker(ctx) {                               // 背景关掉时的透明棋盘格（和 #bgChecker 一致）
  if (!pcCheckerTmp) {
    const t = document.createElement("canvas"); t.width = t.height = 16;
    const c = t.getContext("2d");
    c.fillStyle = "#cdcdcd"; c.fillRect(0, 0, 16, 16);
    c.fillStyle = "#969696"; c.fillRect(0, 0, 8, 8); c.fillRect(8, 8, 8, 8);
    pcCheckerTmp = t;
  }
  return ctx.createPattern(pcCheckerTmp, "repeat");
}
function pcCompose(cv, i) {                             // 把这一帧现在显示的东西画进 cv（预览分辨率）
  const vid = S.compare ? "v0" : S.d.current;
  const base = S.cache[vid] && S.cache[vid][i];
  if (!base || !ready(base)) return false;
  const W = base.naturalWidth, H = base.naturalHeight;
  const kp = W / S.d.w;                                   // 预览像素 / 原画面像素
  // 画布 = 原视频和画幅的并集（画幅扩大时，图层移动 / 放大伸出原视频的部分也要缓存进去）；cv._pcBox 记它在原画面坐标里的位置
  const fr = getFrame(), crop = fr.crop;
  const bx0 = Math.min(0, fr.x), by0 = Math.min(0, fr.y), bx1 = Math.max(S.d.w, fr.x + fr.w), by1 = Math.max(S.d.h, fr.y + fr.h);
  const CW = Math.round((bx1 - bx0) * kp), CH = Math.round((by1 - by0) * kp), ox = -bx0 * kp, oy = -by0 * kp;
  if (cv.width !== CW || cv.height !== CH) { cv.width = CW; cv.height = CH; }
  cv._pcBox = { x0: bx0, y0: by0, x1: bx1, y1: by1 };
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, CW, CH);
  ctx.save();
  ctx.beginPath();                                        // 原画面只画裁切黑边后的那几行
  ctx.rect(ox, oy + (crop ? crop.t * kp : 0), W, H - (crop ? (crop.t + crop.b) * kp : 0));
  ctx.clip();
  if (S.bgVisible) ctx.drawImage(base, ox, oy, W, H);
  else { ctx.fillStyle = pcChecker(ctx); ctx.fillRect(ox, oy, W, H); }
  ctx.restore();
  ctx.translate(ox, oy);                                  // 下面都按原画面坐标画（可以画到原视频外）
  const box = $("#layers");
  const holes = box.querySelector('canvas[data-id="holes"]');   // 移走 / 隐藏的图层留下的透明格洞
  if (holes && !holes.hidden && holes.width) ctx.drawImage(holes, 0, 0, W, H);
  const list = layersFrontToBack().slice().reverse().map(effObj).filter(o=>layerVisible(o));
  for (const o of list) {
    const el = box.querySelector(`canvas.lyr[data-id="${o.id}"]`);
    if (!el || el.hidden || !el.width) continue;
    const origin=apply(o,[0,0]), x=apply(o,[1,0]), y=apply(o,[0,1]);
    ctx.save();
    ctx.globalAlpha = o.opacity ?? 1;
    ctx.transform(x[0]-origin[0],x[1]-origin[1],y[0]-origin[0],y[1]-origin[1],origin[0]*kp,origin[1]*kp);
    ctx.drawImage(el, 0, 0, W, H);
    ctx.restore();
  }
  const ov = $("#ov");                                    // 主体颜色叠加层
  if (S.tint && !ov.hidden && ov.width) ctx.drawImage(ov, 0, 0, W, H);
  const zc = $("#zoneCv");                                // 重绘范围叠加层（是画幅大小，按画幅位置贴）
  if (!zc.hidden && zc.width) ctx.drawImage(zc, fr.x * kp, fr.y * kp, zc.width, zc.height);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  return true;
}
function pcSnap(i) {                                     // 合成 → JPEG → 解好码的 Image（播放时直接贴）
  const cv = document.createElement("canvas");
  if (!pcCompose(cv, i)) return Promise.resolve(null);
  return new Promise((resolve) => cv.toBlob(async (blob) => {
    if (!blob) return resolve(null);
    const im = new Image(), url = URL.createObjectURL(blob);
    im._pcBox = cv._pcBox;                                // 缓存帧在原画面坐标里的位置（可能比原视频大）
    im.onload = () => { im._decoded = true; URL.revokeObjectURL(url); resolve(im); };
    im.onerror = () => { URL.revokeObjectURL(url); resolve(null); };
    im.src = url;
  }, "image/jpeg", 0.92));
}
function pcWaitFrame(i, sig) {                           // 等这一帧在实时预览里真正显示出来（图都到齐了）
  return new Promise((resolve) => {
    const t0 = performance.now();
    const poll = () => {
      if (pcCancel) return resolve(false);
      if (S.pcSigCheck !== sig) return resolve(false);
      if (S.i === i && S.fi === i) return resolve(true);
      if (performance.now() - t0 > 12000) return resolve(false);
      setTimeout(poll, 25);
    };
    poll();
  });
}

// ---------- 缓存整段 ----------
function pcLabel(t) { const b = $("#cacheBtn"); if (b) { b.title = t; b.setAttribute("aria-label", t); } }
function pcSync() {
  if (pcBusy) return;
  const b = $("#cacheBtn");
  if (!b) return;
  const same = pcSame();
  b.classList.toggle("on", same);
  b.title = same ? T("整段已缓存；图层一改就要重新缓存") : T("缓存整段：把每一帧先合成好，播放不再一帧一帧等（图层改了要重新缓存）");
  const label = same ? T("整段已缓存（图层一改要重新缓存）") : pcCache ? T("重新缓存整段") : T("缓存整段");
  b.setAttribute("aria-label", label);
  if (!same) b.title = label;
}
async function pcBuild() {
  if (pcBusy) { pcCancel = true; pcLabel(T("正在停止…")); return; }   // 再点一次 = 停止
  if (!S.d) return;
  if (S.playing) togglePlay();
  const { from, to } = pcRange();
  const sig = pcSig(), total = to - from + 1;
  const old = pcCache;
  pcBusy = true; pcCancel = false;
  S.pcSigCheck = sig;
  pcCache = null;                                        // 构建时走实时画面；建完再挂上
  delete S.cache[PC_VID];
  const imgs = new Array(S.d.n);
  let ok = 0, failed = 0;
  pcLabel(T`缓存中 0/${total}`);
  try {
    for (let i = from; i <= to; i++) {
      if (pcCancel) break;
      S.i = i;
      draw();
      const shown = await pcWaitFrame(i, sig);
      if (pcCancel) break;
      if (!shown) { failed++; continue; }
      const im = await pcSnap(i);
      if (pcCancel) break;
      if (!im) { failed++; continue; }
      imgs[i] = im;
      ok++;
      pcLabel(T`缓存中 ${ok}/${total}`);
    }
  } finally {
    const done = !pcCancel && !failed && ok === total;
    pcBusy = false;
    if (done && total > 0) { pcCache = { sig, from, to, imgs }; pcInstall(); }
    else { pcCache = old; if (old) pcInstall(); else delete S.cache[PC_VID]; }
    pcCancel = false;
    draw();
    if (failed) previewStatus(T`整段缓存没做完：有 ${failed} 帧没画出来，再点一次缓存按钮重试`);
    pcSync();
  }
}
$("#cacheBtn").onclick = () => pcBuild();
