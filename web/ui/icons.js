/* 图标：全部内联 SVG（24×24，描边跟随 currentColor），避免额外字体 / 网络请求。
   用法：IC("undo") 返回 svg 字符串；<button data-icon="undo"> 会被 uiIcons() 自动填充。 */
(() => {
  const P = {
    film: '<rect x="2.5" y="4" width="19" height="16" rx="2"/><path d="M7 4v16M17 4v16M2.5 9.5H7M2.5 14.5H7M17 9.5h4.5M17 14.5h4.5"/>',
    "chevron-down": '<path d="M6 9.5l6 6 6-6"/>',
    "chevron-left": '<path d="M15 6l-6 6 6 6"/>',
    "chevron-right": '<path d="M9 6l6 6-6 6"/>',
    clipboard: '<rect x="6" y="4.5" width="12" height="16" rx="2"/><path d="M9.5 4.5V3.5h5v1"/><path d="M9 11h6M9 15h4"/>',
    contrast: '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5v17a8.5 8.5 0 0 0 0-17z" fill="currentColor"/>',
    copy: '<rect x="8.5" y="8.5" width="12" height="12" rx="2"/><path d="M15.5 8.5V5.5a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h3"/>',
    history: '<path d="M3.5 12a8.5 8.5 0 1 0 2.5-6"/><path d="M3 4v4.5h4.5"/><path d="M12 7.5V12l3 2"/>',
    undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
    redo: '<path d="M15 14l5-5-5-5"/><path d="M20 9H10a6 6 0 0 0 0 12h3"/>',
    stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2" fill="currentColor" stroke="none"/>',
    activity: '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.6 8a1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    book: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5"/><path d="M8 7.5h8M8 11h5"/>',
    keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M9.5 10h.01M13 10h.01M16.5 10h.01M6.5 14h11"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.6 2.6 0 0 1 5 .9c0 1.7-2.6 2.1-2.6 4"/><path d="M12 17.4h.01"/>',
    layers: '<path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 12.5l9 5 9-5"/><path d="M3 17l9 5 9-5"/>',
    scan: '<path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2M4 12h16"/>',
    crop: '<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M2 6h14a2 2 0 0 1 2 2v14"/>',
    fit: '<path d="M8 3H4a1 1 0 0 0-1 1v4M16 3h4a1 1 0 0 1 1 1v4M21 16v4a1 1 0 0 1-1 1h-4M3 16v4a1 1 0 0 0 1 1h4"/><rect x="8.5" y="8.5" width="7" height="7" rx="1"/>',
    palette: '<path d="M12 21a9 9 0 1 1 9-9c0 2.2-1.8 3.2-3.4 3.2h-1.8a2.1 2.1 0 0 0-1.2 3.8c.5.4.3 2-1.6 2z"/><circle cx="8" cy="10" r="1"/><circle cx="12" cy="7.5" r="1"/><circle cx="16" cy="10" r="1"/>',
    grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "al-left": '<path d="M4 3v18"/><rect x="7" y="6" width="11" height="4" rx="1"/><rect x="7" y="14" width="7" height="4" rx="1"/>',
    "al-hc": '<path d="M12 3v18"/><rect x="5.5" y="6" width="13" height="4" rx="1"/><rect x="8" y="14" width="8" height="4" rx="1"/>',
    "al-right": '<path d="M20 3v18"/><rect x="6" y="6" width="11" height="4" rx="1"/><rect x="10" y="14" width="7" height="4" rx="1"/>',
    "al-top": '<path d="M3 4h18"/><rect x="6" y="7" width="4" height="11" rx="1"/><rect x="14" y="7" width="4" height="7" rx="1"/>',
    "al-vc": '<path d="M3 12h18"/><rect x="6" y="5.5" width="4" height="13" rx="1"/><rect x="14" y="8" width="4" height="8" rx="1"/>',
    "al-bottom": '<path d="M3 20h18"/><rect x="6" y="6" width="4" height="11" rx="1"/><rect x="14" y="10" width="4" height="7" rx="1"/>',
    "flip-h": '<path d="M12 2.5v19" stroke-dasharray="2 3"/><path d="M9.5 6.5L4 12l5.5 5.5z"/><path d="M14.5 6.5L20 12l-5.5 5.5z"/>',
    "flip-v": '<path d="M2.5 12h19" stroke-dasharray="2 3"/><path d="M6.5 9.5L12 4l5.5 5.5z"/><path d="M6.5 14.5L12 20l5.5-5.5z"/>',
    "rotate-ccw": '<path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1"/><path d="M3.5 4.5V10h5.5"/>',
    sliders: '<path d="M4 7h8M16.5 7H20M4 12h4M12.5 12H20M4 17h11M19 17h1"/><circle cx="14" cy="7" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="17" cy="17" r="2"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    minus: '<path d="M5 12h14"/>',
    eye: '<path d="M2 12s3.6-6.8 10-6.8S22 12 22 12s-3.6 6.8-10 6.8S2 12 2 12z"/><circle cx="12" cy="12" r="2.8"/>',
    "eye-off": '<path d="M2 12s3.6-6.8 10-6.8S22 12 22 12s-3.6 6.8-10 6.8S2 12 2 12z"/><circle cx="12" cy="12" r="2.8"/><path d="M3.5 3.5l17 17"/>',
    lock: '<rect x="4" y="10.5" width="16" height="10" rx="2"/><path d="M8 10.5V7a4 4 0 0 1 8 0v3.5"/>',
    unlock: '<rect x="4" y="10.5" width="16" height="10" rx="2"/><path d="M8 10.5V7a4 4 0 0 1 7.6-1.7"/>',
    more: '<circle cx="5.5" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="18.5" cy="12" r="1.4" fill="currentColor" stroke="none"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    pointer: '<path d="M5.5 3.5l12.5 7-5.4 1.5 3.3 6.1-2.3 1.2-3.3-6.1-4 3.7z"/>',
    hand: '<path d="M8 13V6a1.5 1.5 0 0 1 3 0v5"/><path d="M11 11V4.5a1.5 1.5 0 0 1 3 0V11"/><path d="M14 11V6a1.5 1.5 0 0 1 3 0v6"/><path d="M17 12v-1a1.5 1.5 0 0 1 3 0v3.5a7 7 0 0 1-7 7h-1a6 6 0 0 1-4.9-2.6L4.6 15a1.6 1.6 0 0 1 2.6-1.9L8 14"/>',
    target: '<circle cx="12" cy="12" r="3.2"/><path d="M12 2.5v4M12 17.5v4M2.5 12h4M17.5 12h4"/>',
    brush: '<path d="M9.5 12.2l7.9-7.9a2.6 2.6 0 0 1 3.6 3.6l-7.9 7.9"/><path d="M7 15.2a2.9 2.9 0 0 0-2.9 2.9c0 1.3-2.4 1.5-1.9 2 1 1 2.4 1.9 3.9 1.9a4 4 0 0 0 4-4 2.9 2.9 0 0 0-3.1-2.8z"/>',
    eraser: '<path d="M8 20h12"/><path d="M5.5 15.5l6-6a2 2 0 0 1 2.8 0l3.2 3.2a2 2 0 0 1 0 2.8L14 19H9l-3.5-3.5z"/><path d="M10.5 9.5l5 5"/>',
    wand: '<path d="M4.5 19.5l9.5-9.5"/><path d="M14 5.5V3M14 15.5V13M9 9.5H6.5M21.5 9.5H19"/><path d="M17.6 11.9l1.4 1.4M17.6 5.7l1.4-1.4M11.4 5.7L10 4.3M11.4 11.9L10 13.3"/>',
    move: '<path d="M12 3v18M3 12h18"/><path d="M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 6.5"/>',
    rect: '<rect x="4" y="5" width="16" height="14" rx="2"/>',
    ellipse: '<circle cx="12" cy="12" r="8"/>',
    polygon: '<path d="M12 3.5l8 6-3 9.5H7L4 9.5l8-6z"/>',
    lasso: '<path d="M7 5.5a6 5 0 1 0 9 4.2c0 2-1 3.3-3 3.3-1.2 0-2 .6-2 2s1.1 2.6 1.1 4.5"/>',
    trash: '<path d="M3.5 6.5h17"/><path d="M8.5 6.5V4.2h7v2.3"/><path d="M18.8 6.5L17.9 20H6.1L5.2 6.5"/><path d="M10 11v5.5M14 11v5.5"/>',
    "zoom-in": '<circle cx="11" cy="11" r="7"/><path d="M20.5 20.5l-4.2-4.2M11 8.2v5.6M8.2 11h5.6"/>',
    "zoom-out": '<circle cx="11" cy="11" r="7"/><path d="M20.5 20.5l-4.2-4.2M8.2 11h5.6"/>',
    split: '<rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="M12 2.5v19" stroke-dasharray="2.5 2.5"/>',
    columns: '<rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="M9.5 4.5v15M14.5 4.5v15"/>',
    play: '<path d="M8 5.2l11 6.8-11 6.8z" fill="currentColor" stroke="none"/>',
    pause: '<rect x="7" y="5" width="3.6" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="13.4" y="5" width="3.6" height="14" rx="1" fill="currentColor" stroke="none"/>',
    "skip-back": '<path d="M19.5 19.5L9 12l10.5-7.5z"/><path d="M5 19V5"/>',
    "skip-forward": '<path d="M4.5 4.5L15 12 4.5 19.5z"/><path d="M19 5v14"/>',
    zap: '<path d="M13 2.5L4 14h7l-1 7.5L20 10h-7l1-7.5z"/>',
    dice: '<rect x="3.5" y="3.5" width="17" height="17" rx="3"/><circle cx="8.5" cy="8.5" r="1.3" fill="currentColor" stroke="none"/><circle cx="15.5" cy="8.5" r="1.3" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none"/><circle cx="8.5" cy="15.5" r="1.3" fill="currentColor" stroke="none"/><circle cx="15.5" cy="15.5" r="1.3" fill="currentColor" stroke="none"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9.5" r="1.6"/><path d="M20.5 15.5l-5-5-10 9.5"/>',
    images: '<rect x="7" y="3" width="14" height="12" rx="2"/><path d="M17 19H5a2 2 0 0 1-2-2V8"/><circle cx="11.5" cy="7.5" r="1.2"/><path d="M21 12l-3.5-3.5L9 15"/>',
    upload: '<path d="M12 16V4"/><path d="M7 9l5-5 5 5"/><path d="M4 16v2.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V16"/>',
    download: '<path d="M12 4v12"/><path d="M7 11l5 5 5-5"/><path d="M4 19.5h16"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h3.8l2 2H19a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "arrow-left": '<path d="M19 12H5"/><path d="M11 6l-6 6 6 6"/>',
    "arrow-up": '<path d="M12 19V5"/><path d="M6 11l6-6 6 6"/>',
    "arrow-down": '<path d="M12 5v14"/><path d="M6 13l6 6 6-6"/>',
    "to-front": '<rect x="3.5" y="3.5" width="11" height="11" rx="1.5"/><path d="M10 20.5h9a1.5 1.5 0 0 0 1.5-1.5v-9" stroke-dasharray="2.5 2.5"/>',
    "to-back": '<rect x="9.5" y="9.5" width="11" height="11" rx="1.5"/><path d="M14 3.5H5A1.5 1.5 0 0 0 3.5 5v9" stroke-dasharray="2.5 2.5"/>',
    merge: '<path d="M6 4v4a4 4 0 0 0 4 4h1a4 4 0 0 1 4 4v4"/><path d="M18 4v4a4 4 0 0 1-4 4h-1a4 4 0 0 0-4 4v4"/><path d="M3 7l3-3 3 3M15 17l3 3 3-3"/>',
    sparkles: '<path d="M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9L12 3z"/><path d="M18.5 15.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9.9-2.1z"/>',
    "lock-open": '<rect x="4" y="10.5" width="16" height="10" rx="2"/><path d="M8 10.5V7a4 4 0 0 1 7.6-1.7"/>',
    folder_open: '<path d="M3 7a2 2 0 0 1 2-2h3.8l2 2H19a2 2 0 0 1 2 2v1H3z"/><path d="M3 10h18l-2 8.5a2 2 0 0 1-2 1.5H5.5a2 2 0 0 1-2-1.5z"/>',
    pencil: '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16v4z"/><path d="M13.5 6.5l4 4"/>',
    dot: '<circle cx="12" cy="12" r="3" fill="currentColor" stroke="none"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.8h.01"/>',
    expand: '<path d="M9 3H4v5M15 3h5v5M15 21h5v-5M9 21H4v-5"/>',
    cube: '<path d="M12 2.8l8.5 4.7v9L12 21.2 3.5 16.5v-9L12 2.8z"/><path d="M3.5 7.5L12 12.2l8.5-4.7M12 12.2v9"/>',
  };
  function IC(name, cls) {
    const body = P[name] || P.dot;
    return '<svg class="ico' + (cls ? " " + cls : "") + '" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' + body + "</svg>";
  }
  function uiIcons(root) {
    const scope = root || document;
    for (const el of scope.querySelectorAll("[data-icon]")) {
      if (el.dataset.iconDone === "1") continue;
      el.insertAdjacentHTML("afterbegin", IC(el.dataset.icon));
      el.dataset.iconDone = "1";
    }
  }
  window.IC = IC;
  window.uiIcons = uiIcons;
  window.ICON_NAMES = Object.keys(P);
  uiIcons();
})();
