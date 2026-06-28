const outputDeviceSelect = document.getElementById('outputDeviceSelect');
const vadThreshold = document.getElementById('vadThreshold');
const silenceMs = document.getElementById('silenceMs');
const minSpeechMs = document.getElementById('minSpeechMs');
const chunkSeconds = document.getElementById('chunkSeconds');
const speakerEnabled = document.getElementById('speakerEnabled');
const speakerSimilarity = document.getElementById('speakerSimilarity');
const maxSpeakers = document.getElementById('maxSpeakers');
const speakerModelPath = document.getElementById('speakerModelPath');
const localWhisperModel = document.getElementById('localWhisperModel');
const hotwordsInput = document.getElementById('hotwords');
const sourceLanguage = document.getElementById('sourceLanguage');
const targetLanguage = document.getElementById('targetLanguage');
const translationProvider = document.getElementById('translationProvider');
const oscEnabled = document.getElementById('oscEnabled');
const oscFormat = document.getElementById('oscFormat');
const oscUserHoldSeconds = document.getElementById('oscUserHoldSeconds');
const oscToggleHotkey = document.getElementById('oscToggleHotkey');
const overlayTextSeconds = document.getElementById('overlayTextSeconds');
const overlayTextAlpha = document.getElementById('overlayTextAlpha');
const refreshDevicesButton = document.getElementById('refreshDevicesButton');
const saveConfigButton = document.getElementById('saveConfigButton');
const startButton = document.getElementById('startButton');
const stopButton = document.getElementById('stopButton');
const clearHistoryButton = document.getElementById('clearHistoryButton');
const transcriptView = document.getElementById('transcript');
const translationView = document.getElementById('translation');
const historyView = document.getElementById('history');
const statusView = document.getElementById('status');
const statusPill = document.getElementById('statusPill');
const vadMeterFill = document.getElementById('vadMeterFill');

const SPEAKER_COLORS = ['#4f8cff', '#f59e0b', '#34d399', '#f472b6', '#a78bfa', '#22d3ee', '#fb7185', '#a3e635'];

let running = false;
let lastEventId = 0;
let history = loadHistory();
let activePresetIndex = null;
let presetPollTimer = null;

function speakerLetter(index) {
  let letters = '';
  while (index > 0) {
    const remainder = (index - 1) % 26;
    letters = String.fromCharCode(65 + remainder) + letters;
    index = Math.floor((index - 1) / 26);
  }
  return letters;
}

function speakerColor(index) {
  return SPEAKER_COLORS[(index - 1) % SPEAKER_COLORS.length];
}

function speakerBadge(index) {
  const badge = document.createElement('span');
  badge.className = 'spk-badge';
  badge.style.setProperty('--spk', speakerColor(index));
  badge.textContent = speakerLetter(index);
  return badge;
}

function setStatus(message, isError = false, state = null) {
  statusView.textContent = message;
  statusView.classList.toggle('error-text', isError);
  if (state !== null) statusPill.dataset.state = state;
}

function setVadMeter(probability) {
  vadMeterFill.style.width = `${Math.round(Math.min(Math.max(probability || 0, 0), 1) * 100)}%`;
}

async function refreshDevices() {
  const res = await fetch('/api/speech-translate/devices');
  const data = await res.json();
  if (!data.outputs) throw new Error(data.error || '设备列表格式异常');
  const current = outputDeviceSelect.value;
  outputDeviceSelect.innerHTML = '<option value="">系统默认输出设备</option>';
  data.outputs.forEach((device) => {
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
    audio_source: 'output',
    output_device_id: outputDeviceSelect.value,
    vad_threshold: Number(vadThreshold.value || 0.5),
    silence_ms: Number(silenceMs.value || 900),
    min_speech_ms: Number(minSpeechMs.value || 300),
    chunk_seconds: Number(chunkSeconds.value || 8),
    speaker_enabled: speakerEnabled.checked,
    speaker_similarity: Number(speakerSimilarity.value || 0.6),
    max_speakers: Number(maxSpeakers.value || 6),
    speaker_model_path: speakerModelPath.value.trim(),
    local_whisper_model: localWhisperModel.value.trim(),
    hotwords: hotwordsInput.value.trim(),
    osc_enabled: oscEnabled.checked,
    osc_format: oscFormat.value.trim() || '{translated}',
    osc_user_hold_seconds: Number(oscUserHoldSeconds.value || 10),
    osc_toggle_hotkey: oscToggleHotkey.value.trim(),
    overlay_text_seconds: Number(overlayTextSeconds.value || 6),
    overlay_text_alpha: Number(overlayTextAlpha.value || 0.78),
    provider: translationProvider.value,
    source_language: sourceLanguageValue,
    target_language: targetLanguage.value.trim(),
  };
}

async function loadSavedConfig() {
  const res = await fetch('/api/speech-translate/config');
  const config = await res.json();
  vadThreshold.value = config.vad_threshold ?? vadThreshold.value;
  silenceMs.value = config.silence_ms ?? silenceMs.value;
  minSpeechMs.value = config.min_speech_ms ?? minSpeechMs.value;
  chunkSeconds.value = config.chunk_seconds ?? chunkSeconds.value;
  speakerEnabled.checked = config.speaker_enabled ?? speakerEnabled.checked;
  speakerSimilarity.value = config.speaker_similarity ?? speakerSimilarity.value;
  maxSpeakers.value = config.max_speakers ?? maxSpeakers.value;
  speakerModelPath.value = config.speaker_model_path ?? speakerModelPath.value;
  localWhisperModel.value = config.local_whisper_model ?? localWhisperModel.value;
  hotwordsInput.value = config.hotwords ?? hotwordsInput.value;
  sourceLanguage.value = config.source_language ?? sourceLanguage.value;
  targetLanguage.value = config.target_language ?? targetLanguage.value;
  translationProvider.value = config.provider ?? translationProvider.value;
  oscEnabled.checked = config.osc_enabled ?? oscEnabled.checked;
  oscFormat.value = config.osc_format ?? oscFormat.value;
  oscUserHoldSeconds.value = config.osc_user_hold_seconds ?? oscUserHoldSeconds.value;
  oscToggleHotkey.value = config.osc_toggle_hotkey ?? oscToggleHotkey.value;
  overlayTextSeconds.value = config.overlay_text_seconds ?? overlayTextSeconds.value;
  overlayTextAlpha.value = config.overlay_text_alpha ?? overlayTextAlpha.value;
  await refreshDevices();
  if (config.output_device_id) outputDeviceSelect.value = config.output_device_id;
}

// 保存/切换预设前，先把页面上的当前配置写入后端，确保预设快照拿到的是最新值
window.onBeforePresetSave = () => saveConfig();

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

function setLiveCard(view, speaker, text) {
  view.innerHTML = '';
  if (speaker) view.appendChild(speakerBadge(speaker));
  view.appendChild(document.createTextNode(text));
  view.classList.remove('muted');
}

function renderEvent(event) {
  setLiveCard(transcriptView, event.speaker, event.original);
  if (event.translated) {
    setLiveCard(translationView, event.speaker, event.translated);
  } else if (event.error) {
    setLiveCard(translationView, event.speaker, `翻译失败：${event.error}`);
  }
  addHistory(event);
}

function describeStatus(status) {
  if (!status.running) {
    return status.stage === 'error' ? `出错：${status.message}` : status.message || '已停止';
  }
  if (status.stage === 'loading') return status.message || '正在加载模型...';
  const parts = [];
  parts.push(status.speaking ? '检测到说话中...' : (status.message || '正在监听'));
  if (status.speaker_count > 0) parts.push(`已识别 ${status.speaker_count} 位说话人`);
  if (status.queue_size > 0) parts.push(`待处理片段 ${status.queue_size}`);
  if (status.last_error) parts.push(`最近错误：${status.last_error}`);
  return parts.join(' · ');
}

function pillState(status) {
  if (status.stage === 'error') return 'error';
  if (!status.running) return 'idle';
  if (status.stage === 'loading') return 'loading';
  return 'running';
}

async function pollStream() {
  while (running) {
    try {
      const res = await fetch(`/api/speech-translate/stream?after=${lastEventId}`);
      const data = await res.json();
      if (!running) return;
      (data.events || []).forEach((event) => {
        lastEventId = Math.max(lastEventId, event.id);
        renderEvent(event);
      });
      const status = data.status || {};
      setStatus(describeStatus(status), status.stage === 'error' || Boolean(status.last_error), pillState(status));
      setVadMeter(status.running ? status.vad_probability : 0);
      if (!status.running && status.stage === 'error') {
        stopRealtime(false);
        return;
      }
    } catch (error) {
      setStatus(`获取实时翻译结果失败：${error.message || error}`, true, 'error');
      await wait(1200);
    }
    await wait(400);
  }
}

async function startRealtime() {
  startButton.disabled = true;
  try {
    const res = await fetch('/api/speech-translate/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(collectConfigPayload()),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || '启动实时翻译失败');
    running = true;
    stopButton.disabled = false;
    setStatus('已启动，正在加载模型...', false, 'loading');
    pollStream();
  } catch (error) {
    startButton.disabled = false;
    setStatus(`启动失败：${error.message || error}`, true, 'error');
  }
}

function stopRealtime(callBackend = true) {
  running = false;
  startButton.disabled = false;
  stopButton.disabled = true;
  setVadMeter(0);
  if (callBackend) {
    fetch('/api/speech-translate/stop', {method: 'POST'}).catch(() => {});
    setStatus('已停止', false, 'idle');
  }
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function addHistory(item) {
  history.unshift({...item, created_at: item.created_at || new Date().toLocaleString()});
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
    if (item.speaker) row.style.setProperty('--spk', speakerColor(item.speaker));
    const meta = document.createElement('div');
    meta.className = 'history-meta';
    if (item.speaker) meta.appendChild(speakerBadge(item.speaker));
    const metaText = document.createElement('span');
    metaText.textContent = `${item.created_at} · ${item.provider} · ${item.source_language} → ${item.target_language}`;
    meta.appendChild(metaText);
    const original = document.createElement('div');
    original.className = 'history-original';
    original.textContent = item.original;
    const translated = document.createElement('div');
    translated.className = 'history-translated';
    translated.textContent = item.translated || (item.error ? `翻译失败：${item.error}` : '');
    row.append(meta, original, translated);
    historyView.appendChild(row);
  });
}

refreshDevicesButton.addEventListener('click', async () => {
  try {
    await refreshDevices();
  } catch (error) {
    setStatus(`刷新音频设备失败：${error.message || error}`, true);
  }
});

document.querySelectorAll('#saveConfigButton, .save-config-inline').forEach((button) => {
  button.addEventListener('click', async () => {
    try {
      await saveConfig();
    } catch (error) {
      setStatus(`保存配置失败：${error.message || error}`, true);
    }
  });
});

startButton.addEventListener('click', startRealtime);
stopButton.addEventListener('click', () => stopRealtime(true));

clearHistoryButton.addEventListener('click', () => {
  history = [];
  localStorage.removeItem('vrcVoiceBridgeOutputTranslateHistory');
  renderHistory();
});

renderHistory();
loadSavedConfig()
  .then(() => refreshActivePresetIndex())
  .then(() => startPresetPolling())
  .catch((error) => setStatus(`加载配置失败：${error.message || error}`, true));

// ---------- 本地模型下载 ----------
const downloadSpeakerModel = document.getElementById('downloadSpeakerModel');
const downloadWhisperModel = document.getElementById('downloadWhisperModel');
const modelDownloadFill = document.getElementById('modelDownloadFill');
const modelDownloadLabel = document.getElementById('modelDownloadLabel');
let modelPollTimer = null;

function setModelButtonsDisabled(disabled) {
  if (downloadSpeakerModel) downloadSpeakerModel.disabled = disabled;
  if (downloadWhisperModel) downloadWhisperModel.disabled = disabled;
}

function renderModelStatus(s) {
  if (modelDownloadFill) modelDownloadFill.style.width = `${Math.min(Math.max(s.percent || 0, 0), 100)}%`;
  if (modelDownloadLabel && s.message) modelDownloadLabel.textContent = s.message;
}

async function pollModelStatus() {
  if (modelPollTimer) clearTimeout(modelPollTimer);
  try {
    const res = await fetch('/api/models/status');
    const s = await res.json();
    renderModelStatus(s);
    if (s.running) {
      setModelButtonsDisabled(true);
      modelPollTimer = setTimeout(pollModelStatus, 500);
    } else {
      setModelButtonsDisabled(false);
    }
  } catch (_) {
    modelPollTimer = setTimeout(pollModelStatus, 1500);
  }
}

async function startModelDownload(target) {
  try {
    const res = await fetch('/api/models/download', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({target}),
    });
    if (res.status === 409) {
      // 已有任务在跑：直接接着轮询它
      setModelButtonsDisabled(true);
      pollModelStatus();
      return;
    }
    const data = await res.json();
    if (!data.ok) {
      if (modelDownloadLabel) modelDownloadLabel.textContent = data.error || '启动下载失败';
      return;
    }
    setModelButtonsDisabled(true);
    if (modelDownloadLabel) modelDownloadLabel.textContent = '开始下载...';
    pollModelStatus();
  } catch (error) {
    if (modelDownloadLabel) modelDownloadLabel.textContent = `启动下载失败：${error.message || error}`;
  }
}

if (downloadSpeakerModel) downloadSpeakerModel.addEventListener('click', () => startModelDownload('speaker'));
if (downloadWhisperModel) downloadWhisperModel.addEventListener('click', () => startModelDownload('whisper'));

// 进入页面时若已有下载在进行，恢复进度显示
fetch('/api/models/status')
  .then((res) => res.json())
  .then((s) => { if (s.running) { renderModelStatus(s); setModelButtonsDisabled(true); pollModelStatus(); } })
  .catch(() => {});
