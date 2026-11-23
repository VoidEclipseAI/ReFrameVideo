/* 项目标签（Chrome 式）：顶栏一排打开过的项目。点 = 切到那个项目，× / 中键 = 关掉标签（项目文件不删），
   最右边 ＋（#projBtn）= 打开视频 / 项目。打开的列表和每个项目停在哪一页、第几帧记在浏览器里。
   同时只有当前项目真正载入；切走的项目内存怎么处理看设置「后台的项目标签」（01-core.js 的 setProject）。 */
(() => {
  const KEY = "vps_open_projects";
  const stKey = (pid) => "vps_tabstate_" + pid;
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch { return []; } };
  const save = () => {
    try { localStorage.setItem(KEY, JSON.stringify(list)); } catch {}
    document.dispatchEvent(new Event("vps-tabs"));      // 段号栏（ui/sequence.js）跟着把已打开的段灰掉 / 恢复
  };
  let list = load();                                     // [{ pid, name }]

  // 长片段（ui/sequence.js）的标签 pid = "seq:<sid>"；它的某一段在同一个标签里打开（标签记着 seq = sid）
  const curKey = () => { const sq = window.uiSeqOpen?.(); return sq ? "seq:" + sq : S.pid; };
  function render() {
    const box = document.querySelector("#projTabs");
    if (!box) return;
    const cur = curKey();
    box.innerHTML = list.map((t) =>
      `<div class="ctab ${t.pid === cur ? "on" : ""}" data-pid="${esc(t.pid)}" role="tab" title="${esc(t.name)}">` +
      `${IC(t.pid.startsWith("seq:") ? "layers" : "film")}<span class="ctab-lbl">${esc(t.name)}</span>` +
      T`<span class="ctab-x" role="button" title="关掉这个项目标签（项目文件不删）" aria-label="关掉">${IC("x")}</span></div>`).join("");
    const on = box.querySelector(".ctab.on");
    if (on) on.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  function remember() {                                  // 切走前记下停在哪一页、第几帧，切回来接着看
    if (!S.pid || !S.d) return;
    try { localStorage.setItem(stKey(S.pid), JSON.stringify({ tab: S.tab, i: S.i })); } catch {}
  }
  let openTok = 0;
  async function open(pid) {
    if (pid === curKey()) return;
    remember();
    if (pid.startsWith("seq:")) return window.uiOpenSeq?.(pid.slice(4));
    window.uiCloseSeq?.();
    if (pid === S.pid) return render();                   // 分段页盖着的就是这个项目：关掉分段页就好
    const tok = ++openTok;                               // 快速连点几个标签：只用最后一次的回包
    try {
      const d = await withBusy(T("正在加载项目…"), () => api(`/api/p/${encodeURIComponent(pid)}`));
      if (tok !== openTok) return;
      setProject(d);
      let st = null;
      try { st = JSON.parse(localStorage.getItem(stKey(pid)) || "null"); } catch {}
      if (st) {
        S.i = Math.max(0, Math.min(st.i || 0, S.d.n - 1));
        if (st.tab) { S.tab = st.tab; renderTabs(); }
        draw();
      }
    } catch (e) {                                        // 项目不在了：把标签去掉，换到旁边那个（都没了 = 回到空白）
      setLog(e.message, true);
      const k = list.findIndex((t) => t.pid === pid);
      list = list.filter((t) => t.pid !== pid); save();
      if (k >= 0 && !(S.pid && list.some((t) => t.pid === S.pid))) {   // 现在显示的东西已经没有标签了
        const next = list[k] || list[k - 1];
        if (next) return open(next.pid).then(render);
        try { localStorage.removeItem("vps_pid"); } catch {}
        return location.reload();
      }
      render();
    }
  }
  function close(pid) {
    const k = list.findIndex((t) => t.pid === pid);
    if (k < 0) return;
    list.splice(k, 1); save();
    try { localStorage.removeItem(stKey(pid)); } catch {}
    if (pid !== curKey()) return render();
    window.uiCloseSeq?.();
    const next = list[k] || list[k - 1];
    if (next) return open(next.pid).then(render);
    try { localStorage.removeItem("vps_pid"); } catch {}  // 最后一个也关了：回到空白（弹「打开」对话框）
    location.reload();
  }

  const prev = window.uiProjectChanged;                  // 项目打开 / 变化（shell.js 的顶栏同步）之后：标签列表跟上
  const seqTab = (sid) => list.findIndex((x) => x.pid === "seq:" + sid);
  window.uiProjectChanged = (d) => {
    if (prev) prev(d);
    if (d) {
      const t = list.find((x) => x.pid === d.id);
      if (t) t.name = d.name;
      else {                                             // 长片段的一段：新开一个标签，排在这个长片段的标签后面
        const t2 = d.seq ? { pid: d.id, name: d.name, seq: d.seq.id } : { pid: d.id, name: d.name };
        let k = -1;
        if (d.seq) list.forEach((x, j) => { if (x.pid === "seq:" + d.seq.id || x.seq === d.seq.id) k = j; });
        if (k >= 0) list.splice(k + 1, 0, t2); else list.push(t2);
      }
      save();
    }
    render();
  };
  function showSeq(sid, name) {                          // 分段页打开了：这个长片段的分段页标签，没有就加一个（各段的标签不动）
    const k = seqTab(sid), t = { pid: "seq:" + sid, name };
    if (k >= 0) list[k] = t; else list.push(t);
    save(); render();
  }
  function toSeq(childPid, sid, gid) { remember(); window.uiOpenSeq?.(sid, gid); }
  document.addEventListener("change", (e) => { if (e.target.id === "projectSel") remember(); }, true);   // 从「打开」对话框换项目也先记下
  document.addEventListener("click", (e) => {
    const t = e.target.closest("#projTabs .ctab");
    if (!t) return;
    if (e.target.closest(".ctab-x")) { e.stopPropagation(); return close(t.dataset.pid); }
    open(t.dataset.pid);
  });
  document.addEventListener("auxclick", (e) => {         // 中键关标签（和浏览器一样）
    const t = e.target.closest("#projTabs .ctab");
    if (t && e.button === 1) { e.preventDefault(); close(t.dataset.pid); }
  });
  window.uiProjectTabs = { open, close, showSeq, toSeq, list: () => list.slice() };

  /* 拖动排顺序（三层标签共用）：按住标签左右拖，松手就换位置；拖过的那一下不算点击。 */
  function reorderable(box, sel, key, done) {
    let drag = null;
    box.addEventListener("pointerdown", (e) => {
      const el = e.target.closest(sel);
      if (!el || e.button !== 0 || e.target.closest(".ctab-x, .x, .ctab-add")) return;
      drag = { el, x0: e.clientX, on: false, id: e.pointerId };
    });
    addEventListener("pointermove", (e) => {
      if (!drag || e.pointerId !== drag.id) return;
      if (!drag.on) {
        if (Math.abs(e.clientX - drag.x0) < 6) return;
        drag.on = true; drag.el.classList.add("dragging"); document.body.classList.add("tab-dragging");
      }
      const items = [...box.querySelectorAll(sel)].filter((x) => x !== drag.el);
      const after = items.find((x) => { const r = x.getBoundingClientRect(); return e.clientX < r.left + r.width / 2; });
      if (after) { if (after.previousElementSibling !== drag.el) box.insertBefore(drag.el, after); }
      else { const last = items[items.length - 1]; if (last && last.nextElementSibling !== drag.el) last.after(drag.el); }
    });
    addEventListener("pointerup", (e) => {
      if (!drag || e.pointerId !== drag.id) return;
      const was = drag.on, el = drag.el;
      drag = null;
      if (!was) return;
      el.classList.remove("dragging"); document.body.classList.remove("tab-dragging");
      const eat = (ev) => { ev.stopPropagation(); ev.preventDefault(); };   // 松手那一下不当点击（没有点击跟着来就很快撤掉，不吃掉下一次真点击）
      addEventListener("click", eat, { capture: true, once: true });
      setTimeout(() => removeEventListener("click", eat, true), 250);
      done([...box.querySelectorAll(sel)].map((x) => x.dataset[key]));
    });
  }
  const q = (s) => document.querySelector(s);
  reorderable(q("#projTabs"), ".ctab", "pid", (ids) => {
    list.sort((a, b) => ids.indexOf(a.pid) - ids.indexOf(b.pid)); save(); render();
  });
  reorderable(q("#tabList"), ".dtab", "id", (ids) => window.uiSetTabOrder && window.uiSetTabOrder("pages", ids));
  reorderable(q("#tabSub"), ".dtab", "id", (ids) => window.uiSetTabOrder && window.uiSetTabOrder("images", ids));
  render();
})();
