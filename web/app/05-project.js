// 视频 PS 前端 5/9：视频版本、背景全景图标签、打开视频。
// 9 个文件是同一个全局作用域的普通脚本，按 index.html 里的顺序加载（原来是一个 app.js）。
// ---------- 版本 ----------
// 每个版本一行：第 0 帧缩略图、名字（当前的带「当前」）、「N 图层 · N 视频 · N 图」；版本号 / 来源 / 时间在鼠标停留提示里。
// 点一行 = 切到这个版本；双击名字 = 改名；行尾 ⋯ = 改名 / 复制这个版本 / 在资源管理器中显示 / 删除（原视频不能删）；标题右边 ＋ = 新建
function renderVersions() {
  const nObj = (vid) => S.d.objects.filter((o) => o.version === vid && !o.shape).length;
  const nVid = (vid) => (S.d.results || []).filter((r) => r.base === vid).length;
  const nImg = (vid) => (S.d.images || []).filter((it) => it.version === vid && it.kind !== "bg").reduce((s, it) => s + (it.cands || []).length, 0);
  const label = (id) => (S.d.versions.find((x) => x.id === id) || {}).label || id;
  $("#versions").innerHTML = S.d.versions.map((v) => {
    const cur = v.id === S.d.current;
    const from = !v.parent ? T("原视频") : (v.op && v.op.type === "fresh" ? T("从原视频新建") : T("复制自 ") + label(v.parent));
    const tip = [v.id, from, v.time].filter(Boolean).join(" · ") + (cur ? T("（当前正在编辑）") : T("（点一下切到这个版本）"));
    const stats = [nObj(v.id) + T(" 图层"), nVid(v.id) + T(" 视频"), nImg(v.id) + T(" 图")].join(" · ");
    return T`<div class="ver ${cur ? "cur" : ""}" data-id="${esc(v.id)}" title="${esc(tip)}">
      <img class="ver-thumb" src="${frameUrl(v.id, 0)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
      <span class="ver-txt"><b class="ver-name" title="双击改名">${esc(v.id === "v0" && v.label === "原片" ? T("原视频") : v.label)}</b>${cur ? T('<em class="ver-cur">当前</em>') : ""}<small>${esc(stats)}</small></span>
      <button class="ver-act ver-more" data-refmenu="ver" title="更多操作" aria-label="更多操作">${IC("more")}</button></div>`;
  }).reverse().join("");
}
async function newVersion(body, what) {                            // body：{} = 复制当前；{ from } = 复制某个版本；{ fresh: true } = 从原视频新建
  if (!S.pid) return;
  try {
    setProject(await api(`/api/p/${S.pid}/versions`, { json: body }), true);
    setLog(T`已${what}新版本「${S.d.versions.find((v) => v.id === S.d.current)?.label || S.d.current}」，现在编辑的是它`);
  } catch (err) { setLog(err.message, true); }
}
async function renameVersion(id) {
  const old = S.d.versions.find((x) => x.id === id);
  const label = await uiPrompt(T("版本名字"), old ? old.label : "");
  if (!label || !label.trim() || label.trim() === old?.label) return;
  try { setProject(await api(`/api/p/${S.pid}/versions/${encodeURIComponent(id)}`, { json: { label: label.trim() } }), false); setLog(T("版本已改名")); }
  catch (err) { setLog(err.message, true); }
}
$("#verAdd").dataset.refmenu = "ver:add";
$("#verAdd").onclick = () => openRefMenu($("#verAdd"), [
  { label: T("复制当前版本"), hint: T("同一段视频，图层和编辑复制一份"), run: () => newVersion({}, T("复制成")) },
  { label: T("从原视频新建"), hint: T("原视频画面，不带图层"), run: () => newVersion({ fresh: true }, T("从原视频新建")) },
]);
$("#versions").addEventListener("dblclick", (e) => {
  const v = e.target.closest(".ver");
  if (v && e.target.closest(".ver-name")) renameVersion(v.dataset.id);
});
$("#versions").addEventListener("click", async (e) => {
  const v = e.target.closest(".ver");
  if (v && e.target.closest(".ver-more")) {
    const id = v.dataset.id;
    return openRefMenu(e.target.closest(".ver-more"), [
      { label: T("改名…"), run: () => renameVersion(id) },
      { label: T("复制这个版本"), hint: T("图层和编辑复制一份"), run: () => newVersion({ from: id }, T("复制成")) },
      { label: T("在资源管理器中显示"), hint: T("这个版本的视频"), run: () => revealOutput({ kind: "version", name: id }) },
      { label: T("删除这个版本"), hint: id === "v0" ? T("原视频不能删") : "", disabled: id === "v0", run: () => deleteVersion(id) },
    ]);
  }
  if (v && v.dataset.id !== S.d.current && !e.target.closest(".ver-more")) setProject(await api(`/api/p/${S.pid}/current`, { json: { version: v.dataset.id } }), true);
});
async function deleteVersion(id) {
  const msg = S.d.layout === 2
    ? T('删除此视频版本？整个版本文件夹（视频、结果、中间文件）和它独占的图层文件都会被删除，首帧 / 尾帧 / 资料卡标签和视频结果也从列表里去掉，不能撤销。原视频保留；有别的版本是从它复制来的时会阻止删除。')
    : T('删除此视频版本？它的视频、预览帧、独占图层文件会被删除，它的首帧 / 尾帧 / 资料卡标签和视频结果也会从列表里去掉（生成的文件留在项目文件夹里），不能撤销。原视频保留；有别的版本是从它复制来的时会阻止删除。');
  if (!(await uiConfirm(msg, { ok: T("删除"), danger: true }))) return;
  try {
    if (S.playing) togglePlay();
    const d = await api(`/api/p/${S.pid}/versions/${encodeURIComponent(id)}`, { method: 'DELETE' });
    S.cache = {}; S.fi = null; S.fiVid = null; S.undo = []; S.redo = [];
    zoneImgs.clear(); zoneDecoded.clear();
    setProject(d, true);
    setLog(T('视频版本及对应文件已删除'));
  } catch (err) { setLog(err.message, true); }
}

// 合成一共几步、现在第几步：按服务端日志里的标记认（日志留在下面当详细信息）
const COMPOSE_STEPS = [
  [T("分区：算每一帧哪里是空区 / 重绘区"), /^① 合成/],
  [T("预填：原位置空出来的地方先铺上背景"), /^② /],
  [T("写描述：Qwen3-VL 看图写背景和人物的描述"), /Qwen3-VL 自动描述/],
  [T("H3 生成视频（最久，约 10 分钟）"), /^③ H3/],
];
function showSteps(j) {
  const ol = $("#stepList"), log = j.log || [];
  if (!log.some((l) => COMPOSE_STEPS[0][1].test(l))) { ol.hidden = true; return; }
  let cur = -1;
  log.forEach((l) => COMPOSE_STEPS.forEach(([, re], k) => { if (re.test(l)) cur = Math.max(cur, k); }));
  const seen = COMPOSE_STEPS.map(([, re]) => log.some((l) => re.test(l)));
  const finished = !j.busy && !j.error;
  ol.innerHTML = T('<li class="head" style="color:var(--text)">合成视频：</li>') + COMPOSE_STEPS.map(([name], k) => {
    const cls = finished ? (seen[k] ? "done" : "skip") : k < cur ? (seen[k] ? "done" : "skip") : k === cur ? (j.error ? "todo" : "now") : "todo";
    return `<li class="${cls}">${esc(name)}</li>`;
  }).join("") + (finished ? T('<li class="done">完成：结果放进了上面的「结果」标签</li>') : j.error ? T('<li style="color:var(--danger)">出错了，详情看下面</li>') : "");
  ol.hidden = false;
}

function showProgress(j) {
  showSteps(j);                                         // 状态面板的进度条：阶段 + 第几步 + 剩余时间
  const p = j.busy && j.progress, box = $("#progBox");
  box.hidden = !j.busy;
  if (!j.busy) return;
  const fill = $("#progFill"), total = j.started ? fmtSecs(j.now - j.started) : "";
  if (p && p.max > 1) {
    fill.classList.remove("indet");
    fill.style.width = (100 * p.value / p.max).toFixed(1) + "%";
    $("#progText").textContent = `${p.stage} ${p.value}/${p.max}` + (p.eta != null ? T` · 这一步还剩约 ${fmtSecs(p.eta)}` : "") + T` · 共用时 ${total}`;
  } else {
    fill.classList.add("indet");
    $("#progText").textContent = (p ? (p.queue ? T`排队中：前面还有 ${p.queue} 个任务（ComfyUI 上的其他任务）` : p.stage) : T("处理中")) + T` · 共用时 ${total}`;
  }
}
const fmtSecs = (t) => { t = Math.max(0, Math.round(t)); return t >= 60 ? T`${Math.floor(t / 60)} 分 ${String(t % 60).padStart(2, "0")} 秒` : T`${t} 秒`; };

// 跟着后台任务走。任务会排队：一个跑完马上接下一个（busy 一直是 true），所以靠 j.last（刚跑完的那个）逐个收尾，全部跑完才退出。
// 同时只开一个轮询：任务进行中再提交（进了队列）直接复用正在跑的这个。按钮不再禁用，在跑的时候点 = 排队。
let watching = null;
// 轮询出错（后端重启、网络断）不往外抛：调用的地方很多都没 await / catch。错误写进日志，「停止」按钮收起
function watchJob() {
  return watching || (watching = watchLoop().catch((e) => { setLog(e.message, true); }).finally(() => { watching = null; $("#stopBtn").hidden = true; }));
}
async function watchLoop() {
  let tick = 0, handled;
  const stop = $("#stopBtn");                                      // 任务在跑时顶栏出现「停止」：只停正在跑的，排着的照常接着跑
  const stopLabel = stop.querySelector(".stop-label");
  stop.hidden = false; stop.disabled = false; if (stopLabel) stopLabel.textContent = T("停止");
  stop.onclick = async () => {
    stop.disabled = true; if (stopLabel) stopLabel.textContent = T("正在停止…");
    try { await api("/api/job/cancel", { method: "POST" }); } catch (e) { setLog(e.message, true); }
  };
  for (let seen = false, fails = 0; ; ) {
    let j;
    try { j = await api("/api/job"); fails = 0; }
    catch (e) {                                                    // 偶尔一次没连上不算：连续 5 次（约 5 秒）才放弃
      if (++fails >= 5) throw e;
      await new Promise((r) => setTimeout(r, 1000));
      continue;
    }
    window.taskWindow?.update(j);
    if (!seen && j.busy) handled = j.last?.started;              // 开始轮询前就跑完的旧任务，以前收过尾了
    seen = true;
    if (j.last && j.last.started !== handled) {                   // 有任务刚跑完：收尾（刷新项目、打开结果）
      handled = j.last.started;
      await jobFinished(j.last);
      if (j.busy) { stop.disabled = false; if (stopLabel) stopLabel.textContent = T("停止"); }   // 下一个排队的接着跑
    }
    if (!j.busy) break;
    setLog(j.log.join("\n") || T("运行中…"), false, true);
    showProgress(j);
    if (S.genWait && (tick = (tick || 0) + 1) % 2 === 0) {        // 图片候选：每 2 秒看一下出了几张，出一张显示一张
      const pid = S.pid;                                          // 等回来时换了项目：别把旧项目的图片列表写进新项目
      try { const d = await api(`/api/p/${pid}`); if (S.pid === pid && S.d) { S.d.images = d.images; const it = curAny(); if (it) renderRefTab(it); } } catch {}
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  stop.hidden = true;
}
async function jobFinished(last) {
  showProgress({ busy: false, error: last.error, log: last.log || [] });   // 进度条收起，「合成视频」步骤显示最终状态
  setLog(last.error ? T("出错：") + last.error : (last.log || []).join("\n"), !!last.error, true);
  if (last.cancelled) setLog(T("已停止（这次没生成结果）"));
  window.uiDoneToast?.(last);                                      // 生成完成：右下角提示，「查看」直接跳到结果（别的项目的也行）
  if (!last.error && last.open_seq) { await loadProjectList(); return window.uiOpenSeq?.(last.open_seq); }   // 长片段导入完：打开分段页
  if (!last.error && last.open_segment) { await loadProjectList(); return window.uiOpenSegment?.(last.open_segment); }   // 剪好一段：打开这一段
  if (last.export_file && !last.error && last.project === S.pid) expDownload(last.export_file);   // 「导出视频生成素材」准备好了：下载
  window.uiSeqJobDone?.(last);                                     // 分段页开着：刷新（导出完等）
  if (window.uiSeqOpen?.()) return;                                // 分段页开着：不去刷新下面藏着的项目
  if (last.open_project && !last.error) {                          // 扩图合成：结果是一个新项目（尺寸变了），直接打开
    await loadProjectList();
    setLog(T`扩图结果建成了新项目，已打开（原项目还在，项目列表里切回去）`);
    return setProject(await api(`/api/p/${last.open_project}`));
  }
  if (!S.pid || (last.project && last.project !== S.pid)) return;  // 别的项目的任务：不动现在打开的项目
  setProject(await api(`/api/p/${S.pid}`), true);
  if (last.open_result && !last.error) { resShown = null; showTab("res:" + last.open_result); }   // 合成完：直接切到结果标签播放
}

$("#segBtn").onclick = async () => {
  const text = $("#segText").value.trim();
  if (!S.d || !text) return;
  try {
    await api(`/api/p/${S.pid}/segment`, { json: { text, start: S.start, end: S.end, matte: segMatteOn() } });
    watchJob();
  } catch (e) { setLog(e.message, true); }
};
$("#segText").addEventListener("keydown", (e) => e.key === "Enter" && $("#segBtn").click());
// MatAnyone2 柔边：勾选记在浏览器里（所有项目共用，默认勾）；ComfyUI 上没装就变灰；点选用 MatAnyone2 跟踪时本来就是柔边
const segMatteOn = () => $("#segMatte").checked && !$("#segMatte").disabled;
try { if (localStorage.getItem("vps_seg_matte") === "0") $("#segMatte").checked = false; } catch {}
$("#segMatte").onchange = () => { try { localStorage.setItem("vps_seg_matte", $("#segMatte").checked ? "1" : "0"); } catch {} };
function syncSegMatte() {
  const viaTrack = !$("#segPickBox").hidden && $("#pickTracker").value === "matanyone";
  $(".seg-matte").classList.toggle("dim", viaTrack);
  $(".seg-matte").title = viaTrack ? T("点选用 MatAnyone2 跟踪时本来就是柔边") : T("识别后用 MatAnyone2 抠出头发丝、运动模糊等半透明边缘（多花一点时间）。不勾 = 只用 SAM 硬边");
}
$("#pickTracker").addEventListener("change", syncSegMatte);
api("/api/has_nodes?names=MatAnyone2").then((r) => {
  if (r.MatAnyone2) return;
  $("#segMatte").disabled = true;
  $("#segMatteTxt").textContent = T("MatAnyone2 柔边（ComfyUI 上没装）");
}).catch(() => {});

// ---------- 点选识别：画面上点选中 / 排除点 → 确定后（问要不要往前跟踪）SAM 3.1 识别 + 跟踪 ----------
// S.pick = { frame, pos: [[x, y]], neg: [[x, y]] }（原视频像素）；点都在同一帧上，换帧就清掉
// 点选只在「主体抠图识别」面板看得见、而且在视频编辑页时生效；切到右边别的面板组 = 退出点选（点清掉，uiLeaveGroup）
const pickOn = () => !!S.pick && S.tab === "video" && !!$("#segPickBox").offsetParent;
function setSegMode(mode) {
  const pick = mode === "pick";
  $("#segTextRow").hidden = pick;
  $("#segPickBox").hidden = !pick;
  $$('#segMode input').forEach((r) => { r.checked = r.value === mode; });
  S.pick = pick && S.d ? { frame: S.i, pos: [], neg: [] } : null;
  if (pick && S.playing) togglePlay();
  syncSegMatte();
  if (pick) api("/api/has_nodes?names=MatAnyone2").then((r) => {   // 4090 没装 MatAnyone2：这一项变灰
    const op = $('#pickTracker option[value="matanyone"]');
    op.disabled = !r.MatAnyone2;
    op.textContent = r.MatAnyone2 ? "MatAnyone2" : T("MatAnyone2（ComfyUI 上没装）");
    if (op.disabled && $("#pickTracker").value === "matanyone") $("#pickTracker").value = "sam";
  }).catch(() => {});
  syncPick();
  drawGizmo();
}
$("#segMode").addEventListener("change", (e) => setSegMode(e.target.value));
function syncPick() {
  const P = S.pick;
  $("#pickGo").disabled = !P || !P.pos.length;
  $("#pickClear").disabled = !P || !(P.pos.length + P.neg.length);
  $("#pickInfo").textContent = !P || !(P.pos.length + P.neg.length) ? T("还没点")
    : uiLanguage === "en" ? `Frame ${P.frame} · ${P.pos.length} selected · ${P.neg.length} excluded`
    : `第 ${P.frame} 帧 · 选中 ${P.pos.length} · 排除 ${P.neg.length}`;
}
function pickDown(e) {                                             // 画布上点一下：左键选中，右键 / Alt 排除
  e.preventDefault();
  if (S.i !== S.pick.frame) S.pick = { frame: S.i, pos: [], neg: [] };   // 换了帧：点重新开始
  const [x, y] = toVideo(e);
  if (x < 0 || y < 0 || x >= S.d.w || y >= S.d.h) return;
  (e.button === 2 || e.altKey ? S.pick.neg : S.pick.pos).push([Math.round(x), Math.round(y)]);
  syncPick(); drawGizmo();
}
$("#stage").addEventListener("contextmenu", (e) => { if (pickOn()) e.preventDefault(); });
function pickSvg(k) {                                              // drawGizmo 里画点（绿 = 选中，红 = 排除）
  const P = S.pick;
  if (!P || (S.fi ?? S.i) !== P.frame) return "";
  const dot = ([x, y], c) => `<g class="pick-pt ${c}" transform="translate(${x * k},${y * k})"><circle r="7"/><path d="${c === "pos" ? "M-3.5 0h7M0 -3.5v7" : "M-3.5 0h7"}"/></g>`;
  return P.pos.map((q) => dot(q, "pos")).join("") + P.neg.map((q) => dot(q, "neg")).join("");
}
$("#pickClear").onclick = () => { if (S.pick) { S.pick.pos = []; S.pick.neg = []; syncPick(); drawGizmo(); } };
$("#pickGo").onclick = () => {
  const P = S.pick;
  if (!S.d || !P || !P.pos.length) return;
  const run = async (back) => {
    const xy = (L) => L.map(([x, y]) => ({ x, y }));
    try {
      await api(`/api/p/${S.pid}/segment_points`, { json: { frame: P.frame, pos: xy(P.pos), neg: xy(P.neg), start: S.start, end: S.end,
                                                              back, name: $("#pickName").value.trim(), tracker: $("#pickTracker").value, matte: segMatteOn() } });
      S.pick = { frame: S.i, pos: [], neg: [] }; $("#pickName").value = ""; syncPick(); drawGizmo();
      watchJob();
    } catch (err) { setLog(err.message, true); }
  };
  if (P.frame <= S.start) return run(false);                       // 点在范围第一帧：没有「往前」
  $("#pickGo").dataset.refmenu = "pick";
  openRefMenu($("#pickGo"), [                                      // 运行前问：要不要往前跟踪
    { label: uiLanguage === "en" ? `Track forward only (frames ${P.frame}–${S.end})` : `只往后跟踪（第 ${P.frame}–${S.end} 帧）`, hint: T("从这一帧开始"), run: () => run(false) },
    { label: uiLanguage === "en" ? `Track both ways (frames ${S.start}–${S.end})` : `前后都跟踪（第 ${S.start}–${S.end} 帧）`, hint: T("多跑一次，稍慢"), run: () => run(true) },
  ]);
};

// ---------- 背景全景图 ----------
// 背景图是按旧画幅提取的（之后画幅 / 裁切黑边又改了）：画幅里多出来的地方没有背景，要重新提取
function panoFrameStale(pn) {
  if (!pn) return false;
  const f = getFrame(), c = f.crop;
  const rect = pn.rect || [0, 0, S.d.w, S.d.h];
  return rect.join() !== [f.x, f.y, f.w, f.h].join() || JSON.stringify(pn.crop || null) !== JSON.stringify(c && (c.t || c.b) ? [+c.t, S.d.h - c.b] : null);
}
function renderPano() {
  const pn = S.d && S.d.panorama;
  const promptBox = $("#panoPrompt");
  if (S.pid && promptBox.dataset.project !== S.pid) {
    promptBox.dataset.project = S.pid;
    promptBox.value = "Fill the empty areas with the background so they continue the surrounding scene naturally, as if nothing had ever been there. Keep everything else exactly as it is. Do not add any people.";
  }
  $("#panoPeBox").hidden = !(pn && pn.pe_prompt);
  if (pn && pn.pe_prompt) $("#panoPeText").textContent = pn.pe_prompt;
  const layers = $("#panoObjects");
  const objects = objs();
  const key = JSON.stringify([S.pid, objects.map(o => [o.id,o.name])]);
  if (layers.dataset.key !== key) {
    layers.dataset.key = key;
    layers.innerHTML = objects.map(o => `<label><input type="checkbox" value="${esc(o.id)}" checked> ${esc(o.name)}</label>`).join("");
  }
  $("#panoGenerateStep").disabled = !pn || $("#panoBtn").disabled;
  $("#panoFillBtn").disabled = !pn;                                // 第二步要先有第一步的中间层
  $("#panoFrameStale").hidden = !panoFrameStale(pn);
  if (!pn) return;
  const selector = $("#panoExtract");
  selector.innerHTML = Object.entries(pn.extracts || {}).map(([name,label], i) =>
    `<option value="${esc(name)}">#${i+1}　${esc(label)}</option>`).reverse().join("") || T('<option value="">当前提取结果</option>');
  selector.value = pn.selected_extract || "";
  if (curAny() && curAny().kind === "bg") renderBgMain();
}
$("#panoExtract").onchange = async e => {
  const name = e.target.value;
  try { S.d.panorama = await api(`/api/p/${S.pid}/panorama/choose_extract`, {json:{name}}); }
  catch (err) { setLog(err.message,true); }
  renderPano();
};
$("#panoBtn").onclick = async () => {
  if (!S.d) return;
  $("#panoBtn").disabled = true;
  $("#panoGenerateStep").disabled = true;
  try {
    await api(`/api/p/${S.pid}/panorama`, { json: { fill: false, mode: $("#panoMode").value, ref: S.i, objects: $$("#panoObjects input:checked").map(x => x.value) } });
    await watchJob();
    setLog(T("提取结果已保存，可在第二步选择提取结果"));
  } catch (e) { setLog(e.message, true); }
  $("#panoBtn").disabled = false;
  renderPano();

};
$("#panoRect").oninput = (e) => { $("#panoRectVal").textContent = +e.target.value ? T("矩形 ×") + e.target.value : T("轮廓"); };
$("#panoFillBtn").onclick = async () => {                          // 第二步：Qwen 补中间层
  if (!S.d) return;
  const viaApi = document.body.classList.contains("img-api");     // API：只发底图 + 提示词原样（不用 PE / seed）
  if (viaApi && !$("#panoPrompt").value.trim()) return setLog(T("先写提示词"), true);
  try {
    await api(`/api/p/${S.pid}/panorama/fill`, { json: { api: viaApi,
                                                            prompt: $("#panoPrompt").value.trim(), seed: $("#imgSeed").value || 42,
                                                            extract: $("#panoExtract").value, pe: $("#panoPe").checked, rect: +$("#panoRect").value } });
    $("#panoFillBtn").disabled = true;
    await watchJob();
  } catch (e) { $("#panoFillBtn").disabled = false; renderPano(); return setLog(e.message, true); }   // 出错：留在这里，不跳去结果
  $("#panoFillBtn").disabled = false;
  renderPano();
  const bg = imgTabs().find((t) => t.kind === "bg");
  if (bg && S.d.panorama) showTab("ires:" + bg.id);
};

// ---------- 打开 ----------
$("#openBtn").onclick = async () => {
  const button = $("#openBtn");
  button.disabled = true;
  try {
    const path = $("#videoPath").value.trim() || (await api("/api/local/video-picker", { method: "POST" })).path;
    if (!path) return;
    const info = await api("/api/probe_video", { json: { path } });   // 先看多长：长视频问要不要分段（ui/sequence.js）
    if (info.duration > info.ask_s && await window.uiAskSplit?.(path, info)) return;
    setLog(T("读取视频、生成预览帧…"));
    const d = await api("/api/projects", { json: { video_path: path } });
    setProject(d); $("#openDlg").hidden = true; loadProjectList();
    setLog(d.source_size ? T`已打开：原视频 ${d.source_size[0]}×${d.source_size[1]}，已缩到 ${d.w}×${d.h} 处理（原视频留在项目文件夹；设置 → 性能里可以改）` : T("已打开"));
  }
  catch (e) { setLog(e.message, true); }
  finally { button.disabled = false; }
};
$("#projectSel").onchange = async (e) => {                         // 选了项目：打开它并关掉「打开」对话框
  if (!e.target.value) return;
  try { setProject(await api(`/api/p/${e.target.value}`)); $("#openDlg").hidden = true; }
  catch (err) { setLog(err.message, true); }
};

// ---------- 历史：项目的操作记录（操作记录log.jsonl），改图层 / 改画框可以恢复到那一步之前 ----------
let histItems = [], histPid = null;
const HIST_ACT = { layers: "改图层", frame: "改画框", merge: "合并", delete_layer: "删除图层", segment: "识别", generate_video: "生成视频" };
function histText(e) {
  const names = (e.changes || []).map((c) => c.name).filter(Boolean);
  if (e.act === "layers") return (e.label || T("改图层")) + (names.length ? " · " + names.join("、") : "");
  if (e.act === "merge") return T("合并") + " · " + (e.layers || []).map((l) => l.name).join(" + ");
  if (e.act === "delete_layer") return T("删除图层") + " · " + (e.layer?.name || "");
  if (e.act === "segment") return T("识别") + " · " + (e.text || "");
  return T(HIST_ACT[e.act] || e.act);
}
function histCan(e) {                                              // 能不能恢复：改图层（图层都还在）/ 改画框
  if (e.act === "frame") return !e.version || e.version === S.d.current;   // 别的版本的画框不套到这个版本上
  if (e.act !== "layers" || !e.changes?.length) return false;
  return e.changes.every((c) => obj(c.id));                      // 当前版本里还在的图层才能恢复
}
async function renderHistory() {
  if (!S.pid || $("#historyPanel").closest(".pgroup").hidden) return;
  try {
    const r = await api(`/api/p/${S.pid}/history`);
    histItems = r.items; histPid = S.pid;
    $("#histList").innerHTML = (r.on ? "" : `<div class="sub">${T("设置里「记录操作」关着：新的操作不会记进来")}</div>`) +
      (histItems.map((e, k) => {
        const can = histCan(e), other = e.version && e.version !== S.d.current, gone = e.act === "layers" && !can && !other;
        return `<div class="hist-row"><div class="hist-txt"><b>${esc(histText(e))}</b><small>${esc((e.t || "").slice(5, 16))}${e.version ? " · " + esc(e.version) : ""}${other && (e.act === "layers" || e.act === "frame") ? " · " + T("别的版本：切到那个版本才能恢复") : gone ? " · " + T("图层已不在") : ""}</small></div>` +
          (can ? `<button class="ghostbtn" data-hist="${k}" title="${T("把这一步改过的设置恢复成改之前的样子（恢复也算一步，能撤销）")}">${T("恢复到这步之前")}</button>` : "") + "</div>";
      }).join("") || `<div class="sub">${T("还没有记录")}</div>`);
  } catch (err) { $("#histList").innerHTML = `<div class="sub bad">${esc(err.message)}</div>`; }
}
$("#histList").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-hist]");
  if (!b) return;
  const e = histItems[+b.dataset.hist];
  if (!e || histPid !== S.pid) return renderHistory();             // 列表是别的项目的：重新读
  if (e.act === "frame") {
    const f = e.before || { x: 0, y: 0, w: S.d.w, h: S.d.h, ratio: "free" };
    commitFrame({ ...f, crop: f.crop || null }, T("恢复画框"));
  } else {
    const before = Object.fromEntries(e.changes.map((c) => [c.id, c.before]));
    const os = e.changes.map((c) => obj(c.id)).filter(Boolean);
    commit(os, (o) => Object.assign(o, JSON.parse(JSON.stringify(before[o.id]))), T("恢复：") + (e.label || T("改图层")));
    renderLayers(); renderProps();
  }
  setLog(T("已恢复：") + histText(e));
  setTimeout(renderHistory, 600);
});
$("#histRefresh").onclick = () => renderHistory();
document.querySelector('#panelRail [data-group="history"]').addEventListener("click", () => setTimeout(renderHistory, 0));

// ---------- 导出：打包下载交给模型的素材（zip，后端 exporter.py） ----------
let expLastFile = null;
function expDownload(file) {                                       // 浏览器下载 zip（不是 .mp4 地址，IDM 不会误拦播放）
  expLastFile = file;
  const a = document.createElement("a");
  a.href = `/api/p/${S.pid}/export/download?file=${encodeURIComponent(file)}`;
  a.download = file;
  document.body.append(a); a.click(); a.remove();
  $("#expLast").hidden = false;
  $("#expLastName").textContent = file;
  setLog(T("已导出：") + file);
}
function setIconText(btn, text) {                                // 换按钮文字但保留 data-icon 填进去的图标
  btn.innerHTML = (btn.dataset.icon ? IC(btn.dataset.icon) : "") + esc(text);
}
function renderExport() {
  const t = document.body.dataset.tab;
  const kind = t === "video" ? "video" : (t === "still" || t === "card") ? "image" : t === "bg" ? "bg" : t === "ires" ? "ires" : t === "res" ? "res" : "";
  document.querySelectorAll("#exportPanel .exp-sec").forEach((x) => (x.hidden = x.dataset.exp !== kind));
  const ir = typeof curIres === "function" && curIres();
  const isBg = !!(ir && ir.kind === "bg");                         // 背景图的候选在 panorama 里（bgCards），不在标签的 cands
  const hasCand = isBg ? bgCards().length > 0 : !!(ir && ir.cands && ir.cands.length);
  if (kind === "ires") {
    $("#expCandFile").disabled = !hasCand;                         // 直接下载背景图也可以；打包（附提示词）背景图不支持
    $("#expCand").disabled = $("#expCandAll").disabled = !hasCand || isBg;
  }
  if (kind === "ires" && ir) {
    setIconText($("#expCandFile"), ir.pick || (isBg && (S.d.panorama || {}).use) ? T("下载选用的这张") : T("下载最新的一张"));
    setIconText($("#expCand"), ir.pick ? T("打包选用的这张") : T("打包最新的一张"));
  }
  if (kind === "res") $("#expResultFile").disabled = $("#expResult").disabled = !(typeof curRes === "function" && curRes());
}
function expFile(url, name) {                                      // 直接下载一个结果文件（服务端给好文件名，地址不带 .mp4）
  const a = document.createElement("a");
  a.href = url; a.download = "";
  document.body.append(a); a.click(); a.remove();
  setLog(T("已开始下载：") + name);
}
$("#expCandFile").onclick = () => {
  const it = curIres();
  if (it && it.kind === "bg") {                                    // 背景图：下载选用的那张（服务端按 panorama.use 找，文件名 项目名_背景图_K.png）
    if (!bgCards().length) return setLog(T("还没有背景图"), true);
    return expFile(`/api/p/${S.pid}/export/file/cand/${it.id}`, T("背景图"));
  }
  if (!it || !it.cands?.length) return;
  const name = it.pick || it.cands[it.cands.length - 1];
  expFile(`/api/p/${S.pid}/export/file/cand/${it.id}?name=${encodeURIComponent(name)}`, T`候选 #${it.cands.indexOf(name) + 1}`);
};
$("#expResultFile").onclick = () => {
  const r = curRes();
  if (!r) return setLog(T("还没有视频结果"), true);
  expFile(`/api/p/${S.pid}/export/file/result/${encodeURIComponent(r.id)}`, (r.file || "").split("/").pop());
};
$("#expVideo").onclick = async () => {
  if (!S.d) return;
  try {
    await saveGenPrompt(); await flushLayerSaves();
    await api(`/api/p/${S.pid}/export/video`, { json: genOp() });
    setLog(T("正在准备视频生成素材（空区分析开着时要等看图模型，约 1 分钟）…"));
    watchJob();
  } catch (e) { setLog(e.message, true); }
};
$("#expImage").onclick = async () => {
  const it = curImg();
  if (!it) return;
  const apiMode = document.body.classList.contains("img-api");
  const prompt = apiMode ? $("#imgApiPrompt").value.trim() : [$("#imgDesc").value.trim(), $("#imgSys").value.trim()].filter(Boolean).join(" ");
  try {
    const r = await api(`/api/p/${S.pid}/export/image/${it.id}`, { json: { prompt, mode: it.mode === "inpaint" ? "inpaint" : "gen",
                                                                                 api: apiMode, mentions: mentionsOf(prompt, imgMentionItems(true)) } });
    expDownload(r.file);
  } catch (e) { setLog(e.message, true); }
};
async function expCands(all) {
  const it = curIres();
  if (!it) return;
  try { expDownload((await api(`/api/p/${S.pid}/export/cands/${it.id}`, { json: { all } })).file); } catch (e) { setLog(e.message, true); }
}
$("#expCand").onclick = () => expCands(false);
$("#expCandAll").onclick = () => expCands(true);
$("#expResult").onclick = async () => {
  const r = curRes();
  if (!r) return setLog(T("还没有视频结果"), true);
  try {
    const x = await api(`/api/p/${S.pid}/export/result/${r.id}`, { method: "POST" });
    expDownload(x.file);
    if (x.missing && x.missing.length) setLog(T("有些素材当时没留下来，zip 里的「缺少的文件.txt」写了是哪些"), true);
  } catch (e) { setLog(e.message, true); }
};
$("#expReveal").onclick = async () => {
  if (!expLastFile) return;
  try { await api(`/api/p/${S.pid}/export/reveal`, { json: { file: expLastFile } }); } catch (e) { setLog(e.message, true); }
};
new MutationObserver(renderExport).observe(document.body, { attributes: true, attributeFilter: ["data-tab"] });
document.querySelector('#panelRail [data-group="export"]').addEventListener("click", () => setTimeout(renderExport, 0));

