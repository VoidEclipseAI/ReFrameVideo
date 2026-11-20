// 视频 PS 前端：参考图清单（视频生成、图片生成共用）。
// 一行一张图：编号（= 提示词里的 Picture N）· 缩略图 · 用途 + 来源 ·「换 ▾」· ✕。
// 第一行固定（视频生成 = 首帧，图片生成 = 原图），其余按需「+ 添加」，没加的不占位置。
// 这里只管长相和菜单；每一行能做什么（换图 / 去掉 / 添加）由调用方（08 图片生成、09 视频生成）传进来。

// rows：[{ key, n, role, src, thumb, big, empty, emptyHint, fixed, note, noteN, change }]
//   n = 图号（没有就不显示）；empty = 这一行还没有图（只用于固定的第一行）；fixed = 不能去掉；
//   note = 这张图的说明（只有图片生成的额外参考图有）；change = 有「换 ▾」按钮
function refListHTML(rows) {
  return rows.map((r) => {
    const num = r.n ? T`<span class="rr-n" title="提示词里的 Picture ${r.n}">${r.n}</span>` : '<span class="rr-n off"></span>';
    const thumb = r.empty
      ? `<button type="button" class="rr-thumb empty" data-act="upload" title="${esc(r.emptyHint || T("上传"))}">${IC("plus")}</button>`
      : T`<span class="rr-thumb"><img src="${esc(r.thumb)}" data-big="${esc(r.big || r.thumb)}" loading="lazy" alt="" title="点一下看大图"></span>`;
    const acts = (r.refresh ? T`<button type="button" class="rr-act rr-refresh" data-act="refresh" title="按视频里现在的编辑重新取这一帧的画面（已选的候选保留）">${IC("rotate-ccw")} 刷新</button>` : "") +
      (r.more ? T`<button type="button" class="rr-act" data-act="more" data-refmenu="rr:more" title="更多操作" aria-label="更多操作">${IC("more")}</button>` : "") +
      (r.change ? T`<button type="button" class="rr-act" data-act="change" title="换一张">换 ${IC("chevron-down")}</button>` : "") +
      (r.fixed ? "" : T`<button type="button" class="rr-act rr-del" data-act="del" title="这次不用这张（文件不删）" aria-label="不用这张">${IC("x")}</button>`);
    const note = r.note != null
      ? T`<textarea class="rr-note" rows="1" data-n="${r.noteN || ""}" placeholder="这张图的说明（可空，英文效果最好）">${esc(r.note)}</textarea>` : "";
    return `<div class="rr${r.empty ? " is-empty" : ""}" data-key="${esc(r.key)}" data-name="${esc(r.key)}">${num}${thumb}` +
      `<span class="rr-txt"><b>${esc(r.role)}</b><small>${esc(r.empty ? r.emptyHint || "" : r.src || "")}</small></span>` +
      `<span class="rr-acts">${acts}</span>${note}</div>`;
  }).join("");
}

// 小菜单：items = [{ label, hint, run, disabled }]，贴在 anchor 下面；点外面 / Esc 关
let refMenuEl = null;
function openRefMenu(anchor, items) {
  if (!refMenuEl) {
    refMenuEl = document.createElement("div");
    refMenuEl.className = "ui-menu ref-menu";
    refMenuEl.hidden = true;
    document.body.append(refMenuEl);
    addEventListener("pointerdown", (e) => { if (!refMenuEl.hidden && !refMenuEl.contains(e.target) && !e.target.closest("[data-refmenu]")) refMenuEl.hidden = true; }, true);
    addEventListener("keydown", (e) => { if (e.key === "Escape") refMenuEl.hidden = true; });
  }
  if (!refMenuEl.hidden && refMenuEl.dataset.owner === anchor.dataset.refmenu) { refMenuEl.hidden = true; return; }
  refMenuEl.dataset.owner = anchor.dataset.refmenu || "";
  refMenuEl.innerHTML = items.map((it, k) => it.sep ? '<div class="sep"></div>'
    : `<div data-k="${k}" class="${it.disabled ? "off" : ""}">${esc(it.label)}${it.hint ? `<span class="note">${esc(it.hint)}</span>` : ""}</div>`).join("") ||
    T('<div class="off">没有可添加的</div>');
  refMenuEl.onclick = (e) => {
    const d = e.target.closest("[data-k]");
    if (!d || d.classList.contains("off")) return;
    refMenuEl.hidden = true;
    Promise.resolve(items[+d.dataset.k].run()).catch((err) => setLog(err.message, true));
  };
  refMenuEl.hidden = false;
  const r = anchor.getBoundingClientRect(), mw = refMenuEl.offsetWidth, mh = refMenuEl.offsetHeight;
  refMenuEl.style.left = Math.max(8, Math.min(r.right - mw, innerWidth - mw - 8)) + "px";
  refMenuEl.style.top = (r.bottom + 4 + mh > innerHeight - 8 ? Math.max(8, r.top - mh - 4) : r.bottom + 4) + "px";
}

// 选一个本地图片文件（不需要页面里预先放 <input>）
function pickImageFile(multiple = false) {
  return new Promise((res) => {
    const f = document.createElement("input");
    f.type = "file"; f.accept = "image/*"; f.multiple = multiple;
    f.onchange = () => res([...f.files]);
    f.addEventListener("cancel", () => res([]), { once: true });   // 点了取消：别让 await 永远等下去
    f.click();
  });
}

// 清单里点缩略图看大图（两处共用）
function refZoom(e) {
  const img = e.target.closest(".rr-thumb img[data-big]");
  if (!img) return false;
  $("#imgZoomImg").src = img.dataset.big;
  $("#imgZoom").hidden = false;
  return true;
}
