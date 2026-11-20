/* Translate application-owned text only; interpolated user data is never translated. */
const uiLanguage = (() => {
  try { const saved = localStorage.getItem('vps_language'); if (saved === 'en' || saved === 'zh') return saved; } catch {}
  return (navigator.language || 'zh').toLowerCase().startsWith('zh') ? 'zh' : 'en';
})();
// 先整句（去掉首尾空白后）完全匹配；不行再按词条子串替换，但子串替换不用单个汉字的词条
// （「上」「个」「分」…会拼进别的句子，出过「连不Top」「部minAll」这种乱码），没翻到的就留中文，靠扫描补整句词条。
const uiPhrasePattern = new RegExp(Object.keys(UI_EN).filter(k => !/^\s*[一-鿿]\s*$/.test(k))
  .sort((a, b) => b.length - a.length)
  .map(s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'), 'g');
function uiTranslate(text) {
  if (Object.prototype.hasOwnProperty.call(UI_EN, text)) return UI_EN[text];
  const m = /^(\s*)([\s\S]*?)(\s*)$/.exec(text);
  if (m[2] && Object.prototype.hasOwnProperty.call(UI_EN, m[2])) return m[1] + UI_EN[m[2]].trim() + m[3];
  return text.replace(uiPhrasePattern, key => UI_EN[key]);
}
// 后端的日志 / 报错（服务端写的中文）：英文界面显示时按句型翻（ui/i18n-en-msgs.js：后端源码里抽出来的模板 → 英文，
// {0} {1} … = 模板里的变量）。一行先整行套模板（变量部分再递归翻：常是「首帧」之类的词），套不上再按常量句 / 界面词条子串替换。
const uiMsgRules = (() => {
  const src = (typeof UI_EN_MSGS === 'object' && UI_EN_MSGS) || {};
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const rules = [], consts = [];
  for (const [zh0, en0] of Object.entries(src)) {
    const tag = /^\s*\[[^\]\n]*\]\s*/;                   // 行首缩进 / 「[4090] 」标记 / 行尾空白在 uiMsgLine 里单独留着
    const zh = zh0.replace(tag, '').trim(), en = String(en0).replace(tag, '').trim();
    const pieces = zh.split(/\{(\d+)\}/);                  // 偶数位 = 字面，奇数位 = 变量号
    const lit = pieces.filter((_, i) => i % 2 === 0).map(s => s.replace(/\{\{/g, '{').replace(/\}\}/g, '}'));
    if (pieces.length === 1) { if (lit[0].trim().length >= 2) consts.push([lit[0], en.replace(/\{\{/g, '{').replace(/\}\}/g, '}')]); continue; }
    if (!/[一-鿿]/.test(lit.join(''))) continue;
    const order = pieces.filter((_, i) => i % 2 === 1).map(Number);
    const re = new RegExp('^' + lit.map(esc).join('([\\s\\S]*?)') + '$');
    rules.push({ re, en, order, w: lit.join('').length });
  }
  rules.sort((a, b) => b.w - a.w);
  consts.sort((a, b) => b[0].length - a[0].length);
  const cre = consts.length ? new RegExp(consts.map(c => esc(c[0])).join('|'), 'g') : null;
  return { rules, cmap: new Map(consts), cre };
})();
const uiMsgCache = new Map();
// 路径（硬盘上真实的文件夹名是中文，比如 结果result\v1\…）：一律原样，翻了就找不到文件
const UI_PATH_RE = /(?:[A-Za-z]:)?[^\s，。；：（）()"「」“”]*[\\/][^\s，。；（）()"「」“”]+/g;
const uiIsPath = (s) => /^\s*(?:[A-Za-z]:[\\/]|\S*[\\/]\S*\s*$)/.test(s);
function uiMsgLine(line, depth = 0) {
  if (!/[一-鿿]/.test(line)) return line;
  if (depth > 0 && uiIsPath(line)) return line;
  const m = /^(\s*(?:\[[^\]\n]*\]\s*)?)([\s\S]*?)(\s*)$/.exec(line);   // 前面的缩进 / 「[4090] 」这种标记照留
  let [, pre, body, post] = m;
  pre = pre.replace(/\[([^\]\n]*)\]/, (_, t) => '[' + (depth < 3 ? uiMsgLine(t, depth + 1) : t) + ']');   // 「[网络]」「[第 3 块]」这种标记也翻
  if (Object.prototype.hasOwnProperty.call(UI_EN, body)) return pre + UI_EN[body] + post;
  if (uiMsgRules.cmap.has(body)) return pre + uiMsgRules.cmap.get(body) + post;
  if (depth < 3) for (const r of uiMsgRules.rules) {
    const g = r.re.exec(body);
    if (!g) continue;
    const vals = {};
    r.order.forEach((n, i) => { vals[n] = uiMsgLine(g[i + 1], depth + 1); });
    return pre + r.en.replace(/\{(\d+)\}/g, (_, n) => vals[n] ?? '').replace(/\{\{/g, '{').replace(/\}\}/g, '}') + post;
  }
  const seg = (s) => {                                     // 兜底：常量句 / 界面词条子串替换，路径那几段跳过
    if (uiMsgRules.cre) s = s.replace(uiMsgRules.cre, k => uiMsgRules.cmap.get(k));
    return uiTranslate(s);
  };
  let out = '', last = 0;
  for (const m2 of body.matchAll(UI_PATH_RE)) { out += seg(body.slice(last, m2.index)) + m2[0]; last = m2.index + m2[0].length; }
  out += last ? seg(body.slice(last)) : '';
  if (!last) out = seg(body);
  return pre + out + post;
}
function uiMsg(text) {
  if (uiLanguage !== 'en' || text == null) return text;
  text = String(text);
  if (!/[一-鿿]/.test(text)) return text;
  let v = uiMsgCache.get(text);
  if (v === undefined) {
    v = text.split('\n').map(l => uiMsgLine(l)).join('\n');
    if (uiMsgCache.size > 5000) uiMsgCache.clear();
    uiMsgCache.set(text, v);
  }
  return v;
}
function T(source, ...values) {
  const translate = text => uiLanguage === 'en' ? uiTranslate(text) : text;
  if (typeof source === 'string') return translate(source);
  return source.reduce((out, part, i) => out + translate(part) + (i < values.length ? values[i] : ''), '');
}
document.documentElement.lang = uiLanguage;
// Run once on static markup, before any project or user text is rendered.
for (const el of document.querySelectorAll('option:not([value])')) el.value = el.textContent;
const uiTextWalker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
while (uiTextWalker.nextNode()) {
  const node = uiTextWalker.currentNode;
  if (!node.parentElement.closest('script, style, textarea')) node.nodeValue = T(node.nodeValue);
}
for (const el of document.querySelectorAll('[title], [placeholder], [aria-label], [alt]')) {
  for (const name of ['title', 'placeholder', 'aria-label', 'alt']) {
    if (el.hasAttribute(name)) el.setAttribute(name, T(el.getAttribute(name)));
  }
}
let uiLanguageWrites = null;
const languageSelect = document.getElementById('uiLanguage');
languageSelect.value = uiLanguage;
languageSelect.addEventListener('change', async () => {
  const next = languageSelect.value;
  languageSelect.disabled = true;
  try {
    if (S.refine || S.shape) throw new Error(uiLanguage === 'en' ? 'Finish or cancel the current mask edit before changing language.' : '请先完成或取消当前蒙版编辑，再切换语言。');
    uiLanguageWrites = new Set(pendingApiWrites);
    document.body.inert = true;
    // Existing frame/image controls debounce saves by up to 400 ms.
    await new Promise(resolve => setTimeout(resolve, 500));
    await transSave;
    await flushLayerSaves();
    if ([...layerSaves.values()].some(q => q.error)) throw new Error(uiLanguage === 'en' ? 'Save failed. Save your edits before changing language.' : '保存失败，请保存编辑后再切换语言。');
    while (uiLanguageWrites.size) {
      const writes = [...uiLanguageWrites];
      uiLanguageWrites.clear();
      await Promise.all(writes);
    }
    localStorage.setItem('vps_language', next);
    location.reload();
  } catch (e) {
    uiLanguageWrites = null;
    document.body.inert = false;
    languageSelect.value = uiLanguage;
    languageSelect.disabled = false;
    setLog(e.message, true);
  }
});
