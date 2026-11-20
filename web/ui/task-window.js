/* 任务监控窗（从旧前端 app/static/task-window.js 搬来）：左边按时间排的任务列表，右边这个任务的进度和运行日志。
   两种摆法：「固定」= 停靠在右栏最下面，横跨图标条和面板列（面板列收起时临时浮在右下角）；「浮动」= 独立小窗，拖标题栏移动，可钉住位置。
   只显示，不调度任务。保留全局接口 window.taskWindow = {update, request, notice, open}，01-core / 05-project 的调用不用改。 */
(() => {
  const read = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) || fallback; } catch { return fallback; } };
  const save = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* 存不下就只留在内存里 */ } };
  let queue = [];   // 排队中（还没开始）的任务，服务端 /api/job 的 queue，不存
  let tasks = read("vps-task-history", []), settings = read("vps-task-window", {}), selected = null, manual = false, pending = null, lastUpdate = 0;
  if (!Array.isArray(tasks)) tasks = [];
  tasks = tasks.filter((t) => t && t.id && t.name && Number.isFinite(t.started));   // 旧格式（新前端 ui/task.js 存的，没有 started）丢掉
  tasks.forEach((t) => { if (t.busy) { t.busy = false; t.unknown = true; } });
  settings.pinned = settings.pinned !== false;
  if (settings.open == null) settings.open = true;

  const col = document.getElementById("panelCol"), side = document.getElementById("sidebar");
  if (!col) return;
  const win = document.createElement("section");
  win.id = "taskWindow";
  win.setAttribute("aria-label", T("任务监控"));
  win.innerHTML =
    T('<div class="tw-height" tabindex="0" role="separator" aria-label="调整任务监控高度" aria-orientation="horizontal"></div>') +
    T('<header><strong>任务监控</strong><span class="tw-state"></span>') +
    '<button type="button" class="tw-follow"></button><button type="button" class="tw-float"></button><button type="button" class="tw-pin"></button>' +
    '<button type="button" class="tw-close"></button></header>' +
    T('<div class="tw-body"><nav class="tw-list" aria-label="按时间排列的任务"></nav>') +
    T('<div class="tw-divider" role="separator" aria-label="调整任务列表宽度" aria-orientation="vertical" tabindex="0"></div>') +
    T('<div class="tw-detail"><div class="tw-summary" aria-live="polite">暂无任务</div>') +
    T('<div class="tw-actions"><button type="button" class="tw-cancel" hidden>停止任务</button><button type="button" class="tw-bottom">滚动到底部</button></div>') +
    T('<pre class="tw-log">提交任务后，这里显示运行日志。</pre></div></div>');
  const q = (s) => win.querySelector(s), list = q(".tw-list"), log = q(".tw-log"), summary = q(".tw-summary");
  const svg = (k) => '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + ({
    follow: '<circle cx="12" cy="12" r="7"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4"/><circle cx="12" cy="12" r="2"/>',
    float: '<rect x="3" y="5" width="18" height="15" rx="2"/><path d="M3 9h18M14 3h7v7m0-7-8 8"/>',
    pin: '<path d="m8 3h8l-1 7 4 4H5l4-4-1-7Zm4 11v7"/>',
    min: '<path d="M6 9l6 6 6-6"/>',
    max: '<path d="M6 15l6-6 6 6"/>',
  })[k] + "</svg>";
  for (const [cls, label] of [["follow", T("跟随运行中的任务")], ["float", T("浮动窗口")], ["pin", T("固定")], ["close", T("收起")]]) {
    const b = q(".tw-" + cls); b.innerHTML = svg(cls); b.title = label; b.setAttribute("aria-label", label);
  }
  if (settings.split) win.style.setProperty("--task-left", settings.split + "px");
  const persist = () => save("vps-task-window", settings);
  const colOpen = () => !document.body.classList.contains("sidebar-closed");
  const floatingNow = () => !!settings.floating || !colOpen();   // 固定但面板列收起：临时浮在右下角

  function clamp() {
    if (floatingNow()) {
      const w = Math.min(settings.width || 560, innerWidth - 24), h = Math.min(settings.floatHeight || 340, innerHeight - 70);
      const x = settings.floating ? settings.x ?? innerWidth - w - 18 : innerWidth - w - 74;   // 临时浮动：让开右边图标条
      const y = settings.floating ? settings.y ?? innerHeight - h - 18 : innerHeight - h - 14;
      const cx = Math.max(12, Math.min(x, innerWidth - w - 12)), cy = Math.max(52, Math.min(y, innerHeight - h - 12));
      if (settings.floating) { settings.x = cx; settings.y = cy; }
      Object.assign(win.style, { left: cx + "px", top: cy + "px", width: w + "px", height: h + "px" });
    } else {
      const max = Math.max(170, side.clientHeight - 200);
      Object.assign(win.style, { left: "", top: "", width: "", height: Math.max(170, Math.min(settings.height || 300, max)) + "px" });
    }
  }
  function place() {
    win.classList.toggle("tw-min", !settings.open);                 // 收起：只剩标题栏（显示有没有任务在跑），点标题栏展开
    const fl = floatingNow();
    const parent = fl ? document.body : side;   // 固定：接在右栏最下面，横跨图标条和面板列
    if (win.parentElement !== parent) parent.append(win);   // 已经在那里就别重挂：重挂会打断正在进行的点击、日志滚动位置也会丢
    win.classList.toggle("tw-floating", fl);
    win.classList.toggle("tw-locked", !!settings.floating && !!settings.floatPinned);
    const locked = settings.floating ? !!settings.floatPinned : true;
    const pinLbl = settings.floating ? (locked ? T("解锁窗口位置") : T("钉住窗口位置")) : T("已固定在右下");
    q(".tw-pin").title = pinLbl; q(".tw-pin").setAttribute("aria-label", pinLbl); q(".tw-pin").setAttribute("aria-pressed", String(locked));
    q(".tw-pin").disabled = !settings.floating;
    const flLbl = settings.floating ? T("固定到右下（停靠在面板下面）") : T("浮动窗口");
    q(".tw-float").title = flLbl; q(".tw-float").setAttribute("aria-label", flLbl); q(".tw-float").setAttribute("aria-pressed", String(!!settings.floating));
    const cl = settings.open ? T("收起（只留标题栏）") : T("展开");
    q(".tw-close").title = cl; q(".tw-close").setAttribute("aria-label", cl);
    q(".tw-close").innerHTML = svg(settings.open ? "min" : "max");
    clamp();
    persist();
  }
  q(".tw-pin").onclick = () => { if (settings.floating) { settings.floatPinned = !settings.floatPinned; place(); } };
  q(".tw-float").onclick = () => { settings.floating = !settings.floating; place(); };
  q(".tw-close").onclick = (e) => { e.stopPropagation(); open(!settings.open); };   // 别冒泡到标题栏（按钮图标换了，标题栏会误以为点的是空白处又展开）
  q("header").addEventListener("click", (e) => { if (!settings.open && !e.target.closest("button")) open(true); });
  function open(show = true) { settings.open = show; place(); if (show) render(true); }
  // 面板列开 / 收时，固定的窗口在「停靠」和「临时浮动」之间切换（body 的别的 class 变化不管：画面每帧都可能改）
  let wasFloating = null;
  new MutationObserver(() => { const f = floatingNow(); if (f !== wasFloating) { wasFloating = f; place(); } })
    .observe(document.body, { attributes: true, attributeFilter: ["class"] });

  // 任务名是服务端给的中文（server.py start_job 的 name）：英文界面显示时翻（存的历史不改）
  const tname = (n) => uiLanguage !== "en" || !n ? n : T(n).replace(/^准备第 (\d+) 段$/, "Prepare segment $1");
  const state = (t) => t.busy ? T("● 进行中") : t.unknown ? T("? 状态未确认") : t.cancelled ? T("■ 已取消") : t.error ? T("! 失败") : T("✓ 已完成");
  function render(scroll = false) {
    const active = tasks.find((t) => t.busy);
    const last = tasks.at(-1);                                     // 标题栏上的状态（收起时也看得到）
    q(".tw-state").textContent = active ? T("运行中 · ") + tname(active.name) + (queue.length ? T("（还有 ") + queue.length + T(" 个排队）") : "") : last && last.error ? T("上一个任务出错") : "";
    q(".tw-state").classList.toggle("err", !active && !!(last && last.error));
    win.classList.toggle("tw-busy", !!active);
    q(".tw-follow").classList.toggle("on", !manual);               // 跟随开着：按钮高亮
    const oldScroll = list.scrollTop;
    list.replaceChildren();
    tasks.forEach((t) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "tw-item" + (t.busy ? " tw-run" : ""); b.setAttribute("aria-pressed", String(t.id === selected));
      b.append(document.createTextNode(tname(t.name)));
      const s = document.createElement("small");
      s.textContent = state(t) + " · " + new Date(t.started * 1000).toLocaleTimeString("zh-CN", { hour12: false });
      b.append(s);
      b.onclick = () => { selected = t.id; manual = true; render(); log.scrollTop = 0; };
      list.append(b);
      if (scroll && t.id === selected) requestAnimationFrame(() => b.scrollIntoView({ block: "nearest" }));
    });
    queue.forEach((qd, k) => {                                     // 排队的任务：列表最下面，按顺序；✕ 移出队列
      const row = document.createElement("div");
      row.className = "tw-item tw-queued";
      row.append(document.createTextNode(tname(qd.name) || T("后台任务")));
      const s = document.createElement("small");
      s.textContent = T("… 排队中 · 第 ") + (k + 1) + T(" 个");
      row.append(s);
      const x = document.createElement("button");
      x.type = "button"; x.className = "tw-unqueue"; x.textContent = "✕"; x.title = T("移出队列（不跑这个任务）");
      x.onclick = async () => {
        x.disabled = true;
        try {
          const r = await fetch("/api/job/queue/" + qd.id + "/remove", { method: "POST" });
          if (!r.ok) throw Error(T("移出队列失败（HTTP ") + r.status + "）");
          queue = (await r.json()).queue || []; render();
        } catch (e) { x.disabled = false; notice(e.message, true); }
      };
      row.append(x);
      list.append(row);
    });
    if (!tasks.length && !queue.length) list.innerHTML = T('<div class="tw-empty">还没有任务</div>');
    if (!scroll) list.scrollTop = oldScroll;
    const t = tasks.find((t) => t.id === selected);
    if (!t) return;
    const p = t.progress;
    summary.textContent = tname(t.name) + " · " + state(t) + (p?.stage ? " · " + T(p.stage) : "") + (p?.max > 1 ? " " + p.value + "/" + p.max : "") +
      T(" · 已用 ") + Math.max(0, Math.round((t.ended || t.now || t.started) - t.started)) + T(" 秒");
    const stick = log.scrollHeight - log.scrollTop - log.clientHeight < 36;
    const value = (t.log || []).map(uiMsg).join("\n") + (t.error ? T("\n出错：") + uiMsg(t.error) : "");   // 日志是服务端写的中文：英文界面按句型翻
    if (log.textContent !== value) { log.textContent = value || T("等待日志…"); if (stick || scroll) log.scrollTop = log.scrollHeight; }
    q(".tw-cancel").hidden = !t.busy; q(".tw-cancel").disabled = !!t.stopping;
    if (!t.busy) q(".tw-cancel").textContent = T("停止任务");
  }
  q(".tw-follow").onclick = () => { manual = false; selected = (tasks.find((t) => t.busy) || tasks.at(-1))?.id; render(true); };
  q(".tw-bottom").onclick = () => { log.scrollTop = log.scrollHeight; };
  q(".tw-cancel").onclick = async () => {
    const t = tasks.find((t) => t.id === selected);
    if (!t?.busy) return;
    t.stopping = true; q(".tw-cancel").textContent = T("正在停止…"); render();
    try {
      const r = await fetch("/api/job/cancel", { method: "POST" });
      if (!r.ok) throw Error(T("停止请求失败（HTTP ") + r.status + "）");
      notice(T("已发出停止，正在撤掉任务…"));
    } catch (e) { t.stopping = false; q(".tw-cancel").textContent = T("停止任务"); notice(e.message, true); render(); }
  };

  // 05-project 的 watchJob 每秒调用；这里自己也轮询
  function update(j) {
    lastUpdate = Date.now();
    if (!j) return;
    const qChanged = JSON.stringify(queue) !== JSON.stringify(j.queue || []);
    queue = j.queue || [];
    const L = j.last, done = L && tasks.find((t) => t.id === String(L.started));
    if (done && done.busy) {                                       // 刚跑完的那个（下一个排队的已经接着跑了）：用它的最终结果收尾
      Object.assign(done, { busy: false, error: L.error, cancelled: L.cancelled, log: [...(L.log || [])], ended: L.ended, unknown: false });
      save("vps-task-history", tasks.slice(-40));
    }
    if (!j.started) { if (qChanged || done) render(); return; }
    const id = String(j.started);
    let t = tasks.find((t) => t.id === id);
    const fresh = !t;
    if (!t) {
      tasks.forEach((old) => { if (old.busy) { old.busy = false; old.unknown = true; } });
      t = { id, name: j.name || pending?.name || T("后台任务"), project: j.project || pending?.project };
      tasks.push(t);
      tasks.sort((a, b) => (a.started || Number(a.id)) - (b.started || Number(b.id)));
      pending = null;
    }
    const changed = fresh || t.busy !== j.busy || t.log?.length !== j.log?.length || t.error !== j.error;
    Object.assign(t, { started: j.started, busy: j.busy, error: j.error, cancelled: j.cancelled, log: [...(j.log || [])], progress: j.progress, now: j.now, unknown: false });
    if (!j.busy && !t.ended) t.ended = j.now || Date.now() / 1000;
    if (fresh && j.busy) manual = false;                            // 新任务开始：自动恢复跟随运行中的任务
    if (!manual && (j.busy || !selected || fresh)) selected = id;
    if (!j.busy && changed) save("vps-task-history", tasks.slice(-40));
    if (fresh && j.busy && !settings.open) open(true);              // 新任务开始：自动弹出
    render(fresh && !manual);
  }
  // 01-core 每次 api() 后调用：记下任务名字，等 /api/job 出现新任务时用
  function request(path, opts, result, s) {
    if (!opts || !opts.method || opts.method === "GET") return;
    let name = null;
    const image = s?.d?.images?.find?.((i) => path.includes("/images/" + i.id + "/"));
    const kind = { first: T("首帧"), last: T("尾帧"), bg: T("背景图"), card: T("资料卡") }[image?.kind || opts.json?.kind] || T("图片");
    if (/\/segment$/.test(path)) name = T("主体识别");
    else if (/\/parts$/.test(path)) name = T("部位识别");
    else if (/\/op$/.test(path)) name = T("视频生成");
    else if (/\/cands$/.test(path)) name = kind + T("生成");
    else if (/\/describe$/.test(path)) name = kind + T("描述");
    else if (/\/base$/.test(path)) name = kind + T("提取画面");
    else if (/\/panorama\/fill$/.test(path)) name = T("背景图生成");
    else if (/\/panorama$/.test(path)) name = T("背景图提取");
    else if (/\/export_frame$/.test(path)) name = T("画幅导出");
    else if (result?.pending) name = kind + T("准备");
    if (name) pending = { name, project: s?.pid };
    if (result?.queued) {                                          // 有任务在跑：这个进了队列
      toast(T("已加入队列：") + (name || T("任务")) + T("（前面还有 ") + result.queued + T(" 个任务，跑完自动开始）"));
      pending = null;                                             // 名字由服务端带着（j.name），别贴到正在跑的任务上
      fetch("/api/job", { cache: "no-store" }).then((r) => r.ok && r.json()).then((j) => j && update(j)).catch(() => {});
      if (!settings.open) open(true);
    }
  }
  // 提示：右下角小条（新前端原有的 #uiToast）
  let toastTimer = null;
  function notice(text, err) {
    if (!err) return;                                     // 普通提示留在运行日志里，别每次都弹右下角
    toast(text, err);
  }
  function toast(text, err) {
    const t = document.getElementById("uiToast");
    if (!text || !t) return;
    t.textContent = String(text).slice(0, 400);
    t.classList.toggle("err", !!err);
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, err ? 6000 : 2600);
  }

  function drag(el, move) {
    el.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || e.target.closest("button")) return;
      const start = { x: e.clientX, y: e.clientY, rect: win.getBoundingClientRect() };
      el.setPointerCapture(e.pointerId);
      const onMove = (v) => move(v, start);
      const end = () => { el.removeEventListener("pointermove", onMove); el.removeEventListener("pointerup", end); el.removeEventListener("pointercancel", end); persist(); };
      el.addEventListener("pointermove", onMove); el.addEventListener("pointerup", end); el.addEventListener("pointercancel", end);
    });
  }
  function split(n) { settings.split = Math.max(95, Math.min(n, win.clientWidth - 150)); win.style.setProperty("--task-left", settings.split + "px"); }
  drag(q(".tw-divider"), (e, s) => split(e.clientX - s.rect.left));
  drag(q("header"), (e, s) => { if (!settings.floating || settings.floatPinned) return; settings.x = s.rect.left + e.clientX - s.x; settings.y = s.rect.top + e.clientY - s.y; clamp(); });
  drag(q(".tw-height"), (e, s) => {
    if (floatingNow()) settings.floatHeight = Math.max(190, s.rect.height + s.y - e.clientY);
    else settings.height = s.rect.height + s.y - e.clientY;
    if (floatingNow()) { if (settings.floating) settings.y = s.rect.top + e.clientY - s.y; }
    clamp();
  });
  q(".tw-height").onkeydown = (e) => {
    if (!["ArrowUp", "ArrowDown"].includes(e.key)) return;
    e.preventDefault();
    const k = floatingNow() ? "floatHeight" : "height";
    settings[k] = win.clientHeight + (e.key === "ArrowUp" ? 20 : -20);
    clamp(); persist();
  };
  q(".tw-divider").onkeydown = (e) => { if (["ArrowLeft", "ArrowRight"].includes(e.key)) { e.preventDefault(); split((settings.split || 170) + (e.key === "ArrowLeft" ? -10 : 10)); persist(); } };
  addEventListener("resize", clamp);

  selected = tasks.at(-1)?.id;
  place();
  render();
  window.taskWindow = { update, request, notice, open: () => open(true), refresh: render, resetLayout: () => {
    settings = { open: true, pinned: true, floating: false };
    win.style.removeProperty("--task-left"); place(); render();
  } };
  async function poll() {
    try { if (Date.now() - lastUpdate > 1200 && !document.hidden) { const r = await fetch("/api/job", { cache: "no-store" }); if (r.ok) update(await r.json()); } }
    catch { /* 断线时保留上次的状态 */ }
    finally { setTimeout(poll, 1500); }
  }
  poll();
})();
