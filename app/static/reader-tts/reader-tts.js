(function () {
  'use strict';

  const ASSET_BASE = new URL('/reader-assets/', window.location.origin).href;
  const MANIFEST_URL = `${ASSET_BASE}voices.v1.json`;
  const WORKER_URL = `${ASSET_BASE}tts-worker.js`;
  const CACHE_NAME = 'reader-tts-models-v1';
  const DB_NAME = 'reader-tts-v1';
  const DB_STORE = 'voices';
  const MAX_INSTALLED = 3;

  const $ = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  function normalizeVietnameseText(value) {
    let text = String(value || '')
      .replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, ' ')
      .replace(/[“”„‟]/g, '"')
      .replace(/[‘’‚‛]/g, "'")
      .replace(/[—–]/g, ' - ')
      .replace(/\s+/g, ' ')
      .trim();
    if (!text) return '';

    const digits = ['không', 'một', 'hai', 'ba', 'bốn', 'năm', 'sáu', 'bảy', 'tám', 'chín'];
    function underThousand(number) {
      const n = Math.max(0, Math.floor(Number(number)));
      if (n < 10) return digits[n];
      if (n < 20) return n === 15 ? 'mười lăm' : `mười ${digits[n - 10]}`;
      if (n < 100) {
        const ten = Math.floor(n / 10); const unit = n % 10;
        if (!unit) return `${digits[ten]} mươi`;
        const unitWord = unit === 1 ? 'mốt' : unit === 4 ? 'tư' : unit === 5 ? 'lăm' : digits[unit];
        return `${digits[ten]} mươi ${unitWord}`;
      }
      const hundred = Math.floor(n / 100); const rest = n % 100;
      if (!rest) return `${digits[hundred]} trăm`;
      return `${digits[hundred]} trăm ${rest < 10 ? `lẻ ${digits[rest]}` : underThousand(rest)}`;
    }
    function numberToWords(raw) {
      const normalized = String(raw).replace(/^0+(?=\d)/, '');
      const n = Number(normalized);
      if (!Number.isFinite(n) || n > 999999999999) return normalized.split('').map(d => digits[Number(d)] || d).join(' ');
      if (n < 1000) return underThousand(n);
      const groups = [];
      let rest = Math.floor(n);
      const units = ['', 'nghìn', 'triệu', 'tỷ'];
      let groupIndex = 0;
      while (rest > 0) { groups.push(rest % 1000); rest = Math.floor(rest / 1000); groupIndex += 1; }
      const output = [];
      for (let index = groups.length - 1; index >= 0; index -= 1) {
        const group = groups[index];
        if (!group) continue;
        output.push(underThousand(group));
        if (units[index]) output.push(units[index]);
      }
      return output.join(' ');
    }

    text = text
      .replace(/(\d+)\s*%/g, (_, n) => `${numberToWords(n)} phần trăm`)
      .replace(/(\d+)\s*(?:đồng|VND|vnđ|đ)\b/gi, (_, n) => `${numberToWords(n)} đồng`)
      .replace(/\$\s*(\d+)/g, (_, n) => `${numberToWords(n)} đô la`)
      .replace(/(\d{1,2})[/:](\d{1,2})(?:[/:](\d{1,2}))?/g, (_, h, m, s) => `${numberToWords(h)} giờ ${numberToWords(m)} phút${s ? ` ${numberToWords(s)} giây` : ''}`)
      .replace(/(\d{1,2})[/-](\d{1,2})[/-](\d{4})/g, (_, d, m, y) => `ngày ${numberToWords(d)} tháng ${numberToWords(m)} năm ${numberToWords(y)}`)
      .replace(/\b(\d{1,2})[/-](\d{1,2})\b/g, (_, d, m) => `${numberToWords(d)} tháng ${numberToWords(m)}`)
      .replace(/\b([IVXLCDM]{1,8})\b/g, (match) => {
        const values = { I: 1, V: 5, X: 10, L: 50, C: 100, D: 500, M: 1000 };
        let total = 0; let previous = 0;
        for (const char of match.split('').reverse()) { const current = values[char]; total += current < previous ? -current : current; previous = current; }
        return total > 0 && total <= 30 ? numberToWords(String(total)) : match;
      })
      .replace(/\b(\d{1,12})(?:[.,](\d{1,2}))?\b/g, (_, whole, decimal) => decimal ? `${numberToWords(whole)} phẩy ${numberToWords(decimal)}` : numberToWords(whole));

    return text.replace(/\s+([,.!?;:])/g, '$1').replace(/\s{2,}/g, ' ').trim();
  }

  function segmentText(text) {
    const normalized = String(text || '').replace(/\r\n?/g, '\n').trim();
    if (!normalized) return [];
    const result = [];
    let sentenceIndex = 0;
    const segmenter = typeof Intl !== 'undefined' && Intl.Segmenter ? new Intl.Segmenter('vi', { granularity: 'sentence' }) : null;
    normalized.split(/\n\s*\n/).forEach((paragraph, paragraphIndex) => {
      const pieces = segmenter
        ? Array.from(segmenter.segment(paragraph), item => item.segment.trim()).filter(Boolean)
        : paragraph.match(/[^.!?…]+(?:[.!?…]+|$)/g)?.map(item => item.trim()).filter(Boolean) || [paragraph.trim()];
      pieces.forEach(originalText => {
        const speechText = normalizeVietnameseText(originalText);
        if (speechText) result.push({ sentenceIndex, paragraphIndex, originalText, speechText });
        sentenceIndex += 1;
      });
    });
    return result;
  }

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes)) return 'Không rõ dung lượng';
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function sha256(buffer) {
    return crypto.subtle.digest('SHA-256', buffer).then(hash => Array.from(new Uint8Array(hash)).map(byte => byte.toString(16).padStart(2, '0')).join(''));
  }

  class VoiceStore {
    constructor() { this.db = null; this.memory = new Map(); }

    async openDb() {
      if (this.db || !window.indexedDB) return this.db;
      this.db = await new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_NAME, 1);
        request.onupgradeneeded = event => {
          const database = event.target.result;
          if (!database.objectStoreNames.contains(DB_STORE)) database.createObjectStore(DB_STORE, { keyPath: 'id' });
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      }).catch(() => null);
      return this.db;
    }

    async getMeta(id) {
      const db = await this.openDb();
      if (!db) return this.memory.get(id) || null;
      return new Promise(resolve => {
        const request = db.transaction(DB_STORE, 'readonly').objectStore(DB_STORE).get(id);
        request.onsuccess = () => { if (request.result) this.memory.set(id, request.result); resolve(request.result || null); };
        request.onerror = () => resolve(null);
      });
    }

    async putMeta(meta) {
      this.memory.set(meta.id, meta);
      const db = await this.openDb();
      if (!db) return;
      await new Promise(resolve => {
        const request = db.transaction(DB_STORE, 'readwrite').objectStore(DB_STORE).put(meta);
        request.onsuccess = request.onerror = () => resolve();
      });
    }

    async deleteMeta(id) {
      this.memory.delete(id);
      const db = await this.openDb();
      if (!db) return;
      await new Promise(resolve => {
        const request = db.transaction(DB_STORE, 'readwrite').objectStore(DB_STORE).delete(id);
        request.onsuccess = request.onerror = () => resolve();
      });
    }

    cacheKey(voice) { return `${ASSET_BASE}model/${voice.id}/${voice.revision}`; }

    async isInstalled(voice) {
      if (!('caches' in window)) return false;
      const cache = await caches.open(CACHE_NAME);
      const response = await cache.match(this.cacheKey(voice));
      if (!response) { await this.deleteMeta(voice.id); return false; }
      const meta = await this.getMeta(voice.id);
      if (!meta || meta.revision !== voice.revision || meta.sha256 !== voice.sha256) {
        await cache.delete(this.cacheKey(voice));
        await this.deleteMeta(voice.id);
        return false;
      }
      return true;
    }

    async installed(voices) {
      const result = [];
      for (const voice of voices) if (await this.isInstalled(voice)) result.push(voice);
      return result;
    }

    async remove(voice) {
      if ('caches' in window) await (await caches.open(CACHE_NAME)).delete(this.cacheKey(voice));
      await this.deleteMeta(voice.id);
    }

    async download(voice, { protectedId = '', onProgress = () => {} } = {}) {
      if (!('caches' in window)) throw new Error('Trình duyệt không hỗ trợ Cache Storage.');
      if (await this.isInstalled(voice)) {
        await this.touch(voice.id, voice.revision, voice.sha256);
        return;
      }
      const installed = await this.installed([...(this.manifest?.voices || [])]);
      if (installed.length >= MAX_INSTALLED) {
        const candidates = installed.filter(item => item.id !== protectedId).sort((a, b) => (this.getLastUsed(b.id) - this.getLastUsed(a.id)));
        if (!candidates.length) throw new Error('Đang dùng đủ ba voice; hãy dừng phát trước khi tải voice mới.');
        await this.remove(candidates[candidates.length - 1]);
      }

      let response = null; let lastError = null;
      for (const url of [voice.modelUrl, voice.fallbackUrl].filter(Boolean)) {
        try {
          response = await fetch(url, { mode: 'cors', cache: 'no-store' });
          if (response.ok) break;
          lastError = new Error(`HTTP ${response.status}`);
        } catch (error) { lastError = error; }
      }
      if (!response || !response.ok) throw new Error(`Không tải được voice ${voice.displayName}: ${lastError?.message || 'lỗi mạng'}`);
      const total = Number(response.headers.get('Content-Length')) || voice.sizeBytes;
      const reader = response.body?.getReader();
      const chunks = []; let received = 0;
      if (reader) {
        while (true) {
          const part = await reader.read();
          if (part.done) break;
          chunks.push(part.value); received += part.value.byteLength;
          onProgress(total ? received / total : 0, received, total);
        }
      } else {
        const data = new Uint8Array(await response.arrayBuffer());
        chunks.push(data); received = data.byteLength; onProgress(1, received, total);
      }
      const bytes = new Uint8Array(received); let offset = 0;
      chunks.forEach(chunk => { bytes.set(chunk, offset); offset += chunk.byteLength; });
      if (voice.sizeBytes && bytes.byteLength !== voice.sizeBytes) throw new Error('Kích thước model không khớp manifest.');
      const digest = await sha256(bytes.buffer);
      if (voice.sha256 && digest !== voice.sha256) throw new Error('SHA-256 voice không khớp; bản tải đã bị hủy.');
      const cache = await caches.open(CACHE_NAME);
      await cache.put(this.cacheKey(voice), new Response(bytes, { headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': String(bytes.byteLength) } }));
      await this.touch(voice.id, voice.revision, voice.sha256);
      onProgress(1, bytes.byteLength, bytes.byteLength);
    }

    getLastUsed(id) { return this.memory.get(id)?.lastUsedAt || 0; }

    async touch(id, revision, checksum) { await this.putMeta({ id, revision, sha256: checksum, lastUsedAt: Date.now() }); }

    async getBuffer(voice) {
      const cache = await caches.open(CACHE_NAME);
      const response = await cache.match(this.cacheKey(voice));
      if (!response) throw new Error('Model chưa được cài.');
      await this.touch(voice.id, voice.revision, voice.sha256);
      return response.arrayBuffer();
    }
  }

  function createReaderAudioController(options) {
    const store = new VoiceStore();
    const state = {
      manifest: null, currentChapterIndex: null, sentences: [], cursor: 0, selectedVoiceId: '',
      worker: null, workerReady: false, workerVoiceId: '', workerLoad: null, pending: new Map(), requestId: 0,
      audioContext: null, playing: false, paused: false, pausedOffset: 0, pausedIndex: 0, speed: 1,
      token: 0, requested: new Set(), prepared: new Map(), scheduled: new Map(), playhead: 0, lastScheduled: -1,
      active: null, nextChapterPending: false, status: 'NO_VOICE'
    };

    const player = $ui();
    let managerOpen = false;

    function notify(message, tone = '') {
      if (typeof options.onStatus === 'function') options.onStatus(message, tone);
      const status = player.status;
      status.textContent = message;
      status.dataset.tone = tone;
    }

    function selectedVoice() { return state.manifest?.voices.find(voice => voice.id === state.selectedVoiceId) || state.manifest?.voices.find(voice => voice.isDefault) || null; }

    function setStatus(status, message = '') {
      state.status = status;
      player.status.textContent = message || ({ NO_VOICE: 'Chưa cài voice', LOADING_MODEL: 'Đang khởi tạo model…', GENERATING: 'Đang chuẩn bị câu đọc…', PLAYING: 'Đang phát', PAUSED: 'Đã tạm dừng', BUFFERING: 'Đang đệm câu tiếp theo…', ERROR: 'Có lỗi TTS' }[status] || '');
      player.status.dataset.tone = status === 'ERROR' ? 'error' : '';
      player.playButton.setAttribute('aria-label', state.playing ? 'Tạm dừng đọc audio' : 'Phát audio');
      player.playButton.textContent = state.playing ? 'Ⅱ' : '▶';
    }

    function readSelectedVoice() {
      try { return localStorage.getItem('reader.tts.voice') || ''; } catch (_) { return ''; }
    }

    function saveSelectedVoice(id) { try { localStorage.setItem('reader.tts.voice', id); } catch (_) { /* private mode */ } }

    function progressKey() { const id = options.getNovelId?.(); return id ? `reader.audioProgress.${id}` : ''; }

    function readProgress() {
      try { const key = progressKey(); return key ? JSON.parse(localStorage.getItem(key) || 'null') : null; } catch (_) { return null; }
    }

    function saveProgress(offset = 0) {
      const key = progressKey();
      if (!key || state.currentChapterIndex === null || !state.sentences.length) return;
      try {
        localStorage.setItem(key, JSON.stringify({ chapterIndex: state.currentChapterIndex, sentenceIndex: state.active?.index ?? state.cursor, offsetSeconds: Math.max(0, offset), voiceId: state.selectedVoiceId, speed: state.speed, updatedAt: new Date().toISOString() }));
      } catch (_) { /* storage unavailable */ }
    }

    function ensureAudioContext() {
      if (!state.audioContext) state.audioContext = new AudioContext();
      return state.audioContext;
    }

    function cleanupWorker() {
      if (state.worker) state.worker.terminate();
      state.worker = null; state.workerReady = false; state.workerVoiceId = '';
      state.pending.forEach(item => item.reject(new Error('TTS session đã thay đổi.')));
      state.pending.clear(); state.workerLoad = null;
    }

    function ensureWorker() {
      if (state.worker) return state.worker;
      if (!window.Worker) throw new Error('Trình duyệt không hỗ trợ Web Worker.');
      state.worker = new Worker(WORKER_URL, { type: 'module', name: 'reader-tts' });
      state.worker.addEventListener('message', event => {
        const message = event.data || {};
        if (message.type === 'READY') {
          state.workerReady = true;
          state.workerVoiceId = message.voiceId;
          state.workerLoad?.resolve(); state.workerLoad = null;
          return;
        }
        if (message.type === 'AUDIO' || message.type === 'ERROR') {
          const request = state.pending.get(message.requestId);
          if (!request) return;
          state.pending.delete(message.requestId);
          if (message.type === 'ERROR') request.reject(new Error(message.message || 'Không tạo được audio.'));
          else request.resolve({ samples: new Float32Array(message.audio), sampleRate: message.sampleRate });
        }
      });
      state.worker.addEventListener('error', error => {
        state.workerReady = false;
        state.workerLoad?.reject(new Error(error.message || 'TTS worker bị lỗi.'));
        state.workerLoad = null;
        notify('TTS worker gặp lỗi. Hãy thử lại voice đang cài.', 'error');
      });
      return state.worker;
    }

    async function ensureVoiceLoaded() {
      const voice = selectedVoice();
      if (!voice) throw new Error('Chưa có voice trong manifest.');
      if (!(await store.isInstalled(voice))) throw new Error(`Voice ${voice.displayName} chưa được tải. Bấm “Tải giọng” để cài.`);
      if (state.workerReady && state.workerVoiceId === voice.id) return voice;
      setStatus('LOADING_MODEL');
      cleanupWorker();
      const worker = ensureWorker();
      const [modelBuffer, configResponse] = await Promise.all([
        store.getBuffer(voice),
        fetch(state.manifest.configUrl, { cache: 'force-cache' }).then(response => { if (!response.ok) throw new Error('Không tải được Piper config.'); return response.json(); })
      ]);
      const loaded = new Promise((resolve, reject) => { state.workerLoad = { resolve, reject }; });
      worker.postMessage({ type: 'LOAD', voiceId: voice.id, modelBuffer, config: configResponse }, [modelBuffer]);
      try { await loaded; } catch (error) { cleanupWorker(); throw error; }
      return voice;
    }

    function audioBuffer(samples, sampleRate) {
      const context = ensureAudioContext();
      const buffer = context.createBuffer(1, samples.length, sampleRate);
      buffer.copyToChannel(samples, 0);
      return buffer;
    }

    function requestSpeech(text, speed, token) {
      const requestId = ++state.requestId;
      return new Promise((resolve, reject) => {
        state.pending.set(requestId, { resolve, reject, token });
        ensureWorker().postMessage({ type: 'SYNTHESIZE', requestId, text, speed });
      });
    }

    function stopSources() {
      state.scheduled.forEach(entry => { try { entry.source.stop(); } catch (_) { /* already ended */ } });
      state.scheduled.clear();
      if (state.active?.source) { try { state.active.source.stop(); } catch (_) { /* already ended */ } }
    }

    function clearPlayback({ preserveCursor = true } = {}) {
      state.token += 1;
      stopSources();
      state.requested.clear(); state.prepared.clear(); state.playhead = 0; state.lastScheduled = -1; state.active = null;
      if (!preserveCursor) { state.cursor = 0; state.pausedIndex = 0; state.pausedOffset = 0; }
      state.playing = false; state.paused = false;
      if (state.status !== 'ERROR') setStatus(state.selectedVoiceId ? 'READY' : 'NO_VOICE');
    }

    function highlight(index) {
      document.querySelectorAll('.tts-sentence.is-speaking').forEach(node => node.classList.remove('is-speaking'));
      const node = document.querySelector(`[data-tts-sentence="${index}"]`);
      if (!node) return;
      node.classList.add('is-speaking');
      const rect = node.getBoundingClientRect();
      if (rect.top < 80 || rect.bottom > window.innerHeight - 130) node.scrollIntoView({ behavior: 'smooth', block: 'center' });
      player.counter.textContent = `Câu ${index + 1} / ${state.sentences.length}`;
    }

    function schedulePrepared(token) {
      if (!state.playing || token !== state.token) return;
      const context = ensureAudioContext();
      let nextIndex = state.lastScheduled + 1;
      while (state.prepared.has(nextIndex)) {
        const prepared = state.prepared.get(nextIndex);
        state.prepared.delete(nextIndex);
        const start = Math.max(context.currentTime + 0.06, state.playhead || context.currentTime + 0.06);
        const source = context.createBufferSource();
        source.buffer = prepared.buffer;
        source.playbackRate.value = state.speed;
        source.connect(context.destination);
        const entry = { index: nextIndex, source, buffer: prepared.buffer, offset: prepared.offset || 0, start, token };
        state.scheduled.set(nextIndex, entry);
        source.onended = () => {
          if (entry.token !== state.token) return;
          state.scheduled.delete(entry.index);
          if (state.active === entry) {
            state.cursor = entry.index + 1;
            state.active = null;
            saveProgress(0);
          }
          if (entry.index >= state.sentences.length - 1) {
            state.playing = false;
            state.cursor = state.sentences.length - 1;
            saveProgress(0);
            handleChapterEnd();
          } else {
            pump(token);
          }
        };
        source.start(start, entry.offset);
        const delay = Math.max(0, (start - context.currentTime) * 1000);
        window.setTimeout(() => {
          if (entry.token !== state.token || !state.scheduled.has(entry.index)) return;
          state.active = entry; state.cursor = entry.index; state.pausedOffset = 0; highlight(entry.index); setStatus('PLAYING');
        }, delay);
        state.playhead = start + Math.max(0, (prepared.buffer.duration - entry.offset) / state.speed);
        state.lastScheduled = nextIndex;
        nextIndex += 1;
      }
    }

    function pump(token = state.token) {
      if (!state.playing || token !== state.token) return;
      const start = Math.max(0, state.cursor);
      for (let index = start; index < Math.min(state.sentences.length, start + 3); index += 1) {
        if (state.requested.has(index) || state.prepared.has(index) || state.scheduled.has(index)) continue;
        const sentence = state.sentences[index];
        state.requested.add(index);
        requestSpeech(sentence.speechText, state.speed, token).then(result => {
          state.requested.delete(index);
          if (token !== state.token) return;
          state.prepared.set(index, { buffer: audioBuffer(result.samples, result.sampleRate), offset: index === state.pausedIndex ? state.pausedOffset : 0 });
          setStatus('PLAYING');
          schedulePrepared(token);
        }).catch(async error => {
          state.requested.delete(index);
          if (token !== state.token) return;
          const fallback = state.manifest?.voices.find(item => item.isDefault);
          if (fallback && state.selectedVoiceId !== fallback.id && await store.isInstalled(fallback)) {
            state.selectedVoiceId = fallback.id; saveSelectedVoice(fallback.id); player.voice.textContent = fallback.displayName;
            notify('Voice đang chọn lỗi; đã chuyển về ' + fallback.displayName + '.', 'error');
            clearPlayback(); play(); return;
          }
          state.playing = false; setStatus('ERROR', error.message || 'Không tạo được audio.');
          notify(error.message || 'Không tạo được audio.', 'error');
        });
      }
      schedulePrepared(token);
      if (state.playing && !state.scheduled.size && state.requested.size) setStatus('BUFFERING');
    }

    async function play() {
      if (!state.sentences.length) { notify('Chương hiện tại chưa có câu để đọc.', 'error'); return; }
      try {
        const voice = await ensureVoiceLoaded();
        const context = ensureAudioContext();
        await context.resume();
        const wasPaused = state.paused;
        state.selectedVoiceId = voice.id; saveSelectedVoice(voice.id);
        state.playing = true; state.paused = false; state.status = 'GENERATING';
        if (state.lastScheduled < 0) {
          state.cursor = wasPaused ? state.pausedIndex : Math.min(state.cursor, state.sentences.length - 1);
          state.lastScheduled = state.cursor - 1;
          state.prepared.clear(); state.requested.clear(); state.playhead = 0;
        }
        setStatus('GENERATING'); pump(state.token);
        renderVoiceList();
      } catch (error) {
        const fallback = state.manifest?.voices.find(item => item.isDefault);
        if (fallback && state.selectedVoiceId !== fallback.id && await store.isInstalled(fallback)) {
          state.selectedVoiceId = fallback.id; saveSelectedVoice(fallback.id); player.voice.textContent = fallback.displayName;
          notify('Voice đang chọn lỗi; đã chuyển về ' + fallback.displayName + '.', 'error');
          return play();
        }
        state.playing = false; setStatus('ERROR', error.message); notify(error.message, 'error'); openManager();
      }
    }

    function pause() {
      if (!state.playing) return;
      const context = ensureAudioContext();
      const entry = state.active;
      if (entry) state.pausedOffset = Math.max(0, (context.currentTime - entry.start) * state.speed + entry.offset);
      state.pausedIndex = entry?.index ?? state.cursor;
      saveProgress(state.pausedOffset);
      state.paused = true; state.playing = false;
      stopSources(); state.prepared.clear(); state.requested.clear(); state.playhead = 0; state.lastScheduled = -1; state.active = null;
      setStatus('PAUSED'); highlight(state.pausedIndex);
    }

    function restartAt(index, offset = 0) {
      state.token += 1; stopSources(); state.requested.clear(); state.prepared.clear(); state.playhead = 0; state.lastScheduled = -1; state.active = null;
      state.cursor = Math.max(0, Math.min(index, state.sentences.length - 1)); state.pausedIndex = state.cursor; state.pausedOffset = offset; state.lastScheduled = state.cursor - 1; state.playing = true; state.paused = false; setStatus('GENERATING'); pump(state.token);
    }

    function previous() {
      if (!state.sentences.length) return;
      const index = state.active?.index ?? state.cursor;
      if (state.active && state.pausedOffset > 2) restartAt(index, 0);
      else restartAt(Math.max(0, index - 1), 0);
      saveProgress(0);
    }

    function next() {
      if (!state.sentences.length) return;
      const index = state.active?.index ?? state.cursor;
      if (index >= state.sentences.length - 1) { handleChapterEnd(); return; }
      restartAt(index + 1, 0); saveProgress(0);
    }

    function handleChapterEnd() {
      const nextIndex = options.getNextChapterIndex?.();
      if (!nextIndex || !options.loadChapter) { setStatus('READY', 'Đã hết chương'); return; }
      state.nextChapterPending = true;
      setStatus('BUFFERING', 'Đang mở chương tiếp theo…');
      Promise.resolve(options.loadChapter(nextIndex, { skipRestore: true, fromAudio: true })).then(ok => {
        if (!ok) { state.nextChapterPending = false; setStatus('ERROR', 'Không mở được chương tiếp theo.'); notify('Không mở được chương tiếp theo. Bạn có thể bấm thử lại.', 'error'); }
      });
    }

    function onChapterLoading() {
      if (state.playing || state.paused) clearPlayback();
    }

    function onChapterRendered(chapterIndex, rawText) {
      const wasPending = state.nextChapterPending;
      const oldChapter = state.currentChapterIndex;
      state.currentChapterIndex = chapterIndex;
      state.sentences = segmentText(rawText);
      document.body.classList.add('tts-open');
      player.player.classList.add('is-visible');
      state.nextChapterPending = false;
      const saved = readProgress();
      if (wasPending) { state.cursor = 0; state.pausedOffset = 0; }
      else if (saved?.chapterIndex === chapterIndex) { state.cursor = Math.min(Number(saved.sentenceIndex) || 0, Math.max(0, state.sentences.length - 1)); state.pausedIndex = state.cursor; state.pausedOffset = Number(saved.offsetSeconds) || 0; state.speed = Math.max(.75, Math.min(1.5, Number(saved.speed) || state.speed)); player.speed.value = String(state.speed); }
      else state.cursor = 0;
      player.counter.textContent = state.sentences.length ? `Câu ${state.cursor + 1} / ${state.sentences.length}` : 'Không có câu';
      if (oldChapter !== null && oldChapter !== chapterIndex) clearPlayback({ preserveCursor: true });
      if (wasPending) play();
      renderVoiceList();
    }

    function onChapterFailed() { state.nextChapterPending = false; clearPlayback(); setStatus('ERROR', 'Không thể tiếp tục audio.'); }

    async function selectVoice(voice) {
      if (!(await store.isInstalled(voice))) return;
      state.selectedVoiceId = voice.id; saveSelectedVoice(voice.id); await store.touch(voice.id, voice.revision, voice.sha256); player.voice.textContent = voice.displayName; renderVoiceList();
      if (state.playing) { clearPlayback(); play(); } else setStatus('READY', `Voice: ${voice.displayName}`);
    }

    async function downloadVoice(voice, row) {
      const button = row.querySelector('.tts-voice-action'); const progress = row.querySelector('.tts-voice-progress span');
      button.disabled = true; button.textContent = 'Đang tải…';
      try {
        await store.download(voice, { protectedId: state.workerVoiceId, onProgress: (ratio, received, total) => { progress.style.width = `${Math.round(ratio * 100)}%`; row.querySelector('.tts-voice-meta').textContent = `${formatBytes(received)} / ${formatBytes(total)} · ${voice.source === 'r2' ? 'Cloudflare R2' : 'Hugging Face'}`; } });
        notify(`${voice.displayName} đã được cài.`, ''); renderVoiceList();
      } catch (error) { button.disabled = false; button.textContent = 'Thử lại'; notify(error.message, 'error'); }
    }

    async function renderVoiceList() {
      if (!state.manifest) return;
      player.voiceList.replaceChildren();
      const installed = new Set((await store.installed(state.manifest.voices)).map(voice => voice.id));
      state.manifest.voices.forEach(voice => {
        const row = $uiVoiceRow(voice, installed.has(voice.id));
        player.voiceList.append(row);
      });
    }

    function openManager() { managerOpen = true; player.backdrop.classList.add('is-open'); player.backdrop.setAttribute('aria-hidden', 'false'); renderVoiceList(); }
    function closeManager() { managerOpen = false; player.backdrop.classList.remove('is-open'); player.backdrop.setAttribute('aria-hidden', 'true'); }

    function $uiVoiceRow(voice, installed) {
      const row = $voiceRowBase(voice);
      const action = row.querySelector('.tts-voice-action');
      if (!installed) action.textContent = 'Tải giọng';
      else if (state.selectedVoiceId === voice.id) { action.textContent = 'Đang dùng'; action.disabled = true; row.classList.add('is-active'); }
      else action.textContent = 'Chọn';
      action.addEventListener('click', () => installed ? selectVoice(voice) : downloadVoice(voice, row));
      return row;
    }

    function initUi() {
      document.body.append(player.player, player.backdrop);
      player.playButton.addEventListener('click', () => state.playing ? pause() : play());
      player.prevButton.addEventListener('click', previous);
      player.nextButton.addEventListener('click', next);
      player.voiceButton.addEventListener('click', openManager);
      player.speed.addEventListener('change', () => { state.speed = Math.max(.75, Math.min(1.5, Number(player.speed.value) || 1)); saveProgress(state.pausedOffset); if (state.playing) { pause(); play(); } });
      player.closeButton.addEventListener('click', closeManager);
      player.backdrop.addEventListener('click', event => { if (event.target === player.backdrop) closeManager(); });
      document.addEventListener('keydown', event => { if (event.key === 'Escape' && managerOpen) closeManager(); });
      setStatus('NO_VOICE');
    }

    function $voiceRowBase(voice) {
      const row = $('div', 'tts-voice-row');
      const copy = $('div', 'tts-voice-copy');
      copy.append($('div', 'tts-voice-name', `${voice.displayName}${voice.isDefault ? ' · Mặc định' : ''}`));
      const meta = $('div', 'tts-voice-meta', `${formatBytes(voice.sizeBytes)} · ${voice.source === 'r2' ? 'Cloudflare R2' : 'Hugging Face'}`);
      const progress = $('div', 'tts-voice-progress'); progress.append($('span'));
      copy.append(meta, progress);
      const action = $('button', 'tts-voice-action'); action.type = 'button';
      row.append(copy, action); return row;
    }

    async function init() {
      initUi();
      try {
        const response = await fetch(MANIFEST_URL, { cache: 'no-cache' });
        if (!response.ok) throw new Error('Không tải được danh sách voice.');
        state.manifest = await response.json();
        store.manifest = state.manifest;
        state.selectedVoiceId = readSelectedVoice() || state.manifest.voices.find(voice => voice.isDefault)?.id || state.manifest.voices[0]?.id || '';
        player.voice.textContent = state.manifest.voices.find(voice => voice.id === state.selectedVoiceId)?.displayName || 'Chưa chọn voice';
        renderVoiceList();
      } catch (error) { setStatus('ERROR', error.message); notify(error.message, 'error'); }
    }

    init();
    return { onChapterLoading, onChapterRendered, onChapterFailed, openManager, play, pause, previous, next, isPlaying: () => state.playing };
  }

  function createPlayerUi() {
    const player = $('section', 'tts-player');
    player.setAttribute('aria-label', 'Trình phát audiobook');
    const brand = $('div', 'tts-player__brand');
    brand.append($('div', 'tts-player__eyebrow', 'Nghe chương này'), $('div', 'tts-player__voice', 'Đang tải voice…'), $('div', 'tts-player__status', ''));
    const controls = $('div', 'tts-player__controls');
    const prevButton = $('button', 'tts-control', '‹'); prevButton.type = 'button'; prevButton.title = 'Câu trước'; prevButton.setAttribute('aria-label', 'Câu trước');
    const playButton = $('button', 'tts-control tts-control--primary', '▶'); playButton.type = 'button'; playButton.title = 'Phát audio'; playButton.setAttribute('aria-label', 'Phát audio');
    const nextButton = $('button', 'tts-control', '›'); nextButton.type = 'button'; nextButton.title = 'Câu sau'; nextButton.setAttribute('aria-label', 'Câu sau');
    controls.append(prevButton, playButton, nextButton);
    const tools = $('div', 'tts-player__tools');
    const speedLabel = $('label', 'tts-speed-label', 'Tốc độ'); const speed = $('select', 'tts-speed'); speed.setAttribute('aria-label', 'Tốc độ đọc');
    [.75, 1, 1.25, 1.5].forEach(value => { const option = $('option', '', `${value}x`); option.value = String(value); if (value === 1) option.selected = true; speed.append(option); }); speedLabel.append(speed);
    const voiceButton = $('button', 'text-button', 'Tải giọng'); voiceButton.type = 'button'; voiceButton.setAttribute('aria-label', 'Mở quản lý voice');
    const counter = $('span', 'tts-player__status', 'Chưa có chương');
    tools.append(speedLabel, counter, voiceButton);
    const progress = $('div', 'tts-progress'); progress.setAttribute('aria-hidden', 'true'); progress.append($('span'));
    player.append(brand, controls, tools, progress);

    const backdrop = $('div', 'tts-dialog-backdrop'); backdrop.setAttribute('aria-hidden', 'true');
    const dialog = $('section', 'tts-dialog'); dialog.setAttribute('role', 'dialog'); dialog.setAttribute('aria-modal', 'true'); dialog.setAttribute('aria-labelledby', 'tts-dialog-title');
    const head = $('header', 'tts-dialog__head'); const heading = $('div'); heading.append($('h2', '', 'Voice tiếng Việt'), $('p', '', 'Tải từng giọng về thiết bị để tổng hợp trực tiếp trong trình duyệt. Tối đa ba voice được giữ lại; voice ít dùng nhất sẽ tự bị dọn.'));
    const closeButton = $('button', 'tts-dialog__close', '×'); closeButton.type = 'button'; closeButton.setAttribute('aria-label', 'Đóng quản lý voice'); head.append(heading, closeButton);
    const title = heading.querySelector('h2'); title.id = 'tts-dialog-title';
    const voiceList = $('div', 'tts-voice-list'); voiceList.setAttribute('aria-live', 'polite');
    const foot = $('div', 'tts-dialog__foot', 'Nguồn voice: doof-ferb/nghitts-copy · Sử dụng cá nhân/phi thương mại. Minh Quang ưu tiên từ Cloudflare R2.');
    dialog.append(head, voiceList, foot); backdrop.append(dialog);
    return { player, backdrop, voiceList, voice: brand.querySelector('.tts-player__voice'), status: brand.querySelector('.tts-player__status'), playButton, prevButton, nextButton, speed, voiceButton, counter, closeButton };
  }

  function $ui() {
    const ui = createPlayerUi();
    return ui;
  }

  function $voiceRowBase(voice) {
    const row = $('div', 'tts-voice-row');
    const copy = $('div', 'tts-voice-copy');
    copy.append($('div', 'tts-voice-name', `${voice.displayName}${voice.isDefault ? ' · Mặc định' : ''}`));
    copy.append($('div', 'tts-voice-meta', `${formatBytes(voice.sizeBytes)} · ${voice.source === 'r2' ? 'Cloudflare R2' : 'Hugging Face'}`));
    const progress = $('div', 'tts-voice-progress'); progress.append($('span')); copy.append(progress);
    const action = $('button', 'tts-voice-action'); action.type = 'button'; row.append(copy, action); return row;
  }

  window.ReaderTts = { createReaderAudioController, normalizeVietnameseText, segmentText };
})();
