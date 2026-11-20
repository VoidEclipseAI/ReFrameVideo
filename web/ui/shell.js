/* 外壳：顶栏 / 右侧图标条 + 面板组 / 选中浮动条 / 打开对话框 / 快捷键。
   画布、图层、播放等行为仍然由 app/*.js 提供；这里只做界面层，不碰数据。 */
(() => {
  const q = (s, r = document) => r.querySelector(s);
  const qa = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const has = (n) => typeof window[n] === "function";

  /* ── 右侧面板：PS 式图标条 + 面板组（一次只开一组；再点同一个 = 收起） ── */
  const GROUP_KEY = "vps_group";
  const side = q("#sidebar");
  let curGroup = null;

  // 图片结果 / 视频结果页只是挑结果：别的面板组（图层、蒙版、变换…）都用不了，只留「生成」组（结果列表在里面）
  const resultsMode = () => ["ires", "res"].includes(document.body.dataset.tab);
  // 首帧 / 尾帧用候选图接着编辑（或上传的图）时没有视频图层：图层、蒙版、变换、画幅这几组用不了
  const noLayers = () => {
    if (document.body.dataset.tab !== "card" || typeof curImg !== "function") return false;
    const it = curImg();
    return !!it && (it.kind === "first" || it.kind === "last") && !!it.base_upload;
  };
  const NO_LAYER_GROUPS = ["layers", "mask", "transform", "frame"];
  const groupLocked = (g) => (resultsMode() && g !== "gen" && g !== "export") || (noLayers() && NO_LAYER_GROUPS.includes(g));   // 导出在结果页也能用
  window.uiResultsMode = function () {
    const nl = !resultsMode() && noLayers();
    qa("#panelRail [data-group]").forEach((b) => {
      const lock = groupLocked(b.dataset.group);
      b.disabled = lock;
      b.classList.toggle("locked", lock);
      if (lock) b.title = nl ? T("这张画面没有视频图层（接着编辑的候选图 / 上传的图）：在「生成」里改，可以画范围局部重绘") : T("结果页只能挑结果：回到「视频编辑」或「图片编辑」再用这一组");
      else if (b.dataset.tip) b.title = b.dataset.tip;
    });
    if (curGroup && groupLocked(curGroup)) openGroup("gen");
  };
  qa("#panelRail [data-group]").forEach((b) => { b.dataset.tip = b.title; });
  // 浏览器禁了网站数据时 localStorage 会抛错：包一层，存不了就当没存（不然整个 shell.js 跑不起来，右栏 / 快捷键都没了）
  const lsGet = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
  const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch {} };
  const lsDel = (k) => { try { localStorage.removeItem(k); } catch {} };

  function openGroup(name, force) {
    if (!side) return;
    if (name && groupLocked(name)) return;                          // 结果页 / 没有视频图层的画面：锁住的组打不开（快捷键 L / P 也一样）
    const ok = name && q('.pgroup[data-group="' + name + '"]');
    // 换组 = 退出上一组里正在用的临时模式（app/07b-shape.js uiLeaveGroup）；返回 false = 先弹窗问，用户选完再 openGroup(name, true)
    if (!force && curGroup && (ok ? name : null) !== curGroup && typeof window.uiLeaveGroup === "function"
        && window.uiLeaveGroup(curGroup, ok ? name : null) === false) return;
    curGroup = ok ? name : null;
    qa(".pgroup").forEach((g) => (g.hidden = g.dataset.group !== curGroup));
    qa("#panelRail [data-group]").forEach((b) => b.classList.toggle("on", b.dataset.group === curGroup));
    document.body.classList.toggle("sidebar-closed", !curGroup);
    lsSet(GROUP_KEY, curGroup || "");
    if (typeof S !== "undefined" && S.extPending && typeof renderLayers === "function") renderLayers();   // 离开蒙版组：刚添加还没拉开的主体扩绘区取消
    if (typeof drawGizmo === "function" && typeof S !== "undefined" && S.d) drawGizmo();   // 点选的点只在识别面板开着时显示
  }
  window.uiOpenGroup = openGroup;
  const toggleGroup = (name) => openGroup(name);      // 面板列不再收起：点已开的那组 = 保持开着

  // 打开某个面板（选中条、更多菜单、P 键都走这里）：开它所在的组
  function revealSide(sel) {
    const el = sel ? q(sel) : null;
    const g = el ? el.closest(".pgroup") : null;
    openGroup(g ? g.dataset.group : "layers");
    if (!el) return;
    setTimeout(() => {
      el.scrollIntoView({ block: "nearest", behavior: "smooth" });
      el.classList.add("flash");
      setTimeout(() => el.classList.remove("flash"), 1200);
    }, 40);
  }

  /* 结果页的左栏：图片结果、视频结果都只有一个「结果」（index.html 里 .rail-page 那两个） */
  window.uiSyncRailPage = function () {
    const tab = document.body.dataset.tab;
    if (tab !== "ires" && tab !== "res") return;
    qa("#panelRail .rail-page").forEach((b) => b.classList.toggle("on", b.dataset.page === tab));
    openGroup("gen");
  };
  qa("#panelRail .rail-page").forEach((b) => b.addEventListener("click", () => window.uiSyncRailPage()));
  qa("#panelRail [data-group]").forEach((b) => b.addEventListener("click", () => toggleGroup(b.dataset.group)));

  /* 右栏宽度：拖左边缘调；记在浏览器里；窗口变窄时自动收窄，至少给画面留 480 px；双击恢复默认 */
  const WIDTH_KEY = "vps_panel_w", W_DEF = 400, W_MIN = 280, W_MAX = 760;
  let panelW = +lsGet(WIDTH_KEY) || W_DEF;
  function applyPanelW() {
    const room = window.innerWidth - (q("#toolbar")?.offsetWidth || 52) - (q("#panelRail")?.offsetWidth || 56) - 480;
    const w = clamp(panelW, W_MIN, Math.max(W_MIN, Math.min(W_MAX, room)));
    document.documentElement.style.setProperty("--panel-w", w + "px");
    if (has("fitView") && S.d) requestAnimationFrame(() => { try { draw(); } catch {} });
  }
  {
    const h = q("#sideResize");
    h?.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      h.setPointerCapture(e.pointerId);
      const x0 = e.clientX, w0 = q("#panelCol").offsetWidth;
      document.body.classList.add("side-resizing");
      const move = (ev) => { panelW = w0 + (x0 - ev.clientX); applyPanelW(); };
      const up = () => {
        h.removeEventListener("pointermove", move); h.removeEventListener("pointerup", up); h.removeEventListener("pointercancel", up);
        document.body.classList.remove("side-resizing");
        panelW = q("#panelCol").offsetWidth;
        lsSet(WIDTH_KEY, String(panelW));
        window.dispatchEvent(new Event("resize"));               // 画面 / 任务窗按新宽度重排
      };
      h.addEventListener("pointermove", move); h.addEventListener("pointerup", up); h.addEventListener("pointercancel", up);
    });
    h?.addEventListener("dblclick", () => { panelW = W_DEF; lsDel(WIDTH_KEY); applyPanelW(); window.dispatchEvent(new Event("resize")); });
    h?.addEventListener("keydown", (e) => {
      if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
      e.preventDefault();
      panelW = q("#panelCol").offsetWidth + (e.key === "ArrowLeft" ? 20 : -20);
      applyPanelW(); lsSet(WIDTH_KEY, String(q("#panelCol").offsetWidth)); window.dispatchEvent(new Event("resize"));
    });
    window.addEventListener("resize", applyPanelW);
    applyPanelW();
  }
  q("#segText")?.addEventListener("focus", () => openGroup("layers"));
  {
    const saved = lsGet(GROUP_KEY);
    openGroup(saved || "layers");                                   // 面板列常开：上次存的是空（以前收起来过）也开图层组
  }


  window.uiOpenDrawer = (name) => (name === "task" ? window.taskWindow?.open() : revealSide(name === "inspector" ? "#propPanel" : null));
  window.uiCloseDrawers = () => {};
  window.uiOpenGroup = openGroup;
  window.uiCurGroup = () => curGroup;
  window.uiResetLayout = () => {
    panelW = W_DEF; lsDel(WIDTH_KEY); applyPanelW(); openGroup("layers");
    window.taskWindow?.resetLayout(); window.dispatchEvent(new Event("resize"));
  };

  /* ── 对话框 ───────────────────────────────────── */
  function openDialog(id, focusSel) {
    qa(".ui-dialog").forEach((d) => (d.hidden = d.id !== id));
    const d = q("#" + id);
    if (!d) return;
    d.hidden = false;
    if (focusSel) setTimeout(() => { const f = q(focusSel, d); if (f) { f.focus(); f.select?.(); } }, 30);
  }
  function closeDialogs() { qa(".ui-dialog").forEach((d) => (d.hidden = true)); }
  const anyDialog = () => qa(".ui-dialog").some((d) => !d.hidden);

  qa(".ui-dialog").forEach((d) => {
    d.addEventListener("pointerdown", (e) => { if (e.target === d) d.hidden = true; });
    qa(".dlg-close", d).forEach((b) => b.addEventListener("click", () => (d.hidden = true)));
  });
  q("#projBtn")?.addEventListener("click", () => openDialog("openDlg", "#videoPath"));
  q("#empty")?.addEventListener("click", () => openDialog("openDlg", "#videoPath"));
  q("#uiToast")?.addEventListener("click", (e) => (e.currentTarget.hidden = true));

  /* ── 选中条 ───────────────────────────────────── */
  const bar = q("#selBar");
  const EDGE_NAME = { sam: T("硬边"), mix: T("混合"), matte: T("柔边") };
  const GROW_MIN = -10, GROW_MAX = 20;

  const selObjs = () => (S.sel ? [...S.sel].map((id) => { try { return obj(id); } catch { return null; } }).filter(Boolean) : []);
  const editingImage = () => (typeof imgEditing === "function" ? imgEditing() : null);
  const selAllowed = () => {
    if (!S.d || !S.sel || !S.sel.size) return false;
    if (S.playing || S.compare || S.cmp || S.cmpSide || S.frameMode || S.refine || S.shape || S.stillShow) return false;
    const it = editingImage();
    return S.tab === "video" || !!it;
  };
  const apply = (fn, label) => {
    const os = selObjs();
    if (!os.length) return;
    if (has("editLayers")) editLayers(os, fn, label);
    else if (has("commit")) commit(os, fn, label);
  };

  function unionBox(os) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const o of os) {
      let pts = [];
      try { pts = polygon(o, S.i) || []; } catch { pts = []; }
      if (!pts.length && S.d) pts = [{ x: 0, y: 0 }, { x: S.d.w, y: 0 }, { x: S.d.w, y: S.d.h }, { x: 0, y: S.d.h }];
      for (const p of pts) {
        const px = Array.isArray(p) ? p[0] : p && p.x;
        const py = Array.isArray(p) ? p[1] : p && p.y;
        if (!Number.isFinite(px) || !Number.isFinite(py)) continue;
        x0 = Math.min(x0, px); y0 = Math.min(y0, py); x1 = Math.max(x1, px); y1 = Math.max(y1, py);
      }
    }
    return Number.isFinite(x0) ? { x0, y0, x1, y1 } : null;
  }

  let lastSig = "";
  function syncSelBar() {
    if (!bar) return;
    if (!selAllowed()) { if (!bar.hidden) bar.hidden = true; return; }
    const os = selObjs();
    if (!os.length) { bar.hidden = true; return; }
    const st = has("stageRect") ? stageRect() : null;
    if (!st || !st.w) { bar.hidden = true; return; }
    const box = unionBox(os);
    if (!box) { bar.hidden = true; return; }

    // 内容：只在变化时改文本，避免每帧重建
    const single = os.length === 1 ? os[0] : null;
    const eo = single && typeof effObj === "function" ? effObj(single) : single;
    const growVals = os.map((o) => (typeof effObj === "function" ? effObj(o) : o).grow ?? 0);
    const grow = growVals.every((v) => v === growVals[0]) ? growVals[0] : null;
    const scaleVals = os.map(scaleOf);
    const scale = scaleVals.every((v) => v === scaleVals[0]) ? scaleVals[0] : null;
    const anyLocked = os.some((o) => !!o.locked);
    const edgev = os.map((o) => (typeof effObj === "function" ? effObj(o) : o).edge || "sam");
    const sameEdge = edgev.every((v) => v === edgev[0]);
    const visible = os.every((o) => o.shape ? !o.off : o.visible !== false);
    const locked = os.every((o) => !!o.locked);
    const sig = [os.length, single?.id, single?.name, grow, scale, anyLocked, sameEdge ? edgev[0] : "mix", visible, locked,
      eo ? [eo.ring_redraw, eo.hole_rect, eo.box_redraw, eo.edge_fill].join(",") : ""].join("|");
    if (sig !== lastSig) {
      lastSig = sig;
      const nm = q("#sbName");
      if (nm) nm.textContent = single ? single.name : T`已选 ${os.length} 个图层`;
      if (nm) nm.title = T("在右栏打开这个图层的蒙版设置");
      const dot = q("#sbDot");
      if (dot) dot.style.background = single ? single.color || "var(--accent)" : "var(--accent)";
      const eye = q("#sbEye");
      if (eye) { eye.innerHTML = IC(visible ? "eye" : "eye-off"); eye.title = visible ? T("隐藏（H）") : T("显示（H）"); }
      const sv = q("#sbScaleVal");
      if (sv) sv.textContent = scale == null ? "—" : scale + "%";
      for (const id of ["#sbScaleMinus", "#sbScaleVal", "#sbScalePlus", "#sbFlip", "#sbReset"]) { const b = q(id); if (b) b.disabled = anyLocked; }
      const sc = q("#sbScale");
      if (sc) sc.title = anyLocked ? T("图层锁定了，先解锁再缩放") : T("缩放（和「变换」里的宽 % / 高 % 是同一个值）");
      const rf = q("#sbRefine");
      if (rf) {
        const no = os.length !== 1 || !!editingImage();
        rf.disabled = no;
        rf.classList.toggle("off", no);
        rf.title = no ? T("逐帧修整只能对一个图层用（图片编辑页也不行）") : T("修这一帧的抠图");
      }
      const mg = q("#sbMerge");
      if (mg) mg.hidden = os.length < 2;                            // 合并只在多选时出现
      const mk = q("#sbMask");
      if (mk) mk.classList.toggle("on", !!eo && !!(+eo.ring_redraw || +eo.hole_rect || eo.box_redraw || eo.edge_fill));
    }

    // 位置：选区框下方，让开框边的控制点和橙色扩展箭头；碰到画面底边的缩放条 / 图例或放不下，就放框上方（上方还有旋转点）；
    // 上方也放不下（人占满整个高度）就放画面最顶上
    const k = st.w / (S.d.w || 1);
    const sx = st.x + box.x0 * k, sy = st.y + box.y0 * k;
    const ey = st.y + box.y1 * k;
    const stage = q("#stage");
    const sw = stage ? stage.clientWidth : window.innerWidth;
    const sh = stage ? stage.clientHeight : window.innerHeight;
    bar.hidden = false;                                     // 先显示再量：hidden 时 offsetWidth 是 0，会按 260 算，条子会被右边裁掉
    const bw = bar.offsetWidth || 260, bh = bar.offsetHeight || 34;
    const GAP = 30;
    const left = clamp(sx, 6, Math.max(6, sw - bw - 6));
    const sr = stage ? stage.getBoundingClientRect() : { left: 0, top: 0 };
    const rectIn = (sel) => { const el = q(sel); if (!el || el.hidden || !el.offsetWidth) return null;
      const r = el.getBoundingClientRect(); return { l: r.left - sr.left, t: r.top - sr.top, r: r.right - sr.left, b: r.bottom - sr.top }; };
    const blockers = [rectIn("#zoomBar"), rectIn("#zoneLegend")].filter(Boolean);
    const free = (t) => t >= 6 && t + bh <= sh - 6 && !blockers.some((o) => left < o.r && left + bw > o.l && t < o.b && t + bh > o.t);
    const top = [ey + GAP, sy - bh - GAP - 12].find(free) ?? 6;
    bar.style.left = Math.round(left) + "px";
    bar.style.top = Math.round(top) + "px";
  }

  function closeMenus() {
    for (const id of ["#sbMenu", "#sbMaskMenu"]) { const m = q(id); if (m) m.hidden = true; }
  }
  const anyMenu = () => ["#sbMenu", "#sbMaskMenu", "#tabMenu", "#layerMenu"].some((id) => { const m = q(id); return m && !m.hidden; });
  document.addEventListener("pointerdown", (e) => {
    if (e.target.closest?.("#selBar, #sbMenu, #sbMaskMenu")) return;
    if (e.target.closest?.("#tabMenu") || e.target.closest?.("#layerMenu")) return;
    closeMenus();
  });

  q("#sbClose")?.addEventListener("click", () => { if (has("select")) select(null); });
  q("#sbName")?.addEventListener("click", () => revealSide("#maskPanel"));
  q("#sbScaleMinus")?.addEventListener("click", () => stepScale(-5));
  q("#sbScalePlus")?.addEventListener("click", () => stepScale(5));
  let scaleClick = 0;
  q("#sbScaleVal")?.addEventListener("click", () => {          // 单击 = 输入；双击 = 回 100%（单击稍等一下，别和双击打架）
    clearTimeout(scaleClick);
    scaleClick = setTimeout(async () => {
      const os = selObjs();
      if (!os.length) return;
      const v = await uiPrompt(T("缩放（%）"), String(scaleOf(os[0])));
      if (v != null && Number.isFinite(+v) && +v > 0) setScale(+v);
    }, 250);
  });
  q("#sbScaleVal")?.addEventListener("dblclick", () => { clearTimeout(scaleClick); setScale(100); });
  q("#sbEye")?.addEventListener("click", () => apply(toggleVis, T("显示 / 隐藏")));

  q("#sbRefine")?.addEventListener("click", () => {
    const os = selObjs();
    if (os.length !== 1 || editingImage()) return;
    if (has("startRefine")) startRefine(os[0].id);
  });
  q("#sbMerge")?.addEventListener("click", () => q("#mergeLayerBtn")?.click());
  q("#sbReset")?.addEventListener("click", () => q("#resetBtn")?.click());

  q("#sbFlip")?.addEventListener("click", () => q("#flipHBtn")?.click());

  // 缩放：图层宽 % 的绝对值（宽高比例照旧，翻转的正负号照旧）
  function scaleOf(o) { const t = o.tf; return t ? Math.round(Math.abs(t.sx ?? 1) * 100) : 100; }
  function setScale(pct) {
    const os = selObjs().filter((o) => !o.locked);
    if (!os.length) return;
    pct = clamp(Math.round(pct), 5, 1000);
    apply((o) => {
      if (o.locked) return;
      const t = (o.tf ||= { x: 0, y: 0, sx: 1, sy: 1, rot: 0 });
      const old = { ...t };
      const f = pct / 100 / Math.max(Math.abs(t.sx ?? 1), 1e-6);
      t.sx = (t.sx ?? 1) * f; t.sy = (t.sy ?? 1) * f;
      if (typeof anchorFix === "function") anchorFix(o, old);      // 变换中心那个点不动（变换面板里选的）
    }, T("缩放"));
    if (has("renderProps")) renderProps();
  }
  function stepScale(d) {
    const os = selObjs();
    if (!os.length) return;
    setScale(Math.round((scaleOf(os[0]) + d) / 5) * 5);
  }

  // 下拉菜单里的「− 值 +」一行：直接改图层字段（可撤销），菜单不关、数字就地更新
  const STEPS = {
    ring_redraw: { label: T("主体轮廓重绘"), min: -20, max: 40, step: 2, fmt: (v) => (v > 0 ? T("往外 ") + v : v < 0 ? T("往里 ") + -v : T("关")),
                   tip: T("主体轮廓重绘：正数 = 外面加一圈、负数 = 身上贴边那圈不贴，都涂灰重绘（原视频像素）") },
    hole_rect: { label: T("空区形状"), min: 0, max: 8, step: 1, fmt: (v) => (v ? T("矩形 ×") + v : T("轮廓")),
                 tip: T("原位置的空区：轮廓 = 照原样；矩形 ×N = 横切 N 段换成矩形，不留人形") },
    grow: { label: T("收缩 / 扩展"), min: GROW_MIN, max: GROW_MAX, step: 1, fmt: (v) => v + " px",
            tip: T("抠图往外扩 / 往里收（只管这个图层带走哪些像素）") },
  };
  const cur = () => { const o = selObjs()[0]; return o && (typeof effObj === "function" ? effObj(o) : o); };
  function stepRow(f) {
    const c = STEPS[f], v = +(cur()?.[f] ?? 0);
    return '<div class="sb-growrow" data-row="' + f + '" title="' + c.tip + '"><span class="lbl">' + c.label + "</span>" +
      '<button class="sb-ico" data-step="' + f + '" data-d="-1" aria-label="-">' + IC("minus") + "</button>" +
      '<button class="sb-val" data-step="' + f + '" data-d="0" title="' + T("点一下归零") + '">' + c.fmt(v) + "</button>" +
      '<button class="sb-ico" data-step="' + f + '" data-d="1" aria-label="+">' + IC("plus") + "</button></div>";
  }
  function onStep(e) {
    const b = e.target.closest("[data-step]");
    if (!b) return false;
    e.stopPropagation();
    const f = b.dataset.step, c = STEPS[f], d = +b.dataset.d;
    const v0 = +(cur()?.[f] ?? 0), v = d ? clamp(v0 + d * c.step, c.min, c.max) : 0;
    if (v !== v0) apply((o) => { o[f] = v; }, c.label);
    if (has("renderProps")) renderProps();
    const row = e.currentTarget.querySelector('[data-row="' + f + '"]');
    if (row) row.outerHTML = stepRow(f);
    return true;
  }
  const chk = (on) => IC(on ? "check" : "dot");

  /* 蒙版菜单：最常用的几项，其余在右栏「蒙版」 */
  q("#sbMask")?.addEventListener("click", (e) => {
    e.stopPropagation();
    const m = q("#sbMaskMenu");
    if (!m) return;
    if (!m.hidden) { m.hidden = true; return; }
    const o = cur(), os = selObjs();
    const noHole = !os.some((x) => x.visible === false || !isIdentity(x.tf));
    m.innerHTML = T('<div class="note">主体重绘范围</div>') + stepRow("ring_redraw") +
      '<div data-act="boxredraw" title="' + T("主体外接框里，除了看得见的图层，全部重绘") + '">' + chk(o?.box_redraw) + "<span>" + T("边界框内重绘") + "</span></div>" +
      '<div data-act="edgefill" title="' + T("原视频里贴着画面边被截掉的主体，移动后把截断处补画出来") + '">' + chk(o?.edge_fill) + "<span>" + T("补全被裁切部分") + "</span></div>" +
      '<div data-act="ext" title="' + T("主体外框往某一边拉出去，让模型把那边画出来（跟着主体走）") + '">' + IC("expand") + "<span>" + T("主体扩绘区…") + "</span></div>" +
      T('<div class="sep"></div><div class="note">原位置空区</div>') +
      (noHole ? '<div class="dis" style="opacity:.4" title="' + T("主体还没移开，原位置没有洞") + '">' + IC("grid") + "<span>" + T("空区形状") + "</span></div>" : stepRow("hole_rect")) +
      '<div class="sep"></div><div data-act="panel">' + IC("sliders") + "<span>" + T("全部蒙版设置…") + "</span></div>";
    placeSbMenu(m, q("#sbMask"));
  });
  q("#sbMaskMenu")?.addEventListener("click", (e) => {
    if (onStep(e)) return;
    const it = e.target.closest("[data-act]");
    if (!it) return;
    const act = it.dataset.act;
    q("#sbMaskMenu").hidden = true;
    if (act === "boxredraw") return apply((o) => { o.box_redraw = o.box_redraw ? null : true; }, T("边界框内重绘"));
    if (act === "edgefill") return apply((o) => { o.edge_fill = o.edge_fill ? null : true; }, T("补全被裁切部分"));
    if (act === "panel") return revealSide("#maskPanel");
    if (act === "ext") {
      revealSide("#maskPanel");
      setTimeout(() => {
        const card = q('#maskPanel .mctl[data-f="box_ext"]');
        if (card) { card.scrollIntoView({ block: "center", behavior: "smooth" }); card.style.outline = "2px solid var(--accent)"; setTimeout(() => (card.style.outline = ""), 1400); }
      }, 40);
    }
  });

  // 选中条的下拉菜单：画面（#stage）会裁掉超出的部分，所以用 fixed 贴着按钮放，下面放不下就往上翻，再不行就滚动
  function placeSbMenu(m, btn) {
    if (m.parentElement !== document.body) document.body.append(m);   // 选中条有 backdrop-filter，fixed 在它里面会按它定位
    m.hidden = false;
    Object.assign(m.style, { position: "fixed", right: "auto", maxHeight: innerHeight - 16 + "px", overflowY: "auto" });
    const r = btn.getBoundingClientRect(), w = m.offsetWidth, h = m.offsetHeight;
    m.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + "px";
    m.style.top = (r.bottom + 6 + h <= innerHeight - 8 ? r.bottom + 6 : Math.max(8, r.top - h - 6)) + "px";
  }
  /* 更多菜单 */
  q("#sbMore")?.addEventListener("click", (e) => {
    e.stopPropagation();
    const m = q("#sbMenu");
    if (!m) return;
    if (!m.hidden) { m.hidden = true; return; }
    const os = selObjs();
    const one = os.length === 1;
    const img = !!editingImage();
    const eo = one ? (typeof effObj === "function" ? effObj(os[0]) : os[0]) : null;
    const item = (act, icon, label, dis, tip) => '<div data-act="' + act + '"' + (dis ? ' class="dis" style="opacity:.4"' : "") +
      (tip ? ' title="' + tip + '"' : "") + ">" + IC(icon) + "<span>" + label + "</span></div>";
    const locked = os.every((o) => !!o.locked);
    m.innerHTML =
      T('<div class="note">抠图边缘</div>') +
      ["sam", "mix", "matte"].map((v) => '<div data-edge="' + v + '">' + chk((eo?.edge || "sam") === v) + "<span>" + EDGE_NAME[v] + "</span></div>").join("") +
      (one ? stepRow("grow") : "") +
      '<div class="sep"></div>' +
      item("flipv", "flip-v", T("垂直翻转")) +
      item("rename", "scan", T("改名…"), !one) +
      item("front", "to-front", T("置顶")) +
      item("back", "to-back", T("置底")) +
      item("lock", locked ? "unlock" : "lock", locked ? T("解锁") : T("锁定")) +
      '<div class="sep"></div>' +
      '<div data-act="del" class="danger">' + IC("trash") + "<span>" + T("删除图层") + "</span></div>";
    placeSbMenu(m, q("#sbMore"));
  });
  q("#sbMenu")?.addEventListener("click", async (e) => {
    if (onStep(e)) return;
    const eg = e.target.closest("[data-edge]");
    if (eg) {
      q("#sbMenu").hidden = true;
      const v = eg.dataset.edge;
      apply((o) => { o.edge = v; }, T("边缘"));
      const sel = q("#edgeSel");
      if (sel) { sel.value = v; sel.dispatchEvent(new Event("change")); }
      return;
    }
    const it = e.target.closest("[data-act]");
    if (!it || it.classList.contains("dis")) return;
    q("#sbMenu").hidden = true;
    const act = it.dataset.act;
    if (act === "rename") {
      const o = selObjs()[0];
      if (!o) return;
      const nm = await uiPrompt(T("图层名字"), o.name || "");
      if (nm && nm.trim()) apply((x) => { x.name = nm.trim(); }, T("改名"));
      return;
    }
    if (act === "flipv") return q("#flipVBtn")?.click();
    if (act === "lock") { const all = selObjs().every((o) => !!o.locked); return apply((o) => { o.locked = !all; }, all ? T("解锁") : T("锁定")); }
    if (act === "del") return q("#delLayerBtn")?.click();
    if (act === "front" || act === "back") { if (has("zOrder")) zOrder(act); return; }
  });

  /* ── 顶栏工程信息 ─────────────────────────────── */
  function syncProject(d) {
    if (d) closeDialogs();                                  // 项目真打开了（慢加载也算）：别再盖着「打开视频/项目」
    const name = d && d.name ? d.name : T("未打开项目");
    const pn = q("#projName");
    if (pn) pn.textContent = name;
    const pb = q("#projBtn");
    if (pb) pb.title = d ? d.name + T("（点击打开 / 切换项目）") : T("打开视频或项目（Ctrl+O）");
    document.title = d ? d.name + " · ReFrameVideo" : "ReFrameVideo";
    const n = d && Array.isArray(d.objects) ? d.objects.length : 0;
    const lc = q("#layerCount");
    if (lc) { lc.textContent = n > 99 ? "99+" : String(n); lc.hidden = !n; }
    if (!d) { const w = q("#selBar"); if (w) w.hidden = true; }
  }
  window.uiProjectChanged = syncProject;

  /* ── 图标栏徽标 ───────────────────────────────── */
  function railSync() {
    const lc = q("#layerCount");
    if (!lc) return;
    const n = S.d && Array.isArray(S.d.objects) ? S.d.objects.length : 0;
    const t = n > 99 ? "99+" : String(n);
    if (lc.textContent !== t) lc.textContent = t;
    lc.hidden = !n;
  }

  window.uiAfterDraw = function uiAfterDraw() {
    try { syncSelBar(); railSync(); } catch (err) { console.warn("[ui]", err && err.message); }
  };

  /* ── 快捷键 ───────────────────────────────────── */
  document.addEventListener("keydown", (e) => {
    const t = e.target;
    const typing = t && t.closest && t.closest("input,textarea,select,[contenteditable]");
    if (e.key === "Escape") {
      if (S.refine || S.shape) return;                 // 抠图 / 形状编辑自己处理
      if (anyMenu()) { closeMenus(); e.preventDefault(); e.stopPropagation(); return; }
      if (anyDialog()) { closeDialogs(); e.preventDefault(); e.stopPropagation(); return; }
      return;                                          // 交给 04：取消选中
    }
    if (typing) return;
    if (e.ctrlKey || e.metaKey || e.altKey) {
      if (!e.altKey && (e.key === "o" || e.key === "O")) { e.preventDefault(); openDialog("openDlg", "#videoPath"); }
      return;
    }
    const k = e.key.toLowerCase();
    if (k === "l") { e.preventDefault(); toggleGroup("layers"); }
    else if (k === "p") { e.preventDefault(); revealSide("#maskPanel"); }
  }, true);

  /* ── 启动 ─────────────────────────────────────── */
  q("#stopBtn2")?.addEventListener("click", () => q("#stopBtn")?.click());
  syncProject(S.d);
  // 启动流程（app/09-generate.js 末尾）在项目列表 / 上次项目处理完之后决定要不要弹；
  // 不能在这里盲等 900ms：上次项目慢的时候会把已经打开的项目又盖上。
  window.uiOpenProjectDialog = () => { if (!S.d && !anyDialog()) openDialog("openDlg", "#videoPath"); };
})();

/* 滑杆旁边的数值：点一下变成输入框，直接打数字（回车 / 失焦生效，Esc 取消）。所有「名字 + 滑杆 + 数值」的行通用 */
(function () {
  const rangeOf = (b) => {
    for (let el = b.parentElement; el && el !== document.body; el = el.parentElement) {
      const rs = el.querySelectorAll('input[type="range"]');
      if (rs.length === 1) return rs[0];
      if (rs.length > 1) return null;
    }
    return null;
  };
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest('b[id$="Val"]');
    if (!b || b.querySelector("input")) return;
    const r = rangeOf(b);
    if (!r || r.disabled || r.closest(".disabled")) return;
    const old = b.textContent;
    const inp = document.createElement("input");
    inp.type = "number"; inp.className = "val-edit";
    inp.min = r.min; inp.max = r.max; inp.step = r.step || 1; inp.value = r.value;
    b.textContent = ""; b.append(inp); inp.focus(); inp.select();
    let done = false;
    const finish = (ok) => {
      if (done) return; done = true;
      const v = Number(inp.value);
      inp.remove(); b.textContent = old;
      if (!ok || inp.value === "" || !Number.isFinite(v)) return;
      const lo = r.min === "" ? -Infinity : +r.min, hi = r.max === "" ? Infinity : +r.max;
      r.value = String(Math.min(hi, Math.max(lo, v)));
      r.dispatchEvent(new Event("input", { bubbles: true }));     // 和拖滑杆一样：先 input（预览 / 数值）再 change（存盘 + 撤销）
      r.dispatchEvent(new Event("change", { bubbles: true }));
    };
    inp.addEventListener("keydown", (ev) => {
      ev.stopPropagation();                                         // 别让 Delete / 空格 / Ctrl+Z 这些快捷键吃掉
      if (ev.key === "Enter") finish(true);
      else if (ev.key === "Escape") finish(false);
    });
    inp.addEventListener("blur", () => finish(true));
  });
})();
