async function loadPresetPanel() {
  const panel = document.querySelector('[data-preset-panel]');
  if (!panel) return;
  const list = panel.querySelector('[data-preset-list]');
  const nextHotkey = panel.querySelector('[data-preset-next-hotkey]');
  const status = panel.querySelector('[data-preset-status]');

  const setPresetStatus = (message, isError = false) => {
    status.textContent = message;
    status.classList.toggle('error-text', isError);
  };

  const fetchSummary = async () => {
    const res = await fetch('/api/presets');
    return await res.json();
  };

  const render = (summary) => {
    nextHotkey.value = summary.next_hotkey || '';
    list.innerHTML = '';
    summary.presets.forEach((preset) => {
      const row = document.createElement('div');
      row.className = 'preset-row';
      row.dataset.index = String(preset.index);
      if (preset.index === summary.active) row.classList.add('active');
      row.innerHTML = `
        <div class="preset-badge">${preset.index}</div>
        <label>名称 <input data-preset-name value=""></label>
        <label>热键 <input data-preset-hotkey value=""></label>
        <button type="button" data-apply-preset>切换</button>
        <button type="button" data-save-preset>保存到此预设</button>
      `;
      row.querySelector('[data-preset-name]').value = preset.name || `预设 ${preset.index}`;
      row.querySelector('[data-preset-hotkey]').value = preset.hotkey || '';
      row.querySelector('[data-apply-preset]').addEventListener('click', () => applyPreset(preset.index));
      row.querySelector('[data-save-preset]').addEventListener('click', () => saveCurrentPreset(preset.index));
      list.appendChild(row);
    });
  };

  const saveMeta = async () => {
    const payload = {preset_next_hotkey: nextHotkey.value.trim()};
    list.querySelectorAll('.preset-row').forEach((row) => {
      const index = row.dataset.index;
      payload[`preset_${index}_name`] = row.querySelector('[data-preset-name]').value.trim();
      payload[`preset_${index}_hotkey`] = row.querySelector('[data-preset-hotkey]').value.trim();
    });
    const res = await fetch('/api/presets/meta', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || '保存预设信息失败');
    render(data.summary);
    setPresetStatus('预设名称和热键已保存');
  };

  const saveCurrentPreset = async (index) => {
    const res = await fetch(`/api/presets/${index}/save`, {method: 'POST'});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || '保存当前设置到预设失败');
    render(data.summary);
    setPresetStatus(`已保存当前设置到预设 ${index}`);
  };

  const applyPreset = async (index) => {
    const res = await fetch(`/api/presets/${index}/apply`, {method: 'POST'});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || '切换预设失败');
    render(data.summary);
    setPresetStatus(`已切换到预设 ${data.active}：${data.name}`);
    if (panel.dataset.applyBehavior === 'reload') {
      window.location.reload();
    } else if (typeof window.onPresetApplied === 'function') {
      window.onPresetApplied(data);
    }
  };

  panel.querySelector('[data-save-preset-meta]').addEventListener('click', () => {
    saveMeta().catch((error) => setPresetStatus(error.message || String(error), true));
  });
  panel.querySelector('[data-save-current-preset]').addEventListener('click', async () => {
    try {
      const summary = await fetchSummary();
      await saveCurrentPreset(summary.active || 1);
    } catch (error) {
      setPresetStatus(error.message || String(error), true);
    }
  });

  try {
    render(await fetchSummary());
  } catch (error) {
    setPresetStatus(error.message || String(error), true);
  }
}

document.addEventListener('DOMContentLoaded', loadPresetPanel);
