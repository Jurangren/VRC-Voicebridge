const outputDeviceSelect = document.getElementById('outputDeviceSelect');
const chunkSeconds = document.getElementById('chunkSeconds');
const energyThreshold = document.getElementById('energyThreshold');
const silenceMs = document.getElementById('silenceMs');
const overlayTextSeconds = document.getElementById('overlayTextSeconds');
const overlayTextAlpha = document.getElementById('overlayTextAlpha');
const overlayPositionHotkey = document.getElementById('overlayPositionHotkey');
const recognitionProvider = document.getElementById('recognitionProvider');
const recognitionLanguage = document.getElementById('recognitionLanguage');
const tencentAsrEngineModelType = document.getElementById('tencentAsrEngineModelType');
const localWhisperModel = document.getElementById('localWhisperModel');
const sourceLanguage = document.getElementById('sourceLanguage');
const targetLanguage = document.getElementById('targetLanguage');
const translationProvider = document.getElementById('translationProvider');
const refreshOutputsButton = document.getElementById('refreshOutputsButton');
const saveConfigButton = document.getElementById('saveConfigButton');
const startButton = document.getElementById('startButton');
const stopButton = document.getElementById('stopButton');
const clearHistoryButton = document.getElementById('clearHistoryButton');
const transcriptView = document.getElementById('transcript');
const translationView = document.getElementById('translation');
const historyView = document.getElementById('history');
const statusView = document.getElementById('status');

let running = false;
let lastOriginal = '';
let recentOriginals = [];
let history = loadHistory();
let activePresetIndex = null;
let presetPollTimer = null;

function setStatus(message, isError = false) {
  statusView.textContent = message;
  statusView.classList.toggle('error-text', isError);
}

async function refreshOutputDevices() {
  const res = await fetch('/api/output-capture/devices');
  const data = await res.json();
  if (!Array.isArray(data)) throw new Error(data.error || '输出设备列表格式异常');
  const current = outputDeviceSelect.value;
  outputDeviceSelect.innerHTML = '<option value="">系统默认输出设备</option>';
  data.forEach((device) => {
    const option = document.createElement('option');
    option.value = device.id;
    option.textContent = `#${device.index} ${device.name}`;
    if (device.id === current) option.selected = true;
    outputDeviceSelect.appendChild(option);
  });
  setStatus('输出设备列表已刷新');
}

function collectConfigPayload() {
  const sourceLanguageValue = sourceLanguage.value.trim();
  return {
    output_device_id: outputDeviceSelect.value,
    chunk_seconds: Number(chunkSeconds.value || 8),
    energy_threshold: Number(energyThreshold.value || 0.01),
    silence_ms: Number(silenceMs.value || 900),
    overlay_text_seconds: Number(overlayTextSeconds.value || 6),
    overlay_text_alpha: Number(overlayTextAlpha.value || 0.78),
    overlay_position_hotkey: overlayPositionHotkey.value.trim(),
    recognition_provider: recognitionProvider.value,
    recognition_language: sourceLanguageValue,
    tencent_asr_engine_model_type: tencentAsrEngineModelType.value.trim(),
    local_whisper_model: localWhisperModel.value.trim(),
    provider: translationProvider.value,
    source_language: sourceLanguageValue,
    target_language: targetLanguage.value.trim(),
  };
}

async function loadSavedConfig() {
  const res = await fetch('/api/speech-translate/config');
  const config = await res.json();
  chunkSeconds.value = config.chunk_seconds ?? chunkSeconds.value;
  energyThreshold.value = config.energy_threshold ?? energyThreshold.value;
  silenceMs.value = config.silence_ms ?? silenceMs.value;
  overlayTextSeconds.value = config.overlay_text_seconds ?? overlayTextSeconds.value;
  overlayTextAlpha.value = config.overlay_text_alpha ?? overlayTextAlpha.value;
  overlayPositionHotkey.value = config.overlay_position_hotkey ?? overlayPositionHotkey.value;
  recognitionProvider.value = config.recognition_provider ?? recognitionProvider.value;
  recognitionLanguage.value = config.source_language ?? config.recognition_language ?? recognitionLanguage.value;
  tencentAsrEngineModelType.value = config.tencent_asr_engine_model_type ?? tencentAsrEngineModelType.value;
  localWhisperModel.value = config.local_whisper_model ?? localWhisperModel.value;
  sourceLanguage.value = config.source_language ?? sourceLanguage.value;
  targetLanguage.value = config.target_language ?? targetLanguage.value;
  translationProvider.value = config.provider ?? translationProvider.value;
  await refreshOutputDevices();
  if (config.output_device_id) outputDeviceSelect.value = config.output_device_id;
}

window.onPresetApplied = async () => {
  await loadSavedConfig();
  await refreshActivePresetIndex();
  setStatus('已加载切换后的预设配置');
};

async function refreshActivePresetIndex() {
  const res = await fetch('/api/presets');
  const summary = await res.json();
  activePresetIndex = summary.active ?? activePresetIndex;
  return summary;
}

async function pollPresetSwitch() {
  try {
    const res = await fetch('/api/presets');
    const summary = await res.json();
    if (activePresetIndex !== null && summary.active !== activePresetIndex) {
      activePresetIndex = summary.active;
      await loadSavedConfig();
      setStatus(running ? `已切换到预设 ${summary.active}，实时翻译继续运行` : `已切换到预设 ${summary.active}`);
    } else {
      activePresetIndex = summary.active ?? activePresetIndex;
    }
  } catch (_) {
    // 轮询只用于同步热键切换后的页面配置，失败时不打断实时翻译循环。
  }
}

function startPresetPolling() {
  if (presetPollTimer !== null) return;
  presetPollTimer = window.setInterval(pollPresetSwitch, 1000);
}

async function saveConfig() {
  const res = await fetch('/api/speech-translate/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(collectConfigPayload()),
  });
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || '保存配置失败');
  setStatus('实时语音翻译配置已保存');
}

async function setCaptureEnabled(enabled) {
  await fetch('/api/output-capture/enabled', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({enabled}),
  });
}

async function captureTranslateLoop() {
  while (running) {
    try {
      setStatus('正在监听输出设备音频...');
      const res = await fetch('/api/output-capture/translate-once', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({device_id: outputDeviceSelect.value, ...collectConfigPayload()}),
      });
      const data = await res.json().catch(() => ({}));
      if (!running) return;
      if (!data.ok) {
        if (data.silent) {
          setStatus('未识别到可翻译的输出设备语音，继续监听...');
          continue;
        }
        throw new Error(data.error || '输出设备实时翻译失败');
      }
      if (isDuplicate(data.original)) {
        setStatus('识别到重复片段，已跳过，继续监听...');
        continue;
      }
      lastOriginal = data.original;
      rememberOriginal(data.original);
      transcriptView.textContent = data.original;
      transcriptView.classList.remove('muted');
      translationView.textContent = data.translated;
      translationView.classList.remove('muted');
      addHistory(data);
      setStatus('已翻译当前输出音频片段，继续监听...');
    } catch (error) {
      setStatus(`监听/翻译失败：${error.message || error}`, true);
      await wait(1200);
    }
  }
}

function normalizeText(text) {
  return String(text || '').replace(/[\s\p{P}\p{S}]/gu, '').toLowerCase();
}

function isDuplicate(text) {
  const normalized = normalizeText(text);
  if (!normalized) return true;
  return recentOriginals.some((item) => item === normalized || item.includes(normalized) || normalized.includes(item));
}

function rememberOriginal(text) {
  const normalized = normalizeText(text);
  if (!normalized) return;
  recentOriginals.unshift(normalized);
  recentOriginals = recentOriginals.slice(0, 8);
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function addHistory(item) {
  history.unshift({...item, created_at: new Date().toLocaleString()});
  history = history.slice(0, 100);
  localStorage.setItem('vrcVoiceBridgeOutputTranslateHistory', JSON.stringify(history));
  renderHistory();
}

function loadHistory() {
  try {
    const raw = localStorage.getItem('vrcVoiceBridgeOutputTranslateHistory');
    return raw ? JSON.parse(raw) : [];
  } catch (_) {
    return [];
  }
}

function renderHistory() {
  if (!history.length) {
    historyView.innerHTML = '<p class="hint">暂无翻译历史。</p>';
    return;
  }
  historyView.innerHTML = '';
  history.forEach((item) => {
    const row = document.createElement('article');
    row.className = 'history-item';
    row.innerHTML = `
      <div class="history-meta"></div>
      <div class="history-original"></div>
      <div class="history-translated"></div>
    `;
    row.querySelector('.history-meta').textContent = `${item.created_at} · ${item.provider} · ${item.source_language} → ${item.target_language}`;
    row.querySelector('.history-original').textContent = item.original;
    row.querySelector('.history-translated').textContent = item.translated;
    historyView.appendChild(row);
  });
}

refreshOutputsButton.addEventListener('click', async () => {
  try {
    await refreshOutputDevices();
  } catch (error) {
    setStatus(`刷新输出设备失败：${error.message || error}`, true);
  }
});

saveConfigButton.addEventListener('click', async () => {
  try {
    await saveConfig();
  } catch (error) {
    setStatus(`保存配置失败：${error.message || error}`, true);
  }
});

startButton.addEventListener('click', () => {
  running = true;
  lastOriginal = '';
  recentOriginals = [];
  startButton.disabled = true;
  stopButton.disabled = false;
  setCaptureEnabled(true).catch(() => {});
  captureTranslateLoop();
});

stopButton.addEventListener('click', () => {
  running = false;
  startButton.disabled = false;
  stopButton.disabled = true;
  setCaptureEnabled(false).catch(() => {});
  setStatus('已停止');
});

clearHistoryButton.addEventListener('click', () => {
  history = [];
  localStorage.removeItem('vrcVoiceBridgeOutputTranslateHistory');
  renderHistory();
});

sourceLanguage.addEventListener('input', () => {
  recognitionLanguage.value = sourceLanguage.value.trim();
});

renderHistory();
loadSavedConfig()
  .then(() => refreshActivePresetIndex())
  .then(() => startPresetPolling())
  .catch((error) => setStatus(`加载配置失败：${error.message || error}`, true));
