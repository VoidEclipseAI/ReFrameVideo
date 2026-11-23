// 视频 PS 前端 3/9：右栏：图层面板、属性（变换 / 蒙版与重绘区域）、撤销 / 重做、选中、接入设置。
// 9 个文件是同一个全局作用域的普通脚本，按 index.html 里的顺序加载（原来是一个 app.js）。
// ---------- 图层面板 ----------
function renderLayers() {
  pruneExt();                                                       // 刚添加、还没拉开的主体扩绘区：离开了就取消
  const list = layersFrontToBack().filter(o=>!o.parent_id).flatMap(o => [o, ...(o.parts || []).slice().sort((a,b) => (b.z || 0) - (a.z || 0))]);
  const frIn = (s, e) => T`<input class="fr" data-k="start" type="number" min="0" max="${S.d.n - 1}" value="${s}" title="起始帧">–<input class="fr" data-k="end" type="number" min="0" max="${S.d.n - 1}" value="${e}" title="结束帧">`;
  const extSel = (o, m) => S.sel.has(o.id) && curExt(o)?.id === m.id;
  const extRow = (o, m) => T`<div class="layer child-layer extrow ${extSel(o, m) ? "sel" : ""} ${m.off ? "hidden" : ""}" data-id="${o.id}" data-ext="${m.id}">
      <span class="ic eye ${m.off ? "off" : ""}" role="button" title="启用 / 停用这个主体扩绘区">${IC(m.off ? "eye-off" : "eye")}</span>
      <span class="ic kind" title="主体扩绘区">${IC("expand")}</span>
      <div class="meta"><div class="nm" title="${esc(uiLanguage === "en" ? `Subject extension of "${o.name}" (double-click to rename)` : `跟随「${o.name}」的主体扩绘区（双击改名）`)}">${esc(m.name || T("主体扩绘区"))}</div>
        <div class="rg">${frIn(m.start ?? 0, m.end ?? S.d.n - 1)}<span class="rg-x">${["t", "b", "l", "r"].filter((k) => m[k] > 0).map((k) => ({ t: T("上"), b: T("下"), l: T("左"), r: T("右") })[k] + " " + m[k] + " px").join(" · ") || T("未拉开")}</span></div></div>
      <span class="ic extdel" role="button" title="删除这个主体扩绘区">${IC("x")}</span>
    </div>`;
  // 主体局部保留（keep_masks）：和主体扩绘区一样作为从蒙版列在主体下面
  const keepSel = (o, m) => S.sel.has(o.id) && (S.keepSel || {})[o.id] === m.id;
  const keepRow = (o, m) => T`<div class="layer child-layer extrow keeprow ${keepSel(o, m) ? "sel" : ""} ${m.off ? "hidden" : ""}" data-id="${o.id}" data-keep="${esc(m.id)}">
      <span class="ic eye ${m.off ? "off" : ""}" role="button" title="启用 / 停用这个主体局部保留">${IC(m.off ? "eye-off" : "eye")}</span>
      <span class="ic kind" title="主体局部保留"><i class="keep-kind"></i></span>
      <div class="meta"><div class="nm" title="${esc(uiLanguage === "en" ? `Subject keep area of "${o.name}": kept inside, redrawn outside (double-click to rename)` : `「${o.name}」的主体局部保留：范围里保留，范围外重绘（双击改名）`)}">${esc(m.name)}</div>
        <div class="rg">${frIn(m.start ?? 0, m.end ?? S.d.n - 1)}</div></div>
      <span class="ic extdel keepdel" role="button" title="删除这个主体局部保留">${IC("x")}</span>
    </div>`;
  $("#layerList").innerHTML = (list.length ? list.map((o) => T`
    <div class="layer ${o.parent_id ? "child-layer" : ""} ${S.sel.has(o.id) ? "sel" : ""} ${(o.shape ? !o.off : o.visible) ? "" : "hidden"}" data-id="${o.id}" draggable="true">
      ${o.shape ? T`<span class="ic eye ${o.off ? "off" : ""}" role="button" title="启用 / 停用这个蒙版（H）">${IC(o.off ? "eye-off" : "eye")}</span>`
        : T`<span class="ic eye ${o.visible ? "" : "off"}" role="button" title="显示 / 隐藏（H）">${IC(o.visible ? "eye" : "eye-off")}</span>`}
      <span class="ic lock ${o.locked ? "" : "off"}" role="button" title="${o.locked ? T("已锁定（点一下解锁）") : T("锁定")}">${IC(o.locked ? "lock" : "unlock")}</span>
      ${o.prefill ? T('<span class="thumb grid prefill" title="背景预填：范围里的空区生成时用背景图填，不涂灰"></span>') : o.protect ? T('<span class="thumb grid protect" title="保护蒙版：画出来的地方一律不重绘，保留原样"></span>') : o.shape ? T('<span class="thumb grid" title="空区蒙版：画出来的地方当空区，生成时重绘"></span>') : `<span class="thumb"><img src="${layerThumb(o)}" alt=""></span>`}
      <div class="meta">
        <div class="nm" title="${esc(o.name)}（双击改名）">${esc(o.name)}</div>
        <div class="rg"><span class="sw" style="background:${o.color}"></span>${o.shape ? frIn(o.start, o.end) : T`<span title="出现的帧">${o.start}–${o.end}</span>`}
          ${isIdentity(o.tf) ? "" : T`<span class="flag" title="已移动 / 缩放 / 翻转">${IC("move")}</span>`}${o.alpha || o.shape ? "" : T`<span class="flag warn" title="没有柔边，只有 硬边">${IC("scan")}</span>`}</div>
      </div>
      ${!o.shape && !o.parent_id ? T`<button type="button" class="ic part-add" data-parent="${o.id}" title="添加主体扩绘区 / 主体抠图拆分" aria-label="添加">${IC("plus")}</button>` : ""}
      <span class="ic more" role="button" title="更多操作">${IC("more")}</span>
    </div>
    ${o.shape ? "" : extsOf(o).map((m) => extRow(o, m)).join("") + (o.keep_masks || []).map((m) => keepRow(o, m)).join("")}`).join("") : T('<div class="empty-note">还没有图层：在上面「识别」里输入主体名称</div>')) +
    (!S.d ? "" : T`<div class="layer bg ${S.bgVisible ? "" : "hidden"}"><span class="ic eye bgeye ${S.bgVisible ? "" : "off"}" role="button" title="显示 / 隐藏背景">${IC(S.bgVisible ? "eye" : "eye-off")}</span><span class="ic lock bglock" title="背景固定不动">${IC("lock")}</span>
      <span class="thumb"><img src="${S.d ? `/api/p/${S.pid}/bgthumb/${S.d.current}.png?n=${objs().length}&r=${rev()}` : ""}" alt=""></span>
      <div class="meta"><div class="nm" title="原视频里所有图层以外的部分">背景</div><div class="rg">原视频</div></div></div>`);
  $("#objBars").innerHTML = list.map((o) =>
    T`<div class="bar ${S.sel.has(o.id) ? "sel" : ""} ${o.shape ? "shapebar" : ""}" data-oid="${o.id}" title="${esc(o.name)}${uiLanguage === "en" ? `: frames ${o.start}–${o.end}` : `：第 ${o.start}–${o.end} 帧`}"><div class="seg" style="left:${fx(o.start)}%;width:${Math.max(fx(o.end) - fx(o.start), 0.5)}%;background:${o.color}"><span class="bar-nm">${esc(o.name)}</span>` +
    (o.shape ? T`<i class="bh" data-e="start" title="拖动：${shapeKind(o)}从第几帧开始"></i><i class="bh" data-e="end" title="拖动：${shapeKind(o)}到第几帧结束"></i>` : "") + `</div></div>` +
    (o.shape ? "" : extsOf(o).map((m) => { const s = m.start ?? 0, e = m.end ?? S.d.n - 1;      // 跟随蒙版的色条：两头可拖改帧范围
      return T`<div class="bar shapebar extbar ${extSel(o, m) ? "sel" : ""}" data-oid="${o.id}" data-ext="${m.id}" title="${esc(m.name || "")}：${FR(s, e)}"><div class="seg" style="left:${fx(s)}%;width:${Math.max(fx(e) - fx(s), 0.5)}%;background:${o.color};opacity:${m.off ? 0.3 : 0.65}"><span class="bar-nm">${esc(m.name || "")}</span>` +
        T('<i class="bh" data-e="start" title="拖动：跟随蒙版从第几帧开始"></i><i class="bh" data-e="end" title="拖动：跟随蒙版到第几帧结束"></i></div></div>'); }).join("") +
      (o.keep_masks || []).map((m) => { const s = m.start ?? 0, e = m.end ?? S.d.n - 1;      // 主体局部保留的色条
        return T`<div class="bar shapebar extbar ${keepSel(o, m) ? "sel" : ""}" data-oid="${o.id}" data-keep="${esc(m.id)}" title="${esc(m.name || "")}：${FR(s, e)}"><div class="seg" style="left:${fx(s)}%;width:${Math.max(fx(e) - fx(s), 0.5)}%;background:${o.color};opacity:${m.off ? 0.3 : 0.65}"><span class="bar-nm">${esc(m.name || "")}</span>` +
          T('<i class="bh" data-e="start" title="拖动：主体局部保留从第几帧开始"></i><i class="bh" data-e="end" title="拖动：主体局部保留到第几帧结束"></i></div></div>'); }).join(""))
  ).join("");
  renderProps();
}

$("#layerList").addEventListener("click", (e) => {
  if (e.target.closest(".part-add")) return;
  const row = e.target.closest(".layer");
  const eye = e.target.closest(".eye"), lock = e.target.closest(".lock"), more = e.target.closest(".more");
  if (row && eye && eye.classList.contains("bgeye")) { S.bgVisible = !S.bgVisible; renderLayers(); return draw(); }
  if (!row || row.classList.contains("bg") || e.target.tagName === "INPUT") return;
  if (e.detail === 2 && e.target.closest(".nm")) return startRename(row);   // 双击名字 = 改名（见 startRename）
  const o = obj(row.dataset.id);
  if (row.dataset.keep) {                                          // 主体局部保留行：选中主体；不在帧范围里就跳过去
    const kid = row.dataset.keep, m = (o.keep_masks || []).find((v) => v.id === kid);
    if (eye) return keepEdit(o, kid, (v) => { if (v.off) delete v.off; else v.off = true; return v; }, T("启用 / 停用主体局部保留"));
    if (e.target.closest(".keepdel")) return keepEdit(o, kid, () => null, T("删除主体局部保留"));
    (S.keepSel ||= {})[o.id] = kid;
    if (m && (S.i < (m.start ?? 0) || S.i > (m.end ?? S.d.n - 1))) S.i = m.start ?? 0;
    select(o.id);
    renderLayers(); return draw();
  }
  if (S.keepSel && !eye && !lock && !more) delete S.keepSel[o.id];   // 点主体本身 / 主体扩绘区：不再高亮主体局部保留行
  if (row.dataset.ext) {                                           // 跟随蒙版行：选中它的图层 + 让它成为正在编辑的那个；不在它的帧范围里就跳过去
    const mid = row.dataset.ext, m = extsOf(o).find((v) => v.id === mid);
    if (eye) return editExt(o, mid, { off: m.off ? null : true }, T("启用 / 停用主体扩绘区"));
    if (e.target.closest(".extdel")) return editExt(o, mid, null, T("删除主体扩绘区"));
    (S.extSel ||= {})[o.id] = mid;
    if (m && (S.i < (m.start ?? 0) || S.i > (m.end ?? S.d.n - 1))) S.i = m.start ?? 0;
    select(o.id);
    renderLayers(); return draw();
  }
  if (eye) return commit([o], toggleVis, o.shape ? T("启用 / 停用蒙版") : T("显示 / 隐藏"));
  if (lock) return commit([o], (x) => { x.locked = !x.locked; }, T("锁定"));
  if (more) return openLayerMenu(o, more);
  if (o.shape && (S.i < o.start || S.i > o.end)) S.i = o.start;   // 固定蒙版：不在它的帧范围里就跳过去（画面上画出它的框）
  select(o.id, e.shiftKey || e.ctrlKey);
});
// 双击名字改名。点一下会重绘图层列表，第二下落在新元素上、浏览器不一定再发 dblclick，
// 所以点击里的第二下（e.detail === 2）也走这里；按 id 找现在的那一行
function startRename(row) {
  const o = obj(row.dataset.id), mid = row.dataset.ext, kid = row.dataset.keep;
  if (!o) return;
  const cur = kid ? ((o.keep_masks || []).find((v) => v.id === kid) || {}).name || "" : mid ? (extsOf(o).find((v) => v.id === mid) || {}).name || "" : o.name;
  const live = $(`#layerList .layer[data-id="${o.id}"]` + (kid ? `[data-keep="${kid}"]` : mid ? `[data-ext="${mid}"]` : ":not([data-ext]):not([data-keep])") + " .nm");
  if (!live || live.querySelector("input")) return;
  live.innerHTML = `<input value="${esc(cur)}">`;
  const inp = live.querySelector("input");
  inp.focus(); inp.select();
  let fin = false;
  const done = (ok) => {
    if (fin) return; fin = true;
    if (!ok || !inp.value.trim() || inp.value === cur) return renderLayers();
    if (kid) keepEdit(o, kid, (v) => ({ ...v, name: inp.value.trim() }), T("主体局部保留改名"));
    else if (mid) editExt(o, mid, { name: inp.value.trim() }, T("主体扩绘区改名"));
    else commit([o], (x) => { x.name = inp.value.trim(); }, T("改名"));
  };
  inp.onkeydown = (ev) => { if (ev.key === "Enter") done(true); if (ev.key === "Escape") done(false); ev.stopPropagation(); };
  inp.onblur = () => done(true);
}
$("#layerList").addEventListener("dblclick", (e) => {
  const nm = e.target.closest(".nm"), row = e.target.closest(".layer");
  if (nm && row && !row.classList.contains("bg")) startRename(row);
});

// 拖动图层行来排序
let dragRow = null;
$("#layerList").addEventListener("dragstart", (e) => { dragRow = e.target.closest(".layer"); });
$("#layerList").addEventListener("dragover", (e) => {
  const row = e.target.closest(".layer");
  if (!dragRow || !row) return;
  e.preventDefault();
  $$(".layer.dragover").forEach((r) => r.classList.remove("dragover"));
  row.classList.add("dragover");
});
$("#layerList").addEventListener("drop", (e) => {
  e.preventDefault();
  const row = e.target.closest(".layer");
  $$(".layer.dragover").forEach((r) => r.classList.remove("dragover"));
  if (!dragRow || !row || row === dragRow || row.dataset.ext || dragRow.dataset.ext) return;   // 跟随蒙版行不参与排序
  const source = obj(dragRow.dataset.id), target = obj(row.dataset.id);
  if (source?.parent_id || target?.parent_id) {
    if (!source?.parent_id || source.parent_id !== target?.parent_id) return;
    const siblings = obj(source.parent_id).parts.slice().sort((a,b)=>(b.z||0)-(a.z||0)).map(o=>o.id).filter(id=>id!==source.id);
    siblings.splice(siblings.indexOf(target.id),0,source.id);
    setOrder(siblings,T("调整顺序")); dragRow=null; return;
  }
  const order = layersFrontToBack().filter(o=>!o.parent_id).map((o) => o.id).filter((id) => id !== dragRow.dataset.id);
  const at = row.classList.contains("bg") ? order.length : order.indexOf(row.dataset.id);
  order.splice(at, 0, dragRow.dataset.id);
  setOrder(order, T("调整顺序"));
  dragRow = null;
});

function setOrder(frontToBack, label) {
  const os = frontToBack.map(obj);
  commit(os, (o) => { o.z = frontToBack.length - 1 - frontToBack.indexOf(o.id); }, label);
}

function zOrder(kind) {
  const parents = new Set(selected().map(o => o.parent_id || null));
  if (parents.size !== 1) return setLog(T("请选择同一父图层下的图层调整顺序"));
  const parent = selected()[0]?.parent_id;
  const order = (parent ? obj(parent).parts.slice().sort((a,b)=>(b.z||0)-(a.z||0)) : layersFrontToBack().filter(o=>!o.parent_id)).map(o=>o.id);
  const sel = order.filter((id) => S.sel.has(id));
  if (!sel.length) return;
  let rest = order.filter((id) => !S.sel.has(id)), out;
  if (kind === "front") out = [...sel, ...rest];
  else if (kind === "back") out = [...rest, ...sel];
  else {
    out = order.slice();
    const idxs = sel.map((id) => out.indexOf(id));
    const step = kind === "up" ? -1 : 1;
    (step < 0 ? idxs : idxs.reverse()).forEach((i) => {
      const j = i + step;
      if (j < 0 || j >= out.length || S.sel.has(out[j])) return;
      [out[i], out[j]] = [out[j], out[i]];
    });
  }
  setOrder(out, { front: T("置顶"), back: T("置底"), up: T("上移一层"), down: T("下移一层") }[kind]);
}
$$(".layer-actions [data-z]").forEach((b) => (b.onclick = () => zOrder(b.dataset.z)));

$("#delLayerBtn").onclick = async () => {
  const os = selected();
  if (!os.length) return;
  if (!(await uiConfirm(T`删除 ${os.map((o) => o.name).join("、")}？将清理对应的遮罩、柔边、抠图修改和预览缓存；其他图层仍使用的文件会保留。原视频不受影响，不能撤销。`, { ok: T("删除"), danger: true }))) return;
  let d;
  for (const o of os.filter(o=>!o.parent_id || !S.sel.has(o.parent_id))) d = await api(`/api/p/${S.pid}/obj/${o.id}`, { method: "DELETE" });
  S.sel.clear();
  setProject(d, true);
};

const tfMoved = (t) => !!(t && (t.x || t.y || (t.sx ?? 1) !== 1 || (t.sy ?? 1) !== 1 || t.rot));
async function mergeLayers(os, main) {
  const en = uiLanguage === "en";
  const names = os.map((o) => o.name).join(en ? ", " : "、");
  const from = (main || os.find((o) => tfMoved(o.tf)) || { name: T("面积最大的那个") }).name;
  const msg = en ? `Merge ${names} into one layer? (This can't be undone. Position, size and edge settings come from "${from}".)`
    : `把 ${names} 合并成一个图层？（不能撤销；位置大小、边缘设置用「${from}」的）`;
  if (!(await uiConfirm(msg, { ok: T("合并") }))) return;
  try {
    const d = await api(`/api/p/${S.pid}/merge`, { json: { oids: os.map((o) => o.id), main: main?.id } });
    S.sel.clear();
    setProject(d, true);
    const no = os[0].parent_id ? obj(os[0].parent_id).parts.slice(-1)[0] : d.objects[d.objects.length - 1];
    select(no.id);
    setLog(T`已合并成「${no.name}」`);
  } catch (e) { setLog(e.message, true); }
}
$("#mergeLayerBtn").onclick = () => {
  const os = selected();
  if (os.length < 2) return setLog(T("先按住 Shift 或 Ctrl 选中两个以上的图层，再点合并"));
  const moved = os.filter((o) => tfMoved(o.tf));                   // 改过位置大小的：合并后跟着它走
  if (new Set(moved.map((o) => JSON.stringify(o.tf))).size > 1)    // 几个都改过、不一样：让用户选按哪个
    return openRefMenu($("#mergeLayerBtn"), [{ label: T("按哪个图层的位置大小合并？"), disabled: true },
      ...moved.map((o) => ({ label: o.name, run: () => mergeLayers(os, o) }))]);
  mergeLayers(os, null);
};

// ---------- 属性面板 ----------
// 首帧 / 尾帧标签里：蒙版与重绘区域的字段读写这张图自己的值（it.layer_opts，默认继承视频；images.set_layer_opts）
const ringText = (v) => (+v > 0 ? T`往外 ${+v} px` : +v < 0 ? T`往里 ${-v} px` : T("关"));
// 图层缩略图（图层行、蒙版 / 变换面板标题右边共用）
const layerThumb = (o) => `/api/p/${S.pid}/thumb/${o.id}.png?a=${o.alpha || ""}&e=${o.edge || "sam"}&g=${o.grow ?? 0}&r=${rev()}&m=${Object.values(o.medits || {}).reduce((a, b) => a + b, 0)}`;
const shapeKind = (o) => (o.prefill ? T("背景预填") : o.protect ? T("保护蒙版") : T("空区蒙版"));
const IMG_LAYER_FIELDS = ["edge", "grow", "box_ext", "ext_masks", "hole_rect", "box_redraw", "edge_fill", "hole_dilate", "hole_t", "ring_redraw"];
// 复制 / 粘贴 / 重置用的两组字段：蒙版（哪里重绘）和抠图（抠出来的样子）。主体扩绘区（ext_masks）跟着主体框，不复制
const MASK_FIELDS = ["hole_rect", "hole_dilate", "hole_t", "ring_redraw", "box_redraw", "edge_fill", "hole_prefill"];
const MASK_DEFAULT = { hole_rect: 3, hole_dilate: 0, hole_t: 0, ring_redraw: 3,   // 和新建主体的默认一样（project.SUBJECT_MASK_DEFAULTS）
  box_redraw: null, edge_fill: null, hole_prefill: null };
const MATTE_FIELDS = ["edge", "grow", "fill_holes", "despeckle", "smooth_t"];
const MATTE_DEFAULT = { edge: "sam", grow: 0, fill_holes: 0, despeckle: 0, smooth_t: 0 };
const imgEditing = () => { if (S.tab === "video" || typeof curImg !== "function") return null; const it = curImg(); return it && isStill(it) ? it : null; };
const effObj = (o) => { const it = imgEditing(); return it ? { ...o, ...((it.layer_opts || {})[o.id] || {}) } : o; };
async function imgLayerEdit(os, fn) {
  const it = imgEditing();
  for (const o of os) {
    const e = JSON.parse(JSON.stringify(effObj(o)));
    fn(e);
    const fields = {};
    IMG_LAYER_FIELDS.forEach((k) => { fields[k] = e[k] ?? null; });
    try { Object.assign(it, await api(`/api/p/${S.pid}/images/${it.id}/layer_opts`, { json: { oids: [o.id], fields } })); }
    catch (err) { return setLog(err.message, true); }
  }
  S.stillGen++;                                                    // 补图范围叠加层重新算
  renderProps();
  draw();
  setLog(T("只改了这张图（本图），视频不变；画面上的补图范围已更新"));
}
function editLayers(os, fn, label, before) { return imgEditing() ? imgLayerEdit(os, fn) : commit(os, fn, label, before); }
// 改一个主体扩绘区（patch = null 删掉）。还没存的那一个（S.extPending）：改完还是空的只改界面上的那份，
// 拉开了（四边有一边 > 0）才存进图层（图片编辑页存成这张图的单图覆盖）。
function editExt(o, mid, patch, label) {
  const P = S.extPending && S.extPending.oid === o.id && S.extPending.m.id === mid ? S.extPending : null;
  if (P) {
    if (patch === null) { S.extPending = null; return afterChange(); }
    const m = { ...P.m, ...patch };
    if (extEmpty(m)) { P.m = m; return afterChange(); }
    S.extPending = null;
    return editLayers([o], (x) => { x.ext_masks = [...extList(x).map((v) => ({ ...v })), m]; x.box_ext = null; }, label);
  }
  const cur = extList(o).find((v) => v.id === mid);
  if (patch && cur && EXT_SIDES.some((k) => k in patch) && extEmpty({ ...cur, ...patch })) {   // 四边都改回 0：这个扩绘区没用了，直接删掉
    patch = null; label = T("删除主体扩绘区");
    const nm = cur.name || T("主体扩绘区");
    setLog(uiLanguage === "en" ? `“${nm}” had all four sides at 0 and was removed (undo to restore)` : `「${nm}」四边都是 0，已删掉（可以撤销）`);
  }
  editLayers([o], (x) => {
    x.ext_masks = extList(x).map((v) => (v.id === mid ? (patch === null ? null : { ...v, ...patch }) : { ...v })).filter(Boolean);
    x.box_ext = null;
  }, label);
}
function addExt(o) {                                                // 「添加」：下面多出一条（先不存），填四边或拖画面上的橙色箭头才存
  if (!o || o.shape) return;
  if (S.sel.size !== 1 || !S.sel.has(o.id)) select(o.id);
  const P = S.extPending;
  if (!(P && P.oid === o.id)) {
    const m = newExt(extsOf(o));
    S.extPending = { oid: o.id, m, tab: S.tab };
  }
  (S.extSel ||= {})[o.id] = S.extPending.m.id;
  window.uiOpenGroup?.("mask");
  renderLayers(); draw();
  setLog(T`加了「${S.extPending.m.name}」：在下面填上 / 下 / 左 / 右 像素，或拖画面上主体框外的橙色箭头。什么都没填就自动取消`);
}
// 蒙版工具里的洞形状选项（默认都是 0 / 关，软件不会自己加）：[界面 id, 图层字段, 名字, 单位]
function renderProps() {
  if (pruneExt()) return renderLayers();                          // 离开了刚添加的空主体扩绘区：取消，重画列表（它会再调回这里）
  const os = selected();
  $("#mergeLayerBtn").disabled = os.length < 2;
  $("#propPanel").classList.toggle("disabled", os.length !== 1);
  $("#maskPanel").classList.toggle("disabled", os.length !== 1);
  $("#mattePanel").classList.toggle("disabled", os.length !== 1 || !!(os[0] && os[0].shape));   // 空区蒙版 / 背景预填不是抠出来的
  const o = os.length === 1 ? os[0] : null;
  $("#propName").textContent = o ? o.name : os.length > 1 ? T`已选 ${os.length} 个图层` : T("未选中");
  $("#maskName").textContent = $("#propName").textContent;
  $("#matteName").textContent = $("#propName").textContent;
  renderSubExtras(o);
  for (const id of ["#maskThumb", "#propThumb"]) {                  // 标题右边：正在编辑的主体的缩略图（蒙版图层没有画面，不显示）
    const im = $(id), show = !!o && !o.shape;
    im.hidden = !show;
    if (show) { const u = layerThumb(o); if (im.getAttribute("src") !== u) im.src = u; im.title = o.name; }
  }
  { const sh = !!(o && o.shape);                                   // 蒙版面板：主体选中 = 主体重绘范围 + 原位置蒙版；蒙版图层选中 = 手绘蒙版
    $("#msecSubj").hidden = sh; $("#shapeEdit").hidden = !sh;      // 手绘蒙版一直显示（新建按钮），选中蒙版图层时多出它的设置
    $("#shapeKindVal").textContent = sh ? shapeKind(o) : "";
    if (sh) {
      for (const [id, k, d] of [["#shapeStart", "start", 0], ["#shapeEnd", "end", S.d.n - 1]])
        if (document.activeElement !== $(id)) { $(id).max = S.d.n - 1; $(id).value = o[k] ?? d; }
      $("#shapeOn").checked = !o.off;
    } }
  const tf = o ? o.tf : { x: 0, y: 0, sx: 1, sy: 1, rot: 0 };
  const bw = o ? o.bbox[2] - o.bbox[0] : 0, bh = o ? o.bbox[3] - o.bbox[1] : 0;   // 图层整段外框（原视频像素）
  const vals = { x: tf.x, y: tf.y, w: Math.abs(tf.sx) * 100, h: Math.abs(tf.sy) * 100, rot: tf.rot,
                 wpx: bw * Math.abs(tf.sx), hpx: bh * Math.abs(tf.sy) };
  $$("#propPanel [data-p]").forEach((inp) => {
    if (document.activeElement === inp) return;
    inp.value = inp.dataset.p.endsWith("px") ? Math.round(vals[inp.dataset.p]) : Math.round(vals[inp.dataset.p] * 10) / 10;
  });
  if (typeof tfClip !== "undefined") $("#tfPaste").disabled = !tfClip || !os.length;
  $("#opacity").value = Math.round((o ? o.opacity : 1) * 100);
  const eo = o && effObj(o);
  { const o = eo;                                                  // 蒙版字段：图片标签里是这张图的值
  $("#edgeSel").value = o ? o.edge || "sam" : "sam";
  $("#growRange").value = o ? o.grow ?? 0 : 0;
  $("#growVal").textContent = (o ? o.grow ?? 0 : 0) + " px";
  const hr = o ? +(o.hole_rect || 0) : 0;                         // 空区方块：0 = 不改，>0 = 横切段数（旧的 true = 1 段）
  $("#holeRectRange").value = hr;
  $("#holeRectVal").textContent = rectText(hr);
  $("#boxRedraw").checked = !!(o && o.box_redraw);
  $("#edgeFill").checked = !!(o && o.edge_fill);
  for (const [rid, vid, f, fmt] of CLEAN_UI) { const v = o ? +(o[f] || 0) : 0; $(rid).value = v; $(vid).textContent = fmt(v); }
  $("#holePrefill").checked = !!(o && o.hole_prefill);
  { const hd = o ? +(o.hole_dilate || 0) : 0, ht = o ? +(o.hole_t || 0) : 0;
    $("#holeDilateRange").value = hd; $("#holeDilateVal").textContent = dilText(hd);
    $("#holeTRange").value = ht; $("#holeTVal").textContent = htText(ht);
    const rr = o ? +(o.ring_redraw || 0) : 0; $("#ringRange").value = rr; $("#ringVal").textContent = ringText(rr); }
  [...$("#edgeSel").options].forEach((op) => (op.disabled = op.value !== "sam" && !(o && o.alpha)));   // 没做过 MatAnyone 抠像只能用硬边
  }
  // 首帧 / 尾帧：这张图单独改过的项标「本图」，点一下还原成视频的值
  const ov = (o && imgEditing() && (imgEditing().layer_opts || {})[o.id]) || {};
  $$(":is(#maskPanel, #mattePanel) .mctl[data-f]").forEach((el) => {
    const on = Object.prototype.hasOwnProperty.call(ov, el.dataset.f);
    el.classList.toggle("ovr", on);
    let tag = el.querySelector(".ovrTag");
    if (!tag) {
      tag = document.createElement("button");
      tag.type = "button"; tag.className = "ovrTag"; tag.textContent = T("本图 · 还原");
      tag.title = T("这一项只在这张图里改过（视频不变）。点一下还原成视频的值");
      el.querySelector(".mhead").append(tag);
    }
  });
  $("#opacityVal").textContent = $("#opacity").value + "%";
  for (const kind of ["mask", "matte"]) {
    $(`#${kind}Paste`).disabled = !clipOf(kind) || !os.length;
    $(`#${kind}Copy`).disabled = $(`#${kind}Reset`).disabled = os.length !== 1 || !!os[0].shape;
  }
  loadMaskStats(o);
}
// 重绘范围：这个图层造成的空区在哪些帧、占原视频多少（服务端估算，改了设置 0.6 秒后重算）
let statsTimer = 0, statsSig = "";
function loadMaskStats(o) {
  const el = $("#maskStats");
  if (!o || o.shape || imgEditing()) { el.textContent = ""; statsSig = ""; return; }
  const sig = JSON.stringify([S.pid, o.id, rev(), o.tf, o.visible, [...MASK_FIELDS, ...MATTE_FIELDS].map((k) => o[k] ?? null), o.ext_masks || o.box_ext || null, o.keep_masks || null]);
  if (sig === statsSig) return;
  statsSig = sig;
  clearTimeout(statsTimer);
  el.textContent = T("重绘范围计算中…");
  statsTimer = setTimeout(async () => {
    try {
      const r = await api(`/api/p/${S.pid}/obj/${o.id}/stats`);
      if (statsSig !== sig) return;
      el.textContent = r.frames ? (uiLanguage === "en" ? `Redraw range: frames ${r.start}–${r.end} · up to ${r.max}% of the frame (avg ${r.mean}%) to redraw` : `重绘范围：第 ${r.start}–${r.end} 帧 · 最多 ${r.max}% 画面（平均 ${r.mean}%）要重绘`)
                                : T("重绘范围：这个图层没有造成要重绘的空区");
    } catch (err) { if (statsSig === sig) el.textContent = ""; }
  }, 600);
}
$$("#propPanel [data-p]").forEach((inp) => {
  let before = null, editingId = null;
  inp.oninput = () => {
    const o = selected()[0];
    if (!o || inp.value === "" || !Number.isFinite(inp.valueAsNumber)) return;
    if (!before || editingId !== o.id) { before = [snap(o)]; editingId = o.id; }
    applyValue(o);
    persist([snap(o)]);
    drawSoon();
  };
  inp.onchange = inp.onblur = () => {
    const o = selected()[0];
    if (o && before && editingId === o.id) commit([o], () => {}, T("属性"), before);
    before = null; editingId = null;
  };
  function applyValue(o) {
  const v = inp.valueAsNumber, p = inp.dataset.p;
    const t = o.tf, old = { ...t };
    if (p === "x" || p === "y" || p === "rot") t[p] = v;
    if (p === "w" || p === "h" || p === "wpx" || p === "hpx") {        // 宽 / 高：百分比，或者直接填像素（换算成百分比）
      const px = p.endsWith("px"), side = px ? (p === "wpx" ? o.bbox[2] - o.bbox[0] : o.bbox[3] - o.bbox[1]) : 0;
      const f = px ? Math.max(v, 1) / Math.max(side, 1) : Math.max(v, 1) / 100;
      const key = p[0] === "w" ? "sx" : "sy", other = p[0] === "w" ? "sy" : "sx";
      const ratio = Math.abs(t[other] / t[key]);
      t[key] = Math.sign(t[key] || 1) * f;
      if ($("#ratioLock").checked) t[other] = Math.sign(t[other] || 1) * f * ratio;
    }
    if (p !== "x" && p !== "y") anchorFix(o, old);                // 改大小 / 旋转：变换中心那个点不动
  }
});
// 拖标签调数值（PS / AE 那样）：在「X」「宽 %」这些字上按住左右拖；Shift 快 10 倍；松手记一步撤销
const SCRUB_STEP = { x: 1, y: 1, w: 0.5, h: 0.5, rot: 0.5, wpx: 1, hpx: 1 };
$$("#propPanel .scrub").forEach((lab) => {
  const inp = lab.parentElement.querySelector("input[data-p]");
  lab.addEventListener("click", (e) => e.preventDefault());       // 不让 <label> 自动把焦点给输入框（拖完要用快捷键）；没拖时下面自己 focus
  lab.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || !selected()[0]) return;
    e.preventDefault();
    if (document.activeElement && document.activeElement.matches("input, textarea")) document.activeElement.blur();   // 焦点离开输入框：拖完 Ctrl+Z 归软件撤销
    const x0 = e.clientX, v0 = +inp.value || 0, step = SCRUB_STEP[inp.dataset.p] || 1;
    let moved = false;
    lab.setPointerCapture(e.pointerId);
    const move = (ev) => {
      const dx = ev.clientX - x0;
      if (!moved && Math.abs(dx) < 2) return;
      moved = true;
      inp.value = Math.round((v0 + dx * step * (ev.shiftKey ? 10 : 1)) * 10) / 10;
      inp.dispatchEvent(new Event("input"));
    };
    const up = () => {
      lab.removeEventListener("pointermove", move); lab.removeEventListener("pointerup", up);
      if (moved) inp.dispatchEvent(new Event("change")); else inp.focus();
    };
    lab.addEventListener("pointermove", move); lab.addEventListener("pointerup", up);
  });
});
// 变换中心九宫格
function renderAnchor() { $$("#tfAnchor [data-a]").forEach((b) => b.classList.toggle("on", +b.dataset.a === anchorIdx())); }
$("#tfAnchor").addEventListener("click", (e) => {
  const b = e.target.closest("[data-a]");
  if (!b) return;
  try { localStorage.setItem("vps_anchor", b.dataset.a); } catch {}
  renderAnchor();
  setLog(T("变换中心：") + [T("左上"), T("上中"), T("右上"), T("左中"), T("中心"), T("右中"), T("左下"), T("下中"), T("右下")][+b.dataset.a] + T("（改大小 / 旋转时这个点不动）"));
});
renderAnchor();
// 对齐到画幅：按图层整段外框（变换后）贴边 / 居中；多选时各自对齐
function alignLayers(kind) {
  const os = selected().filter((o) => !o.locked);
  if (!os.length) return setLog(T("先选中图层（锁定的不动）"), true);
  const fr = getFrame();
  commit(os, (o) => {                                              // 变换不能单图覆盖：一律改视频的
    const b = o.bbox, pts = [[b[0], b[1]], [b[2], b[1]], [b[0], b[3]], [b[2], b[3]]].map((p) => apply(o, p));
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const d = [kind === "l" ? fr.x - x0 : kind === "r" ? fr.x + fr.w - x1 : kind === "hc" ? fr.x + fr.w / 2 - (x0 + x1) / 2 : 0,
               kind === "t" ? fr.y - y0 : kind === "b" ? fr.y + fr.h - y1 : kind === "vc" ? fr.y + fr.h / 2 - (y0 + y1) / 2 : 0];
    const [dx, dy] = ownDelta(o, d);
    o.tf.x += dx; o.tf.y += dy;
  }, T("对齐到画幅"));
}
$("#propPanel").addEventListener("click", (e) => { const b = e.target.closest("[data-al]"); if (b) alignLayers(b.dataset.al); });
// 复制 / 粘贴变换：大小、旋转、翻转（位置是相对各自图层的偏移，不搬）
var tfClip = null;
try { tfClip = JSON.parse(localStorage.getItem("vps_tfclip") || "null"); } catch {}
$("#tfCopy").onclick = () => {
  const o = selected()[0];
  if (!o) return setLog(T("先选中一个图层"), true);
  tfClip = { sx: o.tf.sx, sy: o.tf.sy, rot: o.tf.rot };
  try { localStorage.setItem("vps_tfclip", JSON.stringify(tfClip)); } catch {}
  renderProps();
  setLog(T`复制了「${o.name}」的变换：宽 ${Math.round(Math.abs(tfClip.sx) * 100)}% · 高 ${Math.round(Math.abs(tfClip.sy) * 100)}% · 旋转 ${tfClip.rot}°${tfClip.sx < 0 ? T(" · 水平翻转") : ""}${tfClip.sy < 0 ? T(" · 垂直翻转") : ""}`);
};
$("#tfPaste").onclick = () => {
  const os = selected().filter((o) => !o.locked);
  if (!tfClip || !os.length) return;
  commit(os, (o) => { const old = { ...o.tf }; Object.assign(o.tf, tfClip); anchorFix(o, old); }, T("粘贴变换"));
};
// 变换面板里的翻转 / 还原：和左边工具栏、浮窗里的是同一个功能
$("#propPanel").addEventListener("click", (e) => {
  const b = e.target.closest("[data-tf]");
  const btn = b && { flipH: "#flipHBtn", flipV: "#flipVBtn", reset: "#resetBtn" }[b.dataset.tf];
  if (btn) $(btn).click();
});
$("#opacity").oninput = () => {
  const o = selected()[0];
  if (!o) return;
  o.opacity = $("#opacity").value / 100;
  $("#opacityVal").textContent = $("#opacity").value + "%";
  drawLayers();
};
$("#opacity").onchange = () => {
  const o = selected()[0];
  if (!o) return;
  const v = $("#opacity").value / 100;
  o.opacity = S.dragBefore?.opacity ?? o.opacity;
  commit([o], (x) => { x.opacity = v; }, T("不透明度"));
};
// 滑杆：拖的时候数值、画面预览立刻变，停下 0.2 秒就存（服务端重算重绘范围，叠加层跟着变）；松手记一步撤销
const dilText = (v) => (+v ? (v > 0 ? "+" : "") + v + " px" : T("关"));
const htText = (v) => (+v ? "±" + v + (uiLanguage === "en" ? " frames" : " 帧") : T("关"));
const rectText = (v) => (v ? T("矩形 ×") + v : T("轮廓"));               // 空区形状：一个滑杆，0 = 轮廓（照原样）
const CLEAN_UI = [["#fillHolesRange", "#fillHolesVal", "fill_holes", (v) => (v ? v + " px" : T("关"))],
                  ["#despeckleRange", "#despeckleVal", "despeckle", (v) => (v ? v + " %" : T("关"))],
                  ["#smoothTRange", "#smoothTVal", "smooth_t", (v) => (v ? "±" + v + T(" 帧") : T("关"))]];
function bindSlider(id, valId, field, label, unit = " px", fmt = (v) => v + unit, videoOnly = false) {
  const el = $(id);
  let before = null, timer = 0;
  el.oninput = () => {
    const os = selected(), v = +el.value;
    if (!os.length) return;
    if (imgEditing() && !videoOnly) { $(valId).textContent = fmt(v); clearTimeout(timer); timer = setTimeout(() => imgLayerEdit(os, (o) => { o[field] = v; }), 250); return; }
    if (!before) before = os.map(snap);
    $(valId).textContent = fmt(v);
    os.forEach((o) => { o[field] = v; });
    clearTimeout(timer);
    persist(os.map(snap));
    drawSoon();
  };
  el.onchange = () => {
    const os = selected();
    if (imgEditing() && !videoOnly) return;
    clearTimeout(timer);
    if (os.length && before) commit(os, (o) => { o[field] = +el.value; }, label, before);
    before = null;
  };
}
bindSlider("#growRange", "#growVal", "grow", T("收缩 / 扩展"));
bindSlider("#holeRectRange", "#holeRectVal", "hole_rect", T("空区形状"), "", rectText);
bindSlider("#holeDilateRange", "#holeDilateVal", "hole_dilate", T("空区扩展 / 收缩"), "", dilText);
bindSlider("#holeTRange", "#holeTVal", "hole_t", T("空区帧间合并"), "", htText);
bindSlider("#ringRange", "#ringVal", "ring_redraw", T("主体轮廓重绘"), "", ringText);
for (const [rid, vid, f, fmt] of CLEAN_UI) bindSlider(rid, vid, f, { fill_holes: T("填充孔洞"), despeckle: T("去除杂点"), smooth_t: T("帧间平滑") }[f], "", fmt, true);   // 清理改的是蒙版本身：图片里也直接改视频
$("#boxRedraw").onchange = () => {
  const os = selected(), on = $("#boxRedraw").checked;
  if (os.length) editLayers(os, (o) => { o.box_redraw = on || null; }, on ? T("边界框内重绘：开") : T("边界框内重绘：关"));
};
$("#edgeFill").onchange = () => {
  const os = selected(), on = $("#edgeFill").checked;
  if (os.length) editLayers(os, (o) => { o.edge_fill = on || null; }, on ? T("补全被裁切部分：开") : T("补全被裁切部分：关"));
};
$("#holePrefill").onchange = () => {
  const os = selected(), on = $("#holePrefill").checked;
  if (os.length) commit(os, (o) => { o.hole_prefill = on || null; }, on ? T("空区预填背景：开") : T("空区预填背景：关"));
  if (on && !S.d.panorama) setLog(T("还没有背景图：先在图片编辑 → 背景图里提取并生成，否则生成时照旧涂灰"), true);
};
// 复制 / 粘贴 / 重置：蒙版、抠图各一套（记在浏览器，跨项目也能粘）
const CLIPS = {
  mask: { fields: MASK_FIELDS, def: MASK_DEFAULT, key: "vps_maskclip", zh: "蒙版设置", en: "mask settings", paste: "粘贴蒙版设置", reset: "重置蒙版设置" },
  matte: { fields: MATTE_FIELDS, def: MATTE_DEFAULT, key: "vps_matteclip", zh: "抠图设置", en: "matte settings", paste: "粘贴抠图设置", reset: "重置抠图设置" },
};
const clipOf = (kind) => { try { const v = JSON.parse(localStorage.getItem(CLIPS[kind].key) || "null"); return v && CLIPS[kind].fields.every((k) => k in v) ? v : null; } catch { return null; } };
for (const kind of ["mask", "matte"]) {
  const C = CLIPS[kind];
  $(`#${kind}Copy`).onclick = () => {
    const o = selected()[0];
    if (!o) return;
    try { localStorage.setItem(C.key, JSON.stringify(Object.fromEntries(C.fields.map((k) => [k, o[k] ?? C.def[k]])))); } catch {}
    renderProps();
    setLog(uiLanguage === "en" ? `Copied ${C.en} of "${o.name}"` : `复制了「${o.name}」的${C.zh}`);
  };
  $(`#${kind}Paste`).onclick = () => {
    const os = selected().filter((o) => !o.shape), v = clipOf(kind);
    if (!v || !os.length) return;
    editLayers(os, (o) => { for (const k of C.fields) o[k] = k === "edge" && v.edge !== "sam" && !o.alpha ? "sam" : v[k]; }, T(C.paste));
  };
  $(`#${kind}Reset`).onclick = () => {
    const os = selected().filter((o) => !o.shape);
    if (os.length) editLayers(os, (o) => { for (const k of C.fields) o[k] = C.def[k]; }, T(C.reset));
  };
}
$("#maskView").onclick = () => toggleMaskView();
$("#matteRefine").onclick = () => {
  const os = selected().filter((o) => !o.shape);
  if (os.length !== 1 || imgEditing()) return setLog(T("逐帧修整只能对一个图层用（图片编辑页也不行）"), true);
  startRefine(os[0].id);
};
// 蒙版面板「主体扩绘区」列表：每个一条（启用、名字、删除 / 上下左右像素 / 帧范围、填充间隙；正在编辑的那条多出拉开那几边的范围）
const EXT_SIDE_NAME = { t: "上", b: "下", l: "左", r: "右" };
function renderExtList(o) {
  const box = $("#extList"), subj = !!o && !o.shape;
  $("#extNew").disabled = !subj;
  const ae = document.activeElement;                               // 正在数字框里输入：不重画（不然输入框会丢焦点）；按钮 / 勾选框有焦点照常重画（删除后行要消失）
  if (box.contains(ae) && ae.matches('input[type="number"]')) return;
  const L = subj ? extsOf(o) : [], cur = subj ? curExt(o) : null, n = S.d ? S.d.n : 1;
  const num = (k, v, min, max, tip) => `<input type="number" data-k="${k}" min="${min}" max="${max}" value="${v}" title="${esc(tip)}">`;
  box.innerHTML = L.map((m) => {
    const isCur = cur && cur.id === m.id, empty = extEmpty(m);
    const spans = isCur ? EXT_SIDES.filter((s) => m[s] > 0).map((s) => `<div class="ei-row ei-span">` +
      T`<span class="ei-l">${T(EXT_SIDE_NAME[s])}边范围</span>` + num(s + "0", Math.round((m[s + "0"] ?? 0) * 100), 0, 95, T("从外框的百分之几开始")) + " – " +
      num(s + "1", Math.round((m[s + "1"] ?? 1) * 100), 5, 100, T("到外框的百分之几")) + " %</div>").join("") : "";
    return `<div class="ext-item${isCur ? " cur" : ""}${m.off ? " off" : ""}" data-mid="${esc(m.id)}">` +
      `<div class="ei-row ei-head">` + T`<input type="checkbox" class="e-on" ${m.off ? "" : "checked"} title="启用（关掉 = 当它不存在）">` +
      `<span class="sl-n" title="${esc(m.name || "")}">${esc(m.name || T("主体扩绘区"))}</span>` +
      (empty ? T`<span class="ei-hint">还没拉开</span>` : "") +
      T`<button type="button" class="ic e-del" title="删除这个主体扩绘区">${IC("x")}</button></div>` +
      `<div class="ei-row ei-sides">` + EXT_SIDES.map((s) => `<label>${T(EXT_SIDE_NAME[s])} <small class="ei-u">px</small>` + num(s, m[s] || 0, 0, 4000, T(`往${EXT_SIDE_NAME[s]}拉出多少像素（原视频像素）`)) + "</label>").join("") + `</div>` +
      `<div class="ei-row ei-frames">` + T`<span class="ei-l">帧</span>` + num("start", m.start ?? 0, 0, n - 1, T("起始帧")) + " – " + num("end", m.end ?? n - 1, 0, n - 1, T("结束帧")) +
      T`<label class="ei-cont" title="填充间隙：主体轮廓和拉开的边之间的空隙也重绘（这一个主体扩绘区自己的设置）"><input type="checkbox" class="e-cont" ${m.cont !== false ? "checked" : ""}> 填充间隙</label></div>` +
      spans + `</div>`;
  }).join("");
}
$("#extNew").onclick = (e) => {
  e.preventDefault();
  const os = selected();
  if (os.length !== 1) return setLog(T("先只选中一个图层"), true);
  addExt(os[0]);
};
$("#extList").addEventListener("click", (e) => {
  const it = e.target.closest("[data-mid]"), o = selected()[0];
  if (!it || !o) return;
  const mid = it.dataset.mid;
  if (e.target.closest(".e-del")) { e.target.closest(".e-del").blur(); return editExt(o, mid, null, T("删除主体扩绘区")); }
  if (e.target.closest("input, label")) return;
  if ((S.extSel ||= {})[o.id] === mid) return;                    // 点一条 = 让它成为正在编辑的那个（画面上的橙色箭头改它）；不在帧范围里就跳过去
  S.extSel[o.id] = mid;
  const m = extsOf(o).find((v) => v.id === mid);
  if (m && (S.i < (m.start ?? 0) || S.i > (m.end ?? S.d.n - 1))) S.i = m.start ?? 0;
  renderLayers(); draw();
});
$("#extList").addEventListener("change", (e) => {
  const it = e.target.closest("[data-mid]"), o = selected()[0];
  if (!it || !o) return;
  const mid = it.dataset.mid, m = extsOf(o).find((v) => v.id === mid);
  if (!m) return;
  (S.extSel ||= {})[o.id] = mid;
  const t = e.target, k = t.dataset.k, n = S.d.n;
  e.target.blur();                                                 // 让列表能重画
  if (t.matches(".e-on")) return editExt(o, mid, { off: t.checked ? null : true }, T("启用 / 停用主体扩绘区"));
  if (t.matches(".e-cont")) return editExt(o, mid, { cont: t.checked }, T("主体扩绘区：填充间隙"));
  if (EXT_SIDES.includes(k)) return editExt(o, mid, { [k]: Math.max(0, Math.round(+t.value || 0)) }, T`主体扩绘区：${T(EXT_SIDE_NAME[k])}`);
  if (k === "start" || k === "end") {
    const cl = (v) => Math.max(0, Math.min(n - 1, Math.round(+v || 0)));
    let a = cl(it.querySelector('[data-k="start"]').value), b = cl(it.querySelector('[data-k="end"]').value);
    if (a > b) { if (k === "start") b = a; else a = b; }
    return editExt(o, mid, { start: a, end: b }, T("主体扩绘区帧范围"));
  }
  if (/^[tblr][01]$/.test(k)) {                                    // 拉开那一边的范围（外框的 %），两头至少留 5%
    const s = k[0];
    let a = Math.min(Math.max(+it.querySelector(`[data-k="${s}0"]`).value || 0, 0), 95) / 100;
    let b = Math.min(Math.max(+it.querySelector(`[data-k="${s}1"]`).value || 0, 5), 100) / 100;
    if (b - a < 0.05) { if (k.endsWith("0")) a = Math.max(0, b - 0.05); else b = Math.min(1, a + 0.05); }
    return editExt(o, mid, { [s + "0"]: a, [s + "1"]: b }, T`主体扩绘区：${T(EXT_SIDE_NAME[s])}边范围`);
  }
});
$("#extList").addEventListener("keydown", (e) => { if (e.target.matches("input")) { e.stopPropagation(); if (e.key === "Enter") e.target.blur(); } });
$("#edgeSel").onchange = () => {
  const os = selected();
  if (os.length) editLayers(os, (o) => { o.edge = $("#edgeSel").value; }, T("边缘"));
};
$("#opacity").onpointerdown = () => { const o = selected()[0]; if (o) S.dragBefore = { opacity: o.opacity }; };

// ---------- 撤销 / 重做 ----------
const snap = (o) => JSON.parse(JSON.stringify({ id: o.id, name: o.name, tf: o.tf, z: o.z, opacity: o.opacity, visible: o.visible, locked: o.locked, edge: o.edge || "sam", grow: o.grow ?? 0, box_ext: o.box_ext || null, ext_masks: o.ext_masks || null, keep_masks: o.keep_masks || null,
  hole_rect: +(o.hole_rect || 0), hole_dilate: +(o.hole_dilate || 0), hole_t: +(o.hole_t || 0), ring_redraw: +(o.ring_redraw || 0), box_redraw: o.box_redraw || null, edge_fill: o.edge_fill || null, off: o.off || null,
  fill_holes: +(o.fill_holes || 0), despeckle: +(o.despeckle || 0), smooth_t: +(o.smooth_t || 0), hole_prefill: o.hole_prefill || null }));
function commit(os, fn, label, before = null) {   // 改图层：记录改之前的状态 → 改 → 存到服务器
  before = before || os.map(snap);
  os.forEach(fn);
  const after = os.map(snap);
  if (JSON.stringify(before) === JSON.stringify(after)) return;
  S.undo.push({ before, after, label });
  S.redo = [];
  persist(after, label);
  afterChange();
}
function restore(states, label) {
  for (const s of states) { const o = obj(s.id); if (o) Object.assign(o, JSON.parse(JSON.stringify(s))); }
  persist(states, label);
  afterChange();
}
function undo() { const e = S.undo.pop(); if (!e) return; S.redo.push(e); e.frame ? applyFrame(e.before) : restore(e.before, "撤销：" + e.label); setLog(T("撤销：") + e.label); }
function redo() { const e = S.redo.pop(); if (!e) return; S.undo.push(e); e.frame ? applyFrame(e.after) : restore(e.after, "重做：" + e.label); setLog(T("重做：") + e.label); }
// 每个批次固定项目 ID；单条写入队列防止旧请求晚到覆盖新参数。
const layerSaves = new Map();
let saveRunning = null, saveTimer = null;
function persist(states, label) {                                   // label：这一步叫什么（写进项目的操作记录）
  const pid = S.pid;
  let q = layerSaves.get(pid);
  if (!q) { q = { pending: {}, revision: 0, saved: 0, error: null, labels: [] }; layerSaves.set(pid, q); }
  if (label && !q.labels.includes(label)) q.labels.push(label);
  states.forEach(s => q.pending[s.id] = JSON.parse(JSON.stringify(s)));
  q.revision++; q.error = null;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushLayerSaves, 80);
}
function flushLayerSaves() {
  if (saveRunning) return saveRunning;
  saveRunning = (async () => {
    for (;;) {
      const entry = [...layerSaves].find(([, q]) => Object.keys(q.pending).length && !q.error);
      if (!entry) break;
      const [pid, q] = entry, revision = q.revision, batch = q.pending, labels = q.labels || [];
      q.pending = {}; q.labels = [];
      try {
        await api(`/api/p/${pid}/layers`, { json: { layers: Object.values(batch), label: labels.join("、") } });
        q.saved = revision;
        if (pid === S.pid && q.revision === revision) {
          S.zoneGen = (S.zoneGen || 0) + 1;
          zoneDecoded.clear();
          zoneImgs.clear(); zoneRetry.clear();
          drawSoon();
        }
      } catch (e) {
        q.pending = { ...batch, ...q.pending };
        q.error = e.message;
        if (pid === S.pid) { setLog(T("保存失败：") + e.message, true); drawSoon(); }
      }
    }
  })().finally(() => { saveRunning = null; });
  return saveRunning;
}
function layerPreviewPending() {
  const q = layerSaves.get(S.pid);
  return q && q.saved !== q.revision ? (q.error ? T("保存失败，预览尚未同步") : T("正在更新预览…")) : "";
}
function afterChange() {
  $("#undoBtn").disabled = !S.undo.length;
  $("#redoBtn").disabled = !S.redo.length;
  renderLayers();
  draw();
}
$("#undoBtn").onclick = undo;
$("#redoBtn").onclick = redo;

// ---------- 选中 ----------
function select(id, add = false) {
  if (S.refine && id !== S.refine.oid) {                           // 逐帧修整中换了图层：和切组一样，有改动先存再退出
    if (S.refine.dirty) { saveRefine().then((ok) => ok && (endRefine(), select(id, add))); return; }   // 保存失败：留在修整里
    endRefine();
  }
  if (!add) S.sel.clear();
  if (id) { if (add && S.sel.has(id)) S.sel.delete(id); else S.sel.add(id); }
  renderLayers();
  draw();
}

const tfButtons = {
  flipHBtn: [T("水平翻转"), (o) => { o.tf.sx = -o.tf.sx; }],
  flipVBtn: [T("垂直翻转"), (o) => { o.tf.sy = -o.tf.sy; }],
  resetBtn: [T("还原变换"), (o) => { o.tf = { x: 0, y: 0, sx: 1, sy: 1, rot: 0 }; }],
};
for (const [id, [label, fn]] of Object.entries(tfButtons)) {
  $("#" + id).onclick = () => {
    const os = selected().filter((o) => !o.locked);
    if (os.length) commit(os, fn, label);
  };
}
$("#tintBtn").onclick = () => { S.tint = !S.tint; $("#tintBtn").classList.toggle("on", S.tint); draw(); };
$("#zoneBtn").onclick = () => toggleZones();
$("#helpBtn").onclick = $("#toolKeys").onclick = () => ($("#help").hidden = false);   // 顶栏 ? 和左边栏最下面的键盘图标都打开快捷键列表

// ---------- 云端 API：图片生成 / 视频生成面板切到 API 时用；平台由用户按接口格式自己配（key 存在服务端本机文件）----------
const FMT_NAME = { openai_edits: "OpenAI 改图", json_sync: "JSON 同步", async_task: "异步任务" };
let provProfiles = [], provTemplates = {}, provFromTpl = false;
const provProf = (id) => provProfiles.find((x) => x.id === id);
function fillProviders(c) {
  provProfiles = c.profiles || [];
  provTemplates = c.templates || {};
  provFromTpl = false;
  $("#provTpl").value = "";
  $("#provKind").innerHTML = provProfiles.length ? provProfiles.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("")
    : `<option value="">${T("（还没有平台：在下面「接口配置」里从模板新建）")}</option>`;
  $("#provKind").value = c.provider || "";
  showProfile(c.provider || "");
  if (!provProfiles.length) $("#provAdv").open = true;
  provKindUi();
  $("#provBase").value = c.base_url; $("#provImg").value = c.image_model;
  $("#provSize").value = c.image_size;
  $("#provKeyIn").value = "";
  $("#provKeyIn").placeholder = c.key_set ? T("已保存，留空就不改") : T("粘贴这个平台的 API key");
  $("#provKey").innerHTML = c.key_set
    ? `<span class="ok">${T("已保存")}${c.key_tail ? (uiLanguage === "en" ? ` (ends in ${esc(c.key_tail)})` : `（尾号 ${esc(c.key_tail)}）`) : ""}</span>`
    : `<span class="bad">${T("还没填 API key")}</span>`;
  $("#provKeyClear").hidden = !c.key_set;
}
function showProfile(id) {                                       // 「接口配置」：这个平台的配置 JSON（可以改了再保存、删除）
  const x = provProf(id);
  $("#provProfileDel").hidden = !x;
  if (!x) { $("#provProfile").value = ""; $("#provFmt").textContent = ""; return; }
  const { name, ...pf } = x.profile;
  $("#provProfile").value = JSON.stringify(pf, null, 2);
  $("#provFmt").textContent = "· " + T(FMT_NAME[x.format] || x.format);
}
$("#provTpl").onchange = () => {                                 // 从模板新建：空白配置填进框里，保存时一定新建
  const t = provTemplates[$("#provTpl").value];
  if (!t) return;
  $("#provProfile").value = JSON.stringify(t, null, 2);
  $("#provFmt").textContent = "· " + T(FMT_NAME[t.format] || t.format);
  provFromTpl = true;
};
function provKindUi() {                                            // 只有 OpenAI 改图格式能把尺寸交给平台（auto）
  const x = provProf($("#provKind").value), openai = !x || x.format === "openai_edits";
  $("#provSizeAuto").hidden = !openai;
  if (!openai) $("#provSize").value = "match";
}
$("#provKind").onchange = () => {
  const d = (provProf($("#provKind").value) || {}).profile || {};
  $("#provBase").value = d.base_url || ""; $("#provImg").value = d.image_model || "";
  showProfile($("#provKind").value);
  $("#provKeyIn").value = ""; $("#provKey").textContent = T("换了平台：保存后显示这个平台的 key 有没有填"); $("#provKeyClear").hidden = true;
  provKindUi();
};
async function loadProviders() { try { fillProviders(await api("/api/providers")); } catch (e) { setLog(e.message, true); } }
$("#provBtn").onclick = async () => {
  $("#provView").hidden = false;
  window.refreshSettings?.();
  $("#provMsg").textContent = ""; await loadProviders();
  try { $("#prefPreview").value = String((await api("/api/preview")).w); } catch {}
  try { $("#prefKeep").value = String((await api("/api/keep_projects")).n); } catch {}
  try { $("#prefWork").value = String((await api("/api/work_long")).v); } catch {}
  try { $("#prefSeqPreview").value = String((await api("/api/seq_preview")).long); } catch {}
  try { $("#prefActLog").checked = (await api("/api/action_log")).on; } catch {}
  try { $("#prefCleanUp").checked = (await api("/api/clean_uploads")).on; } catch {}
  await fillCacheInfo();
  $("#provView").hidden = false;
};
$("#prefSeqPreview").onchange = async () => {                      // 分段页整条预览的长边：下次打开分段页时重做
  try {
    const r = await api("/api/seq_preview", { json: { long: +$("#prefSeqPreview").value } });
    setLog(uiLanguage === "en" ? `Segment preview set to long side ${r.long}; it is rebuilt the next time you open the segment page` : `分段预览改成长边 ${r.long}：下次打开分段页时重做预览`);
  } catch (err) { setLog(err.message, true); }
};
$("#prefPreview").onchange = async () => {                         // 画布预览清晰度：改了马上生效，重新载入当前项目
  try {
    const r = await api("/api/preview", { json: { w: +$("#prefPreview").value } });
    S.pw = r.w;
    if (S.pid) {
      S.cache = {}; for (const k of Object.keys(ghostCache)) delete ghostCache[k];
      setProject(await api(`/api/p/${S.pid}`), true); draw();
    }
    setLog(T`画布预览改成 ${r.w === 1920 ? T("原尺寸") : r.w + T(" 宽")}（第一次打开每个视频要重做预览帧，稍等）`);
  } catch (err) { setLog(err.message, true); }
};
$("#prefActLog").onchange = async () => {                          // 记录操作（项目文件夹的 操作记录log.jsonl）
  try {
    const r = await api("/api/action_log", { json: { on: $("#prefActLog").checked } });
    setLog(r.on ? T("之后的操作会记进项目文件夹的 操作记录log.jsonl") : T("已关掉操作记录（之前记的还在）"));
  } catch (err) { setLog(err.message, true); }
};
$("#prefCleanUp").onchange = async () => {                        // 任务结束后删除上传到 ComfyUI 的素材（input/reframe）
  try {
    const r = await api("/api/clean_uploads", { json: { on: $("#prefCleanUp").checked } });
    setLog(r.on ? T("之后每个任务结束就删除上传到 ComfyUI 的素材") : T("上传到 ComfyUI 的素材会留在 input/reframe 里"));
  } catch (err) { setLog(err.message, true); }
};
$("#prefWork").onchange = async () => {                            // 导入视频的工作分辨率（只影响之后新建的项目）
  try {
    const r = await api("/api/work_long", { json: { v: +$("#prefWork").value } });
    setLog(r.v ? T`之后导入的视频：长边超过 ${r.v} 就先缩到 ${r.v}` : T("之后导入的视频：按原尺寸处理（不缩）"));
  } catch (err) { setLog(err.message, true); }
};
$("#prefKeep").onchange = async () => {                            // 后台的项目标签：切走就放掉 / 最近 2 个常驻（前端帧缓存 + 后端遮罩缓存）
  try {
    const r = await api("/api/keep_projects", { json: { n: +$("#prefKeep").value } });
    try { localStorage.setItem("vps_keep_projects", String(r.n)); } catch {}
    if (r.n === 1) projStash.clear();
    setLog(r.n === 2 ? T("后台项目标签：最近 2 个常驻（来回切更快，多占内存）") : T("后台项目标签：切走就放掉（省内存）"));
  } catch (err) { setLog(err.message, true); }
};
$("#provCancel").onclick = () => ($("#provView").hidden = true);
function fmtSize(n) {                                     // 缓存大小：字节 → GB / MB
  if (!n) return "0 MB";
  if (n >= 1 << 30) return (n / (1 << 30)).toFixed(1) + " GB";
  return Math.max(1, Math.round(n / (1 << 20))) + " MB";
}
async function fillCacheInfo() {
  const el = $("#cacheInfo");
  if (!S.pid) { el.textContent = T("先打开一个项目"); return; }
  el.textContent = T("正在统计…");
  try { el.textContent = T("缓存占用") + " " + fmtSize((await api(`/api/p/${S.pid}/cache`)).size); }
  catch (e) { el.textContent = e.message; }
}
$("#cacheClear").onclick = async () => {                  // 只清缓存：不影响项目、图层和生成结果，下次打开重算
  if (!S.pid) { setLog(T("先打开一个项目"), true); return; }
  try {
    const r = await api(`/api/p/${S.pid}/cache/clear`, { method: "POST" });
    setLog(T("已清理缓存") + " " + fmtSize(r.freed));
    await fillCacheInfo();
  } catch (e) { setLog(e.message, true); }
};
$("#provView").addEventListener("pointerdown", (e) => { if (e.target.id === "provView") $("#provView").hidden = true; });
$("#provSave").onclick = async () => {
  try {
    fillProviders(await api("/api/providers", { json: {
      provider: $("#provKind").value, base_url: $("#provBase").value,
      image_model: $("#provImg").value, image_size: $("#provSize").value, key: $("#provKeyIn").value } }));
    $("#provMsg").innerHTML = T('<span class="ok">已保存，下一次 API 生成就按这个走</span>');
  } catch (e) { $("#provMsg").innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
};
$("#provProfileSave").onclick = async () => {                   // 保存为平台（从模板来的 = 新建；否则问覆盖当前平台还是另存）
  let pf;
  try { pf = JSON.parse($("#provProfile").value); } catch (e) { return ($("#provMsg").innerHTML = `<span class="bad">${T("接口格式不是合法的 JSON：")}${esc(e.message)}</span>`); }
  if (!provFromTpl) pf = { ...pf, base_url: $("#provBase").value.trim() || pf.base_url, image_model: $("#provImg").value.trim() || pf.image_model };
  const cur = provFromTpl ? null : provProf($("#provKind").value);
  const update = cur ? await uiConfirm(T`覆盖自己的平台「${cur.name}」，还是另存一个新的？`, { ok: T("覆盖"), no: T("另存为新平台") }) : false;
  if (update === null) return;
  const name = await uiPrompt(T("平台名字"), update ? cur.name : "");
  if (!name || !name.trim()) return;
  try {
    fillProviders(await api("/api/providers/profile", { json: { id: update ? cur.id : null, name: name.trim(), profile: pf } }));
    $("#provMsg").innerHTML = `<span class="ok">${T("已保存平台：")}${esc(name.trim())}</span>`;
  } catch (e) { $("#provMsg").innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
};
$("#provProfileDel").onclick = async () => {
  const cur = provProf($("#provKind").value);
  if (!cur || !(await uiConfirm(T`删除平台「${cur.name}」？它的 API key 也会一起删掉`, { ok: T("删除"), danger: true }))) return;
  try { fillProviders(await api(`/api/providers/profile/${encodeURIComponent(cur.id)}`, { method: "DELETE" })); } catch (e) { $("#provMsg").innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
};
$("#provKeyClear").onclick = async () => {
  if (!(await uiConfirm(T("清除这个平台保存的 API key？"), { ok: T("清除"), danger: true }))) return;
  try {
    fillProviders(await api("/api/providers", { json: { provider: $("#provKind").value, clear_key: true } }));
    $("#provMsg").innerHTML = T('<span class="ok">已清除 key</span>');
  } catch (e) { $("#provMsg").innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
};
$("#provCheck").onclick = async () => {
  $("#provMsg").textContent = T("连接中…（先保存，再测试保存后的设置）");
  try {
    const r = await api("/api/providers/check", { method: "POST" });
    if (r.note) { $("#provMsg").innerHTML = `<span class="ok">${esc(T(r.note))}</span>`; return; }
    $("#provMsg").innerHTML = T`<span class="ok">连上了</span>，平台上有 ${r.models} 个模型` +
      (r.missing.length ? T`；<span class="bad">没找到：${esc(r.missing.join("、"))}</span>（名字可能写错了）` : "");
  } catch (e) { $("#provMsg").innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
};
loadProviders();
$("#helpClose").onclick = () => ($("#help").hidden = true);


for (const panel of ["#maskPanel", "#mattePanel"]) $(panel).addEventListener("click", (e) => {   // 首帧 / 尾帧：「本图 · 还原」= 去掉这一项的单图覆盖
  const tag = e.target.closest(".ovrTag");
  if (!tag || !imgEditing()) return;
  e.preventDefault(); e.stopPropagation();
  const k = tag.closest(".mctl").dataset.f, os = selected();
  if (os.length) imgLayerEdit(os, (o) => { o[k] = (obj(o.id) || {})[k] ?? null; });
});

// 点时间轴上的图层条 = 选中这个图层（主体扩绘区 / 主体局部保留的条：选中它的主体并让它成为正在编辑的那个；不在帧范围里就跳过去）
$("#objBars").addEventListener("click", (e) => {
  const bar = e.target.closest(".bar");
  if (!bar || e.target.closest(".bh") || !S.d) return;
  const o = obj(bar.dataset.oid);
  if (!o) return;
  const kid = bar.dataset.keep, mid = bar.dataset.ext;
  const m = kid ? (o.keep_masks || []).find((v) => v.id === kid) : mid ? extsOf(o).find((v) => v.id === mid) : null;
  if (kid) (S.keepSel ||= {})[o.id] = kid; else if (S.keepSel) delete S.keepSel[o.id];
  if (mid) (S.extSel ||= {})[o.id] = mid;
  const s = m ? m.start ?? 0 : o.start, en = m ? m.end ?? S.d.n - 1 : o.end;
  if (S.i < s || S.i > en) S.i = s;
  select(o.id, !m && (e.shiftKey || e.ctrlKey));
  renderLayers(); draw();
});
// 图层条区域的高度：拖时间轴上边的小横条（记在浏览器里）
(() => {
  const g = $("#barsGrip"), KEY = "vps_bars_h";
  const set = (h) => { $("#timeline").style.setProperty("--bars-h", Math.round(Math.min(Math.max(h, 16), 420)) + "px"); };
  try { const v = +localStorage.getItem(KEY); if (v) set(v); } catch {}
  g.addEventListener("pointerdown", (e) => {
    e.preventDefault(); g.setPointerCapture(e.pointerId);
    const y0 = e.clientY, h0 = $("#objBars").getBoundingClientRect().height;
    const mv = (ev) => set(h0 + (y0 - ev.clientY));                 // 往上拖 = 变高（画面跟着变矮）
    const up = () => {
      g.removeEventListener("pointermove", mv); g.removeEventListener("pointerup", up);
      try { localStorage.setItem(KEY, Math.round($("#objBars").getBoundingClientRect().height)); } catch {}
    };
    g.addEventListener("pointermove", mv); g.addEventListener("pointerup", up);
  });
  g.addEventListener("dblclick", () => { $("#timeline").style.removeProperty("--bars-h"); try { localStorage.removeItem(KEY); } catch {} });
})();

// 空区蒙版只作用在一段帧上：拖时间轴上它那条色条的两端，松手存（服务端按新帧范围重建这个图层）
$("#objBars").addEventListener("mousedown", (e) => {
  const h = e.target.closest(".bh"), bar = e.target.closest(".bar");
  if (!h || !bar || !S.d) return;
  e.preventDefault(); e.stopPropagation();
  const o = obj(bar.dataset.oid), seg = bar.querySelector(".seg"), r = bar.getBoundingClientRect(), mid = bar.dataset.ext;
  const kid = bar.dataset.keep;
  const m = kid ? (o.keep_masks || []).find((v) => v.id === kid) : mid && extsOf(o).find((v) => v.id === mid);   // 主体局部保留 / 跟随蒙版的色条
  let s = m ? m.start ?? 0 : o.start, en = m ? m.end ?? S.d.n - 1 : o.end;
  const frame = (ev) => Math.round(Math.min(Math.max((ev.clientX - r.left) / r.width, 0), 1) * (S.d.n - 1));
  const move = (ev) => {
    const f = frame(ev);
    if (h.dataset.e === "start") s = Math.min(f, en); else en = Math.max(f, s);
    seg.style.left = fx(s) + "%"; seg.style.width = Math.max(fx(en) - fx(s), 0.5) + "%";
    setLog(`${m ? m.name : o.name}${T("：")}${FR(s, en)}`);
  };
  const up = async () => {
    removeEventListener("mousemove", move); removeEventListener("mouseup", up);
    if (kid) return keepEdit(o, kid, (v) => ({ ...v, start: s, end: en }), T("改主体局部保留帧范围"));
    if (mid) return editExt(o, mid, { start: s, end: en }, T("主体扩绘区帧范围"));   // 主体扩绘区：只是图层字段，不用重建
    if (s === o.start && en === o.end) return;
    try {
      setProject(await api(`/api/p/${S.pid}/shape_range`, { json: { oid: o.id, start: s, end: en } }), true);
      S.zoneGen = (S.zoneGen || 0) + 1; zoneDecoded.clear(); zoneImgs.clear(); zoneRetry.clear(); drawSoon();
      setLog(uiLanguage === "en" ? `Empty-area mask applies only to frames ${s}–${en}` : `空区蒙版只作用在第 ${s}–${en} 帧`);
    } catch (err) { setLog(err.message, true); renderLayers(); }
  };
  addEventListener("mousemove", move); addEventListener("mouseup", up);
});

// 图层行里的帧范围数字框：跟随蒙版 = 改图层字段；固定蒙版 = 服务端按新帧范围重建
$("#layerList").addEventListener("change", async (e) => {
  const inp = e.target.closest("input.fr"), row = inp && inp.closest(".layer");
  if (!row) return;
  const o = obj(row.dataset.id), mid = row.dataset.ext;
  const v = (k) => Math.min(Math.max(Math.round(+row.querySelector(`input.fr[data-k="${k}"]`).value || 0), 0), S.d.n - 1);
  let s = v("start"), en = v("end");
  if (s > en) inp.dataset.k === "start" ? (en = s) : (s = en);
  if (row.dataset.keep) return keepEdit(o, row.dataset.keep, (v) => ({ ...v, start: s, end: en }), T("改主体局部保留帧范围"));
  if (mid) return editExt(o, mid, { start: s, end: en }, T("主体扩绘区帧范围"));
  if (!o.shape || (s === o.start && en === o.end)) return renderLayers();
  try {
    setProject(await api(`/api/p/${S.pid}/shape_range`, { json: { oid: o.id, start: s, end: en } }), true);
    S.zoneGen = (S.zoneGen || 0) + 1; zoneDecoded.clear(); zoneImgs.clear(); zoneRetry.clear(); drawSoon();
    setLog(uiLanguage === "en" ? `${o.name} applies only to frames ${s}–${en}` : `${o.name}只作用在第 ${s}–${en} 帧`);
  } catch (err) { setLog(err.message, true); renderLayers(); }
});
// 蒙版面板「手绘蒙版」：帧范围、启用（和图层行的帧范围数字框、眼睛同一套）
async function setShapeRange() {
  const o = selected()[0];
  if (!o || !o.shape) return;
  const v = (id) => Math.min(Math.max(Math.round(+$(id).value || 0), 0), S.d.n - 1);
  let s = v("#shapeStart"), en = v("#shapeEnd");
  if (s > en) document.activeElement === $("#shapeEnd") ? (s = en) : (en = s);
  if (s === (o.start ?? 0) && en === (o.end ?? S.d.n - 1)) return renderProps();
  try {
    setProject(await api(`/api/p/${S.pid}/shape_range`, { json: { oid: o.id, start: s, end: en } }), true);
    S.zoneGen = (S.zoneGen || 0) + 1; zoneDecoded.clear(); zoneImgs.clear(); zoneRetry.clear(); drawSoon();
    setLog(uiLanguage === "en" ? `${o.name} applies only to frames ${s}–${en}` : `${o.name}只作用在第 ${s}–${en} 帧`);
  } catch (err) { setLog(err.message, true); renderProps(); }
}
for (const id of ["#shapeStart", "#shapeEnd"]) {
  $(id).addEventListener("change", setShapeRange);
  $(id).addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") e.target.blur(); });
}
$("#shapeOn").addEventListener("change", (e) => {
  const o = selected()[0];
  if (o && o.shape) commit([o], (x) => { x.off = e.target.checked ? null : true; }, T("启用 / 停用蒙版"));
});
$("#layerList").addEventListener("keydown", (e) => { if (e.target.matches("input.fr")) { e.stopPropagation(); if (e.key === "Enter") e.target.blur(); } });

// 识别一个部位（原「从属图层」：在主体里面再用 SAM 识别一块，比如 hands，单独成层）。图层行 ＋ 和蒙版面板「部位」＋ 共用
async function addPart(o) {
  try {
    const text = await uiPrompt(T("输入要自动识别的部位，例如 hands（双手）"));
    if (!text || !text.trim()) return;
    await api(`/api/p/${S.pid}/obj/${o.id}/parts`, { json: { text: text.trim() } });
    await watchJob();
  } catch (err) { setLog(err.message, true); }
}
// 图层行上的 ＋：加一个扩大重绘范围（跟随蒙版），或识别一个部位
$("#layerList").addEventListener("click", (e) => {
  const add = e.target.closest(".part-add");
  if (!add) return;
  const o = obj(add.dataset.parent), m = $("#layerMenu"), r = add.getBoundingClientRect();
  m.innerHTML = T('<div data-act="ext" title="主体外框往某一边拉出去，让模型把那一边画出来">主体扩绘区</div><div data-act="keep" title="在这个主体身上画一块范围：范围里保留，范围外重绘">主体局部保留…</div><div data-act="part" title="自动识别这个图层里的部位（如 hands），单独成层">主体抠图拆分…</div>');
  m.hidden = false;
  m.style.left = Math.min(r.left, innerWidth - 260) + "px";
  m.style.top = Math.min(r.bottom + 4, innerHeight - 120) + "px";
  m.onclick = async (ev) => {
    const act = ev.target.closest("[data-act]")?.dataset.act;
    if (!act) return;
    m.hidden = true;
    if (act === "ext") return addExt(o);
    if (act === "keep") {
      if (!S.sel.has(o.id) || S.sel.size !== 1) select(o.id);
      return openShapeMenu(add, ["keep"]);
    }
    addPart(o);
  };
});

// 蒙版面板「主体重绘范围」里的主体局部保留（keep_masks）和部位列表
function renderSubExtras(o) {
  const subj = !!o && !o.shape, n = S.d ? S.d.n : 1;
  renderExtList(o);
  const kl = subj ? o.keep_masks || [] : [];
  $("#keepNew").disabled = !subj;
  $("#keepList").innerHTML = kl.map((m) => `<div class="sl-item${m.off ? " off" : ""}" data-kid="${esc(m.id)}">` +   // 两行：启用 · 名字 · × / 帧 起–止
    `<div class="sl-row">` + T`<input type="checkbox" class="k-on" ${m.off ? "" : "checked"} title="启用（关掉 = 当它不存在）">` +
    `<span class="sl-n" title="${esc(m.name)}">${esc(m.name)}</span>` +
    T`<button type="button" class="ic k-del" title="删除这个主体局部保留">${IC("x")}</button></div>` +
    `<div class="sl-row sl-fr">` + T`<span class="sl-l">帧</span><input class="k-a" type="number" min="0" max="${n - 1}" value="${m.start ?? 0}" title="起始帧"> – <input class="k-b" type="number" min="0" max="${n - 1}" value="${m.end ?? n - 1}" title="结束帧">` + `</div></div>`).join("");
  const isPart = !!(o && o.parent_id);
  $("#partNew").hidden = !subj || isPart;                           // 只支持一层：部位里不能再加部位
  const kids = subj && !isPart ? objs().filter((x) => x.parent_id === o.id) : [];
  $("#partList").innerHTML = isPart ? `<div class="sub">${esc(T`属于「${obj(o.parent_id)?.name || ""}」`)}</div>`
    : kids.map((k) => `<button type="button" class="ghostbtn sl-chip" data-sel="${esc(k.id)}" title="${esc(T("选中这个部位"))}">${esc(k.name)}</button>`).join("");
}
function keepEdit(o, kid, fn, label) {                             // 改 / 删一个主体局部保留（fn 返回 null = 删）
  commit([o], (x) => { x.keep_masks = (x.keep_masks || []).map((m) => (m.id === kid ? fn({ ...m }) : { ...m })).filter(Boolean); }, label);
}
function editKeep(kid, fn, label) { const o = selected()[0]; if (o) keepEdit(o, kid, fn, label); }
$("#keepNew").onclick = (e) => { e.stopPropagation(); const o = selected()[0]; if (o && !o.shape) openShapeMenu(e.currentTarget, ["keep"]); };
$("#keepList").addEventListener("change", (e) => {
  const row = e.target.closest("[data-kid]");
  if (!row) return;
  if (e.target.matches(".k-on")) return editKeep(row.dataset.kid, (m) => { if (e.target.checked) delete m.off; else m.off = true; return m; }, T("启用 / 停用主体局部保留"));
  const n = S.d.n, cl = (v) => Math.max(0, Math.min(n - 1, Math.round(+v || 0)));
  let a = cl(row.querySelector(".k-a").value), b = cl(row.querySelector(".k-b").value);
  if (a > b) { if (e.target.matches(".k-a")) b = a; else a = b; }
  editKeep(row.dataset.kid, (m) => ({ ...m, start: a, end: b }), T("改主体局部保留帧范围"));
});
$("#keepList").addEventListener("click", (e) => {
  const row = e.target.closest("[data-kid]");
  if (row && e.target.closest(".k-del")) editKeep(row.dataset.kid, () => null, T("删除主体局部保留"));
});
$("#keepList").addEventListener("keydown", (e) => { if (e.target.matches("input")) { e.stopPropagation(); if (e.key === "Enter") e.target.blur(); } });
$("#partNew").onclick = () => { const o = selected()[0]; if (o && !o.shape && !o.parent_id) addPart(o); };
$("#partList").addEventListener("click", (e) => { const b = e.target.closest("[data-sel]"); if (b) select(b.dataset.sel); });
