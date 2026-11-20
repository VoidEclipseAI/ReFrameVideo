// 视频 PS 前端：提示词里的 @引用（像 Seedance）。在提示词框里输入 @ → 弹出这次的参考图（缩略图 + 名称），
// 选一张插入「@名称」。文字里存的就是 @名称；发请求时带上 mentions = [{name, key}]，后端按这次实际的顺序换成
// 模型认得的写法（Picture N / <Picture N> / @图片N，见 app/mentions.py），所以换顺序、去掉图都不会指错。
// items()：[{ name, key, thumb, alias }]（alias = 另一种语言的名字，换了界面语言也认得）。

function mentionAttach(ta, items) {
  let menu = null, list = [], at = -1, sel = 0;
  const close = () => { if (menu) menu.hidden = true; at = -1; };
  const query = () => {                                            // 光标前面是「@xxx」（中间没有空白）才弹
    const m = /@([^\s@]*)$/.exec(ta.value.slice(0, ta.selectionStart));
    return m ? { start: m.index, q: m[1].toLowerCase() } : null;
  };
  const pick = (k) => {
    const x = list[k], end = ta.selectionStart;
    if (!x || at < 0) return close();
    const ins = "@" + x.name + " ";
    ta.setRangeText(ins, at, end, "end");
    close();
    ta.dispatchEvent(new Event("input", { bubbles: true }));       // 让原来的保存逻辑照常走
    ta.focus();
  };
  const show = () => {
    const q = query();
    if (!q) return close();
    list = items().filter((x) => !q.q || x.name.toLowerCase().includes(q.q) || (x.alias || "").toLowerCase().includes(q.q));
    if (!list.length) return close();
    at = q.start; sel = Math.min(sel, list.length - 1);
    if (!menu) {
      menu = document.createElement("div");
      menu.className = "ui-menu mention-menu";
      document.body.append(menu);
      menu.addEventListener("pointerdown", (e) => {               // 按下就插入（不等 click：textarea 失焦会先关菜单）
        const d = e.target.closest("[data-k]");
        if (d) { e.preventDefault(); pick(+d.dataset.k); }
      });
    }
    menu.innerHTML = list.map((x, k) => `<div data-k="${k}" class="${k === sel ? "on" : ""}">` +
      (x.thumb ? `<img src="${esc(x.thumb)}" alt="">` : `<span class="mm-ic">${IC(x.key === "video" ? "film" : "image")}</span>`) +
      `<span>@${esc(x.name)}</span></div>`).join("");
    menu.hidden = false;
    const r = ta.getBoundingClientRect(), mw = menu.offsetWidth, mh = menu.offsetHeight;
    menu.style.left = Math.max(8, Math.min(r.left, innerWidth - mw - 8)) + "px";
    menu.style.top = (r.bottom + 4 + mh > innerHeight - 8 ? Math.max(8, r.top - mh - 4) : r.bottom + 4) + "px";
  };
  ta.addEventListener("input", (e) => { if (e.isTrusted) { sel = 0; show(); } });
  ta.addEventListener("click", () => { sel = 0; show(); });
  ta.addEventListener("blur", close);
  ta.addEventListener("keydown", (e) => {
    if (!menu || menu.hidden) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { sel = (sel + (e.key === "ArrowDown" ? 1 : list.length - 1)) % list.length; show(); }
    else if (e.key === "Enter" || e.key === "Tab") pick(sel);
    else if (e.key === "Escape") close();
    else return;
    e.preventDefault(); e.stopPropagation();
  });
}

// text 里用到的 @名称 → [{name, key}]（items 用全部可能的图：这次没传的也带上，后端会提醒并当普通文字）
function mentionsOf(texts, items) {
  const all = [].concat(texts).filter(Boolean).join("\n"), out = [];
  for (const x of items) for (const name of [x.name, x.alias]) {
    if (name && all.includes("@" + name) && !out.some((m) => m.name === name)) out.push({ name, key: x.key });
  }
  return out;
}

// 名字：当前界面语言的 + 中文原名（别的语言下写的 @名称 也认得）
const mentionName = (zh) => ({ name: T(zh), alias: T(zh) === zh ? "" : zh });
function uniqueNames(items) {                                       // 重名的后面加 2、3…
  const seen = {};
  for (const x of items) { const n = seen[x.name] = (seen[x.name] || 0) + 1; if (n > 1) { x.name += " " + n; x.alias = ""; } }
  return items;
}
