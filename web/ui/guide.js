/* 新手教程：左边栏书本图标 → 分步弹窗（截图在 ui/tutorial/<语言>/，来自一个示例项目）。 */
(() => {
  const STEPS = [
    ["1_detect", "1. 识别主体", "点顶部的「＋」导入视频。在「图层」面板输入要识别的东西（英文，例如 person, cup），点「识别」，识别到的东西会变成图层。"],
    ["2_edit", "2. 编辑画面", "拖动图层改变位置和大小，在「画幅」里改比例或往外扩。棋盘格是空出来的地方，生成时由模型补画。"],
    ["3_mask", "3. 检查重绘范围", "按 M 查看哪里会重画：红色 = 补画，绿色 = 保留。需要时在「蒙版」面板调整。"],
    ["4_first_frame", "4. 生成首帧", "点页面栏的「＋」新建「首帧」，生成几张候选，在「图片结果」里选用最干净的一张。首帧决定成片的样子。"],
    ["5_generate", "5. 生成视频", "回到「视频编辑」，打开「生成」面板，确认参考图里有首帧，点「生成视频」。成片会出现在「视频结果」里。"],
  ];
  const $ = (s) => document.querySelector(s);
  const dlg = $("#guideDlg");
  const lang = uiLanguage === "en" ? "en" : "zh";
  let i = 0;

  $("#guideMore").href = `https://github.com/VoidEclipseAI/ReFrameVideo/blob/main/web/ui/tutorial-${lang}.md`;
  const dots = $("#guideDots");
  dots.innerHTML = STEPS.map((_, k) => `<i data-k="${k}"></i>`).join("");
  dots.onclick = (e) => { const k = e.target.dataset.k; if (k != null) show(+k); };

  function show(k) {
    i = Math.max(0, Math.min(STEPS.length - 1, k));
    const [img, title, text] = STEPS[i];
    $("#guideImg").src = `ui/tutorial/${lang}/${img}.jpg`;
    $("#guideImg").alt = T(title);
    $("#guideTitle").textContent = T(title);
    $("#guideText").textContent = T(text);
    $("#guideCount").textContent = `${i + 1} / ${STEPS.length}`;
    dots.querySelectorAll("i").forEach((d, k) => d.classList.toggle("on", k === i));
    $("#guidePrev").disabled = i === 0;
    $("#guideNext").textContent = T(i === STEPS.length - 1 ? "完成" : "下一步");
  }
  $("#guidePrev").onclick = () => show(i - 1);
  $("#guideNext").onclick = () => (i === STEPS.length - 1 ? (dlg.hidden = true) : show(i + 1));
  $("#toolGuide").onclick = () => { show(0); dlg.hidden = false; };
  document.addEventListener("keydown", (e) => {
    if (dlg.hidden) return;
    if (e.key === "ArrowRight") { show(i + 1); e.preventDefault(); e.stopPropagation(); }
    if (e.key === "ArrowLeft") { show(i - 1); e.preventDefault(); e.stopPropagation(); }
  }, true);
})();
