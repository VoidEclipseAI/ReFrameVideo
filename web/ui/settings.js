(() => {
  const theme = document.getElementById('prefTheme');
  const language = document.getElementById('prefLanguage');
  let saved = 'system';
  try { saved = localStorage.getItem('vps_theme') || 'system'; } catch {}
  const applyTheme = value => {
    theme.value = ['system', 'light', 'dark'].includes(value) ? value : 'system';
    document.documentElement.dataset.theme = theme.value;
  };
  applyTheme(saved);
  theme.onchange = () => { applyTheme(theme.value); try { localStorage.setItem('vps_theme', theme.value); } catch {} };
  language.value = uiLanguage;
  language.onchange = () => {
    const top = document.getElementById('uiLanguage');
    top.value = language.value;
    top.dispatchEvent(new Event('change'));
    language.value = uiLanguage;
  };
  let checking = false;
  async function checkServices() {
    if (checking) return;
    checking = true;
    const button = document.getElementById('serviceCheck'), status = document.getElementById('serviceStatus');
    button.disabled = true; status.textContent = T('正在检查服务…');
    document.getElementById('settingsVersion').textContent = T('前端版本') + ' 2026.11.10';
    try {
      const info = await api('/api/info', { timeout: 5000 });
      document.getElementById('settingsVersion').textContent += ' · ' + T('后端 API') + ' ' + info.api;
      status.textContent = T('后端已连接');
      if (info.engine === 'mock') status.textContent += ' · ' + T('ComfyUI 未启用（假引擎）');
      else {
        try { await api('/api/qwen', { timeout: 5000 }); status.textContent += ' · ' + T('ComfyUI 模型接口可用'); }
        catch { status.textContent += ' · ' + T('ComfyUI 无响应或不可用'); }
      }
    } catch { status.textContent = T('后端不可用'); }
    finally { checking = false; button.disabled = false; }
  }
  document.getElementById('serviceCheck').onclick = checkServices;
  window.refreshSettings = checkServices;
  document.getElementById('resetLayout').onclick = () => { window.uiResetLayout(); setLog(T('布局已恢复')); };
  document.getElementById('settingsHelp').onclick = () => {
    document.getElementById('provView').hidden = true;
    document.getElementById('help').hidden = false;
  };
})();
