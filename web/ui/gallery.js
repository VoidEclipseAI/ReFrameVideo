/* 所有结果（顶栏「所有结果」）：所有项目生成过的视频和图片放在一处看，按时间从新到旧。
   点一格 = 打开那个项目，跳到对应的「视频结果」/「图片结果」。生成完成时右下角提示也能直接跳过去（uiDoneToast）。
   后端 /api/gallery*（app/gallery.py）只读，不经过 proj()，不会放掉当前项目的遮罩缓存。 */
(() => {
  const q = (s, r = document) => r.querySelector(s);
  const KIND = { first: "首帧", last: "尾帧", card: "资料卡", bg: "背景图" };
  const ALL_KEY = "vps_gallery_all";
  let items = [], filter = "all", pidSel = "", showAll = false;
  try { showAll = localStorage.getItem(ALL_KEY) === "1"; } catch {}
  const blobs = new Map();                                 // 鼠标停过的视频：blob 地址（关掉时释放）

  const box = q("#gallery");                               // 界面在 index.html
  q("#glAll").checked = showAll;

  const fmt = (t) => { const d = new Date(t * 1000), p = (n) => String(n).padStart(2, "0"); return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`; };
  const thumb = (x) => `/api/gallery/thumb?pid=${encodeURIComponent(x.pid)}&type=${x.type}&id=${encodeURIComponent(x.id)}&cand=${encodeURIComponent(x.cand || "")}&t=${Math.round(x.t)}`;
  const kindName = (x) => x.type === "video" ? T("视频") : T(KIND[x.kind] || "图片");

  function render() {
    const list = items.filter((x) => (filter === "all" || (filter === "video" ? x.type === "video" : x.type !== "video")) && (!pidSel || x.pid === pidSel));
    q(".gl-count", box).textContent = T`${list.length} 条`;
    q(".gl-grid", box).innerHTML = list.length ? list.map((x, i) => `
      <button type="button" class="gl-card ${x.type === "video" ? "is-video" : ""}" data-i="${items.indexOf(x)}" title="${esc(x.project)}">
        <span class="gl-th"><img loading="lazy" src="${thumb(x)}" alt="">${x.type === "video" ? `<span class="gl-play">${IC("play")}</span>` : ""}</span>
        <span class="gl-meta"><span class="gl-kind">${esc(kindName(x))}${x.type === "video" ? " · " + esc(resText(x.label)) : x.picked && showAll ? T` · 选用` : ""}</span><span class="gl-time">${fmt(x.t)}</span></span>
        <span class="gl-proj">${esc(x.project)}</span>
      </button>`).join("") : `<div class="gl-empty">${T("还没有生成结果")}</div>`;
  }
  async function load() {
    q(".gl-grid", box).innerHTML = `<div class="gl-empty">${T("正在读取…")}</div>`;
    try { items = await api("/api/gallery" + (showAll ? "?all=1" : "")); }
    catch (e) { q(".gl-grid", box).innerHTML = `<div class="gl-empty">${esc(e.message)}</div>`; return; }
    const projs = [...new Map(items.map((x) => [x.pid, x.project])).entries()];
    if (pidSel && !projs.some(([p]) => p === pidSel)) pidSel = "";
    q("#glProj", box).innerHTML = T`<option value="">所有项目</option>` + projs.map(([p, n]) => `<option value="${esc(p)}" ${p === pidSel ? "selected" : ""}>${esc(n)}</option>`).join("");
    render();
  }
  function open() { box.hidden = false; document.body.classList.add("gallery-on"); load(); }
  function close() {
    box.hidden = true; document.body.classList.remove("gallery-on");
    for (const u of blobs.values()) URL.revokeObjectURL(u);
    blobs.clear();
  }

  // 打开项目并跳到这条结果（所有结果点一格、完成提示点「查看」共用）
  async function goto(x) {
    close();
    if (x.pid !== S.pid) await window.uiProjectTabs?.open(x.pid);
    if (!S.d || x.pid !== S.pid) return;
    const ver = x.version;
    if (ver && ver !== S.d.current && (S.d.versions || []).some((v) => v.id === ver))
      setProject(await api(`/api/p/${S.pid}/current`, { json: { version: ver } }), true);
    if (x.type === "video") { resShown = null; return showTab("res:" + x.id); }
    const tabs = imgTabs();
    const t = x.type === "bg" ? tabs.find((i) => i.kind === "bg") : tabs.find((i) => i.id === x.id) || tabs.find((i) => i.kind === x.kind);
    if (t) { S.lastImg = t.id; showTab("ires"); }
  }
  window.uiGotoResult = goto;

  box.addEventListener("click", (e) => {
    if (e.target.closest(".gl-x")) return close();
    const f = e.target.closest(".gl-seg button");
    if (f) { filter = f.dataset.f; box.querySelectorAll(".gl-seg button").forEach((b) => b.classList.toggle("on", b === f)); return render(); }
    const c = e.target.closest(".gl-card");
    if (c) goto(items[+c.dataset.i]);
  });
  q("#glProj", box).addEventListener("change", (e) => { pidSel = e.target.value; render(); });
  q("#glAll", box).addEventListener("change", (e) => {
    showAll = e.target.checked;
    try { localStorage.setItem(ALL_KEY, showAll ? "1" : "0"); } catch {}
    load();
  });
  addEventListener("keydown", (e) => { if (!box.hidden && e.key === "Escape") { e.stopPropagation(); e.preventDefault(); close(); } }, true);

  // 鼠标停在视频格子上：停一会儿才开始播（blob，不让 IDM 拦截），移开就停
  let hoverTimer = null;
  box.addEventListener("pointerover", (e) => {
    const c = e.target.closest(".gl-card.is-video");
    if (!c || c.contains(e.relatedTarget)) return;
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(async () => {
      const x = items[+c.dataset.i], key = x.pid + "/" + x.id;
      try {
        if (!blobs.has(key)) {
          const r = await fetch(`/api/gallery/video?pid=${encodeURIComponent(x.pid)}&id=${encodeURIComponent(x.id)}`);
          if (!r.ok) return;
          blobs.set(key, URL.createObjectURL(await r.blob()));
        }
        if (!c.matches(":hover") || c.querySelector("video")) return;
        const v = document.createElement("video");
        Object.assign(v, { muted: true, loop: true, autoplay: true, playsInline: true, src: blobs.get(key) });
        c.querySelector(".gl-th").appendChild(v);
      } catch {}
    }, 350);
  });
  box.addEventListener("pointerout", (e) => {
    const c = e.target.closest(".gl-card.is-video");
    if (!c || c.contains(e.relatedTarget)) return;
    clearTimeout(hoverTimer);
    c.querySelector("video")?.remove();
  });

  q("#galleryBtn")?.addEventListener("click", () => (box.hidden ? open() : close()));

  // 生成完成提示：右下角，带「查看」。当前项目的视频生成本来就会直接切到结果，不再提示
  const toast = q("#doneToast");
  let toastTimer = null, toastTarget = null;
  const kindOf = (name) => Object.keys(KIND).find((k) => name.startsWith(KIND[k]));
  window.uiDoneToast = (last) => {
    const name = last?.name || "";
    if (!last || last.error || last.cancelled || !last.project || !name.endsWith("生成")) return;
    let x;
    if (last.open_result) {
      if (last.project === S.pid) return;                  // 当前项目：已经直接切到结果了
      x = { pid: last.project, type: "video", id: last.open_result };
    } else {
      const k = kindOf(name);
      if (!k) return;
      x = { pid: last.project, type: k === "bg" ? "bg" : "image", kind: k };
    }
    const proj = (window.uiProjectTabs?.list() || []).find((t) => t.pid === last.project)?.name || last.project;
    toastTarget = x;
    q(".dt-text", toast).textContent = uiLanguage === "en" ? `${proj} · ${T(name)} finished` : `${proj} · ${name}完成`;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.hidden = true; }, 12000);
  };
  toast.addEventListener("click", (e) => {
    if (e.target.closest(".dt-go") && toastTarget) goto(toastTarget);
    if (e.target.closest(".dt-go, .dt-x")) toast.hidden = true;
  });
})();
