/* 长片段分段（2026-11-15 用户定）。后端 app/sequence.py，接口 /api/s/{sid}…
   - 分段页 #seqView：整条预览（小预览，blob 播放，不让 IDM 拦）+ 分段轨道 + 缩略图条 + 右边这一段的信息。
     长片段在项目标签里是 "seq:<sid>"；分段页开着时盖住编辑区，键盘只给分段页（空格播放、S 切一刀、←/→ 一帧）。
   - 「编辑这一段」= 打开这一段的普通项目，在同一个标签里（ptabs.js 把 seq:<sid> 那个标签换成这一段的项目）；
     编辑时左边栏最上面是段号（#segRail，见文件末尾），第一个按钮回到分段页。
   - 切点：分段轨道上每两段之间一条竖线（#seqCuts）可以拖；旁边的段已经在编辑时松手先问，确认了把它的项目移到回收站（/starts 的 reset）。
   - 退出：「回到第 k 段」/ Esc（从某一段回来时才有）、右上角 × = 关掉这个长片段的标签。
   - 锁定（#seqLock）：这一段「全片里用」定死成选的那条结果，之后再生成也不换；锁定的段在整条预览 / 缩略图条里放锁定的结果
     （服务端重拼 preview_mix.mp4，D.preview_v 变了就重新取预览）。
   - 打开视频时先 /api/probe_video：超过 ask_s 秒弹 #seqAskDlg 问分段编辑还是整段编辑（05-project.js 调 uiAskSplit）。 */
(() => {
  const q = (s) => document.querySelector(s);
  const EN = uiLanguage === "en";                          // 语序和中文不同的句子按语言分开写（词条拼不出来）
  const view = q("#seqView"), video = q("#seqVideo");
  let D = null, sel = null, blobUrl = null, blobSid = null, blobV = null, from = null;   // from = 从哪一段回到分段页的（「回到第 k 段」/ Esc 用）

  const fps = () => (D ? D.fps : 24);
  const frameNow = () => Math.min(D.n - 1, Math.max(0, Math.round(video.currentTime * fps())));
  const fmt = (f) => { const s = f / fps(); return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`; };
  const segAt = (f) => D.segments.find((s) => s.start <= f && f <= s.end);
  const curSeg = () => D.segments.find((s) => s.id === sel) || D.segments[0];

  async function load(sid) {
    D = await api(`/api/s/${encodeURIComponent(sid)}`);
    if (!D.segments.some((s) => s.id === sel)) sel = D.segments[0]?.id;
    render();
  }

  function render() {
    if (!D) return;
    q("#seqName").textContent = D.name;
    q("#seqInfo").textContent = `· ${fmt(D.n)} · ${D.w}×${D.h} · ${D.segments.length}` + (EN ? " segments" : " 段");
    q("#seqSegs").innerHTML = D.segments.map((s, k) => {
      const st = s.pid ? (s.results.length ? "done" : "edit") : "orig";
      const lab = st === "done" ? T("已生成") : st === "edit" ? T("编辑中") : T("原视频");
      return `<button type="button" class="sq-seg ${st} ${s.lock ? "locked" : ""} ${s.id === sel ? "on" : ""}" data-gid="${esc(s.id)}" style="${segPos(s)}" ` +
        `title="${esc(EN ? `Segment ${k + 1}: frames ${s.start}–${s.end} (${((s.end - s.start + 1) / fps()).toFixed(1)} s) · ${lab}` : `第 ${k + 1} 段：第 ${s.start}–${s.end} 帧（${((s.end - s.start + 1) / fps()).toFixed(1)} 秒）· ${lab}`) + (s.lock ? " · " + T("已锁定") : "")}"><b>${k + 1}${s.lock ? IC("lock") : ""}</b><span>${esc(lab)}</span></button>`;
    }).join("");
    renderCuts();
    const s = curSeg(), k = D.segments.indexOf(s);
    const sec = ((s.end - s.start + 1) / fps()).toFixed(1);
    q("#seqSegTitle").textContent = EN ? `Segment ${k + 1}` : `第 ${k + 1} 段`;
    q("#seqSegMeta").innerHTML = esc(EN ? `Frames ${s.start}–${s.end} · ${sec} s` : `第 ${s.start}–${s.end} 帧 · ${sec} 秒`) + "<br>" +
      esc(s.pid ? (EN ? `Being edited · ${s.results.length} video results` : `已在编辑 · ${s.results.length} 条视频结果`) : T("还没开始编辑")) + "<br>" + esc((EN ? "Used in the full video now: " : "全片里现在用：") + T(s.pick));
    q("#seqOpenSeg").textContent = s.pid ? T("继续编辑这一段") : T("编辑这一段");
    q("#seqUse").innerHTML = [["auto", T("最新的结果（没有就用原视频）")], ["orig", T("原视频")], ...s.results.map((r) => [r.id, `${resText(r.label) || r.id} · ${r.time}`])]
      .map(([v, t]) => `<option value="${esc(v)}" ${(s.use || "auto") === v ? "selected" : ""}>${esc(t)}</option>`).join("");
    q("#seqUse").disabled = !!s.lock;
    q("#seqLock").classList.toggle("on", !!s.lock);
    q("#seqLock").innerHTML = IC("lock") + (s.lock ? T("已锁定") : T("锁定"));
    q("#seqLock").title = s.lock ? T("已锁定：全片和上面的整条预览都用这条，再生成也不换。点一下解锁") : T("锁定：全片里这一段定死用选的这条，上面的整条预览也换成它");
    const nx = D.segments[k + 1];
    q("#seqMerge").disabled = !nx;
    const fk = D.segments.findIndex((g) => g.id === from && g.pid);
    q("#seqBack").hidden = fk < 0;
    if (fk >= 0) q("#seqBackTxt").textContent = T("回到第 {k} 段").replace("{k}", fk + 1);
    q("#seqStripImg").src = `/api/s/${encodeURIComponent(D.id)}/strip.jpg?k=40&v=${D.preview_v || 0}`;
    if (blobSid === D.id && blobV !== (D.preview_v || "0")) loadPreview(D.id);   // 锁定变了、整条预览重拼过：换成新的
    const mk = `${D.id}:${D.preview_v}:${D.preview_old ? 1 : 0}`;      // 预览要重做 / 锁定的段还没拼进去：每种情况只补一次，失败不反复重试
    if (D.mix_stale && !mixTried.has(mk)) { mixTried.add(mk); post("/mix"); }   // 锁定的段还没拼进整条预览（上次失败等）：补拼一次，失败不再重试
    head();
  }
  // 段的位置按帧数算（和下面的缩略图条对齐），切点竖线放在两段之间，可以拖
  const pct = (f) => (100 * f / D.n).toFixed(3) + "%";
  const segPos = (s) => `left:calc(${pct(s.start)} + 1.5px);width:calc(${pct(s.end - s.start + 1)} - 3px)`;
  function renderCuts() {
    q("#seqCuts").innerHTML = D.segments.slice(1).map((s, i) => {
      const lock = !!(s.pid || D.segments[i].pid);
      return `<i class="sq-cut ${lock ? "lock" : ""}" data-k="${i + 1}" style="left:${pct(s.start)}" title="${esc((EN ? `Cut at frame ${s.start} (${fmt(s.start)}) · drag left/right to adjust` : `切点：第 ${s.start} 帧（${fmt(s.start)}）· 左右拖动调整`) +
        (lock ? "\n" + T`旁边的段已经在编辑：松手时会问要不要把它的项目移到回收站、重新剪` : ""))}"></i>`;
    }).join("");
  }
  function head() {
    if (!D) return;
    const f = frameNow();
    q("#seqHead").style.left = (100 * f / Math.max(1, D.n - 1)) + "%";
    q("#seqTime").textContent = (EN ? `Frame ${f}` : `第 ${f} 帧`) + ` · ${fmt(f)} / ${fmt(D.n)}`;
    q("#seqPlay").innerHTML = IC(video.paused ? "play" : "pause");
  }

  async function open(sid, gid) {
    from = gid || null;
    if (gid) sel = gid;                                      // 从某一段回来：选中那一段
    if (blobSid !== sid) { blobSid = sid; blobV = null; video.removeAttribute("src"); }
    view.hidden = false;
    document.body.classList.add("seq-on");
    try { localStorage.setItem("vps_pid", "seq:" + sid); } catch {}
    await withBusy(T("正在加载长片段…"), () => load(sid));
    window.uiProjectTabs?.showSeq(sid, D.name);
    document.title = `${D.name} · ${T("分段")} · ReFrameVideo`;
  }
  function close() {
    if (view.hidden) return;
    video.pause();
    view.hidden = true;
    document.body.classList.remove("seq-on");
  }
  window.uiOpenSeq = (sid, gid) => open(sid, gid).catch((e) => setLog(e.message, true));
  window.uiCloseSeq = close;
  window.uiSeqOpen = () => (!view.hidden && D ? D.id : null);

  function loadPreview(sid) {                               // 预览按 blob 播放（地址里没有 .mp4，IDM 不拦）；锁定了段 = 服务端拼好的那份
    const v = D.preview_v || "0", t = video.currentTime || 0, playing = !video.paused;
    blobV = v;
    fetch(`/api/s/${encodeURIComponent(sid)}/preview?v=${v}`).then((r) => r.ok ? r.blob() : null).then((b) => {
      if (!b || blobSid !== sid || blobV !== v) return;
      if (blobUrl) URL.revokeObjectURL(blobUrl);
      blobUrl = URL.createObjectURL(b); video.src = blobUrl;
      video.addEventListener("loadedmetadata", () => {
        video.currentTime = t; if (playing) video.play();
        const r = q("#seqRes");                                    // 左上角标上预览分辨率（设置 → 性能「分段预览清晰度」）
        r.textContent = (EN ? "Preview " : "预览 ") + `${video.videoWidth} × ${video.videoHeight}`;
        r.hidden = !video.videoWidth;
      }, { once: true });
    }).catch(() => {});
  }
  const mixTried = new Set();
  async function post(path, json) {
    const sid = D.id;
    try {
      const r = await api(`/api/s/${encodeURIComponent(sid)}${path}`, { json: json || {} });
      if (r && r.segments) { D = r; return render(); }
      setLog(T("正在更新整条预览…"));                         // 锁定变了：服务端在后台重拼整条预览
      await watchJob();
      if (D && D.id === sid) await load(sid);
    } catch (e) { setLog(e.message, true); }
  }
  const starts = () => D.segments.slice(1).map((s) => s.start);
  // 改切点：会变的段里有已经在编辑的 → 先问，确认了这些段的项目移到回收站（服务端 body.reset）；不确认就还原
  async function commit(old, st) {
    const b = [0, ...[...new Set(st)].filter((x) => 0 < x && x < D.n).sort((x, y) => x - y), D.n];
    const keep = new Set(b.slice(1).map((e, i) => `${b[i]}-${e - 1}`));
    const gone = old.filter((s) => s.pid && !keep.has(`${s.start}-${s.end}`));
    if (gone.length) {
      const lines = gone.map((s) => EN ? `segment ${old.indexOf(s) + 1} (${s.results.length} video results)` : `第 ${old.indexOf(s) + 1} 段（${s.results.length} 条视频结果）`).join(EN ? ", " : "、");
      if (!(await uiConfirm(EN ? `${lines[0].toUpperCase() + lines.slice(1)}: already being edited. Changing the cut moves ${gone.length > 1 ? "these projects" : "its project"} to the recycle folder (projects\\_回收站, recoverable), then re-cuts and re-detects. Continue?`
                      : `${lines}已经开始编辑。改切点要把这些段的项目移到回收站（projects\\_回收站，能找回），之后按新范围重新剪、重新识别。继续？`))
        ) return load(D.id);
    }
    await post("/starts", { starts: st, reset: gone.map((s) => s.id) });
  }
  const snap = () => D.segments.map((s) => ({ ...s }));
  function cutHere(at) {
    const f = at ?? frameNow(), s = segAt(f);
    if (!s) return;
    if (f === s.start) return setLog(EN ? `The playhead is at frame ${f}, exactly the start of a segment: drag the playhead (red line) to where you want to cut first`
      : `播放头在第 ${f} 帧，正好是一段的开头：先把播放头（红线）拖到要切开的地方`, true);
    sel = s.id;
    commit(snap(), [...starts(), f]);
  }

  async function openSegment(gid) {
    try {
      const r = await api(`/api/s/${encodeURIComponent(D.id)}/seg/${encodeURIComponent(gid)}/open`, { method: "POST" });
      if (r.pid) return gotoProject(r.pid);
      setLog(T("正在剪出这一段、建项目…"));
      watchJob();                                            // 完成后 jobFinished → uiOpenSegment
    } catch (e) { setLog(e.message, true); }
  }
  async function gotoProject(pid) {
    close();
    try { setProject(await withBusy(T("正在加载项目…"), () => api(`/api/p/${encodeURIComponent(pid)}`))); } catch (e) { setLog(e.message, true); }
  }
  window.uiOpenSegment = gotoProject;

  // 导入时问要不要分段：返回 true = 已经按分段导入（调用方不要再走普通项目）
  window.uiAskSplit = (path, info) => new Promise((resolve) => {
    const dlg = q("#seqAskDlg");
    q("#seqAskText").textContent = EN ? `This video is ${info.duration.toFixed(1)} s long (${info.n} frames). As a single project, detection and generation will both be slow.`
      : `这段视频有 ${info.duration.toFixed(1)} 秒（${info.n} 帧）。整段做成一个项目时，识别和生成都会很慢。`;
    const done = async (split) => {
      dlg.hidden = true;
      q("#seqAskSplit").onclick = q("#seqAskWhole").onclick = null;
      if (!split) return resolve(false);
      try {
        await api("/api/sequences", { json: { video_path: path } });
        setLog(T("长片段导入中：转工作母版、找镜头切换…"));
        q("#openDlg").hidden = true;
        watchJob();                                          // 完成后 jobFinished → uiOpenSeq
        resolve(true);
      } catch (e) { setLog(e.message, true); resolve(true); }
    };
    q("#seqAskSplit").onclick = () => done(true);
    q("#seqAskWhole").onclick = () => done(false);
    dlg.hidden = false;
  });

  // 分段页的按钮
  q("#seqSegs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-gid]");
    if (!b) return;
    sel = b.dataset.gid;
    const s = curSeg();
    if (s) video.currentTime = (s.start + 0.5) / fps();
    render();
  });
  q("#seqSegs").addEventListener("dblclick", (e) => { const b = e.target.closest("[data-gid]"); if (b) openSegment(b.dataset.gid); });
  q("#seqStrip").addEventListener("pointerdown", (e) => {
    const r = q("#seqStrip").getBoundingClientRect();
    const seek = (ev) => { video.currentTime = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)) * (D.n - 1) / fps(); head(); };
    seek(e);
    const up = () => { removeEventListener("pointermove", seek); removeEventListener("pointerup", up); };
    addEventListener("pointermove", seek); addEventListener("pointerup", up);
  });
  q("#seqStrip").addEventListener("dblclick", (e) => {     // 缩略图条上双击：在那一帧加切点
    const r = q("#seqStrip").getBoundingClientRect();
    cutHere(Math.round(Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * (D.n - 1)));
  });
  q("#seqPlay").onclick = () => (video.paused ? video.play() : video.pause());
  video.addEventListener("timeupdate", head); video.addEventListener("play", head); video.addEventListener("pause", head);
  q("#seqCut").onclick = () => cutHere();
  q("#seqAuto").onclick = async () => { if (await uiConfirm(T("按镜头切换重新分段？已经在编辑的段不动，其余的切点会被替换。"))) post("/auto"); };
  q("#seqOpenSeg").onclick = () => openSegment(curSeg().id);
  q("#seqUse").onchange = (e) => post(`/seg/${encodeURIComponent(curSeg().id)}/use`, { use: e.target.value });
  q("#seqLock").onclick = () => post(`/seg/${encodeURIComponent(curSeg().id)}/lock`, { lock: !curSeg().lock });
  q("#seqMerge").onclick = () => { const k = D.segments.indexOf(curSeg()); const nx = D.segments[k + 1]; if (nx) commit(snap(), starts().filter((x) => x !== nx.start)); };
  // 拖切点：拖的时候只改本地显示、视频跟着跳到这一帧；松手才提交
  q("#seqCuts").addEventListener("pointerdown", (e) => {
    const h = e.target.closest(".sq-cut");
    if (!h || !D) return;
    e.preventDefault();
    const k = +h.dataset.k, old = snap(), a = D.segments[k - 1], b = D.segments[k];
    const r = q("#seqCuts").getBoundingClientRect(), f0 = b.start;
    let f = f0;
    video.pause(); h.classList.add("drag");
    const move = (ev) => {
      f = Math.min(b.end, Math.max(a.start + 1, Math.round((ev.clientX - r.left) / r.width * D.n)));
      a.end = f - 1; b.start = f;
      q(`#seqSegs [data-gid="${a.id}"]`).style.cssText = segPos(a);
      q(`#seqSegs [data-gid="${b.id}"]`).style.cssText = segPos(b);
      h.style.left = pct(f);
      video.currentTime = (f + 0.5) / fps(); head();
    };
    const up = () => {
      removeEventListener("pointermove", move); removeEventListener("pointerup", up);
      h.classList.remove("drag");
      if (f !== f0) commit(old, starts()); else render();
    };
    addEventListener("pointermove", move); addEventListener("pointerup", up);
  });
  // 退出分段页：回到刚才那一段（Esc），或关掉这个长片段的标签
  const goBack = () => { if (!q("#seqBack").hidden) openSegment(from); };
  q("#seqBack").onclick = goBack;
  q("#seqClose").onclick = () => window.uiProjectTabs?.close("seq:" + D.id);
  q("#seqExport").onclick = async () => {
    try { await api(`/api/s/${encodeURIComponent(D.id)}/export`, { method: "POST" }); setLog(T("导出全片中…")); watchJob(); }
    catch (e) { setLog(e.message, true); }
  };
  q("#seqReveal").onclick = () => api(`/api/s/${encodeURIComponent(D.id)}/reveal`, { method: "POST" }).catch((e) => setLog(e.message, true));

  // 分段页开着：键盘只给它（不让空格 / Ctrl+Z / M 之类作用到下面藏着的项目）
  addEventListener("keydown", (e) => {
    if (view.hidden || !D || e.target.closest?.("input, select, textarea")) return;
    if (!q(".ui-dialog:not([hidden])") && !q("#gallery:not([hidden])")) {
      if (e.key === " ") { e.preventDefault(); video.paused ? video.play() : video.pause(); }
      else if (e.key === "s" || e.key === "S") { e.preventDefault(); cutHere(); }
      else if (e.key === "Escape") { e.preventDefault(); goBack(); }
      else if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); video.pause(); video.currentTime = Math.max(0, video.currentTime + (e.key === "ArrowLeft" ? -1 : 1) / fps()); head(); }
    }
    if (!(e.ctrlKey && (e.key === "o" || e.key === "O"))) e.stopImmediatePropagation();
  }, true);

  // 后台任务收尾（05-project.js jobFinished 调）：导出完 / 剪好一段时刷新分段页
  window.uiSeqJobDone = (last) => { if (!view.hidden && D) load(D.id).catch(() => {}); };

  // 左边栏最上面的段号（2026-11-15 用户定：代替编辑区顶上那一行）：只在长片段的某一段里出现；
  // 每段一个小方块（绿 = 已生成，蓝框 = 正在编辑的这一段，灰 = 还没动），点一下切过去；鼠标停上去显示长片段名和第几段
  const rail = q("#segRail"), railList = q("#segRailList");
  async function syncRail(d) {
    rail.hidden = !(d && d.seq);
    if (rail.hidden) return;
    try {
      const s = await api(`/api/s/${encodeURIComponent(d.seq.id)}`);
      if (S.d !== d) return;
      railList.innerHTML = s.segments.map((g, k) => {
        const opened = g.id !== d.seq.seg && g.pid && (window.uiProjectTabs?.list() || []).some((t) => t.pid === g.pid);
        const st = g.id === d.seq.seg ? "cur" : (g.pid ? (g.results.length ? "done" : "edit") : "orig") + (opened ? " opened" : "");
        const sec = ((g.end - g.start + 1) / s.fps).toFixed(1), n = s.segments.length;
        const tip = (EN ? `${s.name} · segment ${k + 1} of ${n}\nframes ${g.start}–${g.end} · ${sec} s` + (g.pid ? ` · ${g.results.length} results` : " · not started")
                        : `${s.name} · 第 ${k + 1} 段 / 共 ${n} 段\n第 ${g.start}–${g.end} 帧 · ${sec} 秒` + (g.pid ? ` · ${g.results.length} 条结果` : " · 还没开始编辑"));
        const tip2 = opened ? tip + (EN ? "\nAlready open in a tab above" : "\n已经在上面的标签里打开了") : tip;
        return `<button type="button" class="seg-chip ${st}" data-gid="${esc(g.id)}" title="${esc(tip2)}" aria-label="${esc(EN ? `Segment ${k + 1}` : `第 ${k + 1} 段`)}"${opened ? " disabled" : ""}>${k + 1}</button>`;
      }).join("");
      q("#segRailBack").title = T`回到「${s.name}」的分段页`;
      rail._seq = s;
      railList.querySelector(".cur")?.scrollIntoView({ block: "nearest" });
    } catch { rail.hidden = true; }
  }
  const prev = window.uiProjectChanged;
  window.uiProjectChanged = (d) => { if (prev) prev(d); syncRail(d); };   // 分段页只在明确切走时关（gotoProject / 项目标签），后台刷新项目不关
  document.addEventListener("vps-tabs", () => { if (S.d && S.d.seq) syncRail(S.d); });   // 开 / 关了标签：灰掉 / 恢复对应的段
  q("#segRailBack").onclick = () => { if (S.d && S.d.seq) window.uiProjectTabs?.toSeq(S.d.id, S.d.seq.id, S.d.seq.seg); };
  railList.addEventListener("click", (e) => {
    const b = e.target.closest("[data-gid]");
    if (!b || !rail._seq || b.classList.contains("cur") || b.disabled) return;
    D = rail._seq;
    openSegment(b.dataset.gid);
  });
})();
