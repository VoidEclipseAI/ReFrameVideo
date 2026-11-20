/* 左边栏的画布工具（2026-11-15 用户定，PS 式「点在画面上会做什么」）。都是已有功能的固定入口：
   选择 / 移动（默认）· 抓手（S.tool = "hand"，04-interact.js 里左键平移）· 点选识别（05-project.js setSegMode）·
   画蒙版（07b-shape.js openShapeMenu）· 逐帧修整（07-refine.js startRefine）；视图：Alpha 视图（toggleMaskView）、
   和原视频对比（转调画面角落的 #cmpBtn）。按钮的高亮跟着实际状态走（syncTools）。 */
(() => {
  const q = (s) => document.querySelector(s);
  S.tool = "sel";

  function syncTools() {
    const pick = typeof pickOn === "function" && pickOn();
    const refine = !!S.refine, shape = !!S.shape;
    q("#toolSel").classList.toggle("on", S.tool === "sel" && !pick && !refine && !shape);
    q("#toolHand").classList.toggle("on", S.tool === "hand");
    q("#toolPick").classList.toggle("on", pick);
    q("#toolMask").classList.toggle("on", shape);
    q("#toolRefine").classList.toggle("on", refine);
    q("#toolAlpha").classList.toggle("on", !!S.maskView);
    q("#toolCmp").classList.toggle("on", !!S.cmp);
    q("#stage").classList.toggle("hand-tool", S.tool === "hand");
  }
  window.uiSyncTools = syncTools;

  function toSelect() {                                     // 回到选择工具：收起点选、抓手
    S.tool = "sel";
    if (S.pick && typeof setSegMode === "function") setSegMode("text");
    syncTools();
  }
  q("#toolSel").onclick = toSelect;
  q("#toolHand").onclick = () => { if (S.pick && typeof setSegMode === "function") setSegMode("text"); S.tool = S.tool === "hand" ? "sel" : "hand"; syncTools(); };
  q("#toolPick").onclick = () => {
    if (!S.d) return setLog(T("先打开一个项目"), true);
    if (S.tab !== "video") showTab("video");
    S.tool = "sel";
    if (S.pick) { setSegMode("text"); return syncTools(); }
    window.uiOpenGroup?.("layers");                          // 点选只在「主体抠图识别」面板开着时生效
    setSegMode("pick");
    setLog(T("点选识别：在画面上左键点主体、右键点要排除的地方，点好后在右边按「识别」"));
    syncTools();
  };
  q("#toolMask").onclick = (e) => {
    e.stopPropagation();                                     // 和右边栏的「＋」一样：这一下点击不算点在菜单外面
    if (!S.d) return setLog(T("先打开一个项目"), true);
    if (S.tab !== "video") showTab("video");
    S.tool = "sel";
    openShapeMenu(e.currentTarget);
    syncTools();
  };
  q("#toolRefine").onclick = () => {
    const os = selected().filter((o) => !o.shape);
    if (os.length !== 1 || imgEditing()) return setLog(T("逐帧修整：先在视频编辑里只选中一个主体"), true);
    S.tool = "sel";
    startRefine(os[0].id);
    syncTools();
  };
  q("#toolAlpha").onclick = () => { toggleMaskView(); syncTools(); };
  q("#toolCmp").onclick = () => { q("#cmpBtn").click(); syncTools(); };

  addEventListener("keydown", (e) => {                      // Esc：抓手 → 选择（别的模式各自有 Esc）
    if (e.key === "Escape" && S.tool === "hand" && !e.target.closest?.("input, textarea, select")) { S.tool = "sel"; syncTools(); }
  });
  document.addEventListener("click", () => setTimeout(syncTools, 0));   // 画面 / 面板里切模式后按钮跟上
  document.addEventListener("keyup", () => setTimeout(syncTools, 0));
})();
