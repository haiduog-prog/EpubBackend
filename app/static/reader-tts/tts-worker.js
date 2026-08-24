import * as ort from './ort.min.mjs';
import { phonemize } from './phonemizer.js';

let session = null;
let voiceConfig = null;
let activeVoiceId = null;
let taskQueue = [];
let processing = false;
let runtimeReady = false;

function post(type, payload = {}, transfer = []) {
  self.postMessage({ type, ...payload }, transfer);
}

function idValue(value) {
  return Array.isArray(value) ? value[0] : value;
}

function toPhonemeIds(phonemeText) {
  const map = voiceConfig?.phoneme_id_map || {};
  const pad = idValue(map['_']);
  const bos = idValue(map['^']);
  const eos = idValue(map['$']);
  if (![pad, bos, eos].every(Number.isInteger)) throw new Error('Piper config thiếu phoneme_id_map.');

  const ids = [bos, pad];
  for (const symbol of Array.from(phonemeText.normalize('NFD'))) {
    const value = idValue(map[symbol]);
    if (Number.isInteger(value)) {
      ids.push(value, pad);
    }
  }
  ids.push(eos);
  return ids;
}

async function initRuntime() {
  if (runtimeReady) return;
  ort.env.wasm.wasmPaths = {
    mjs: new URL('./ort-wasm-simd-threaded.mjs', import.meta.url).href,
    wasm: new URL('./ort-wasm-simd-threaded.wasm', import.meta.url).href
  };
  ort.env.wasm.numThreads = 1;
  ort.env.wasm.proxy = false;
  runtimeReady = true;
}

async function loadVoice({ voiceId, modelBuffer, config }) {
  await initRuntime();
  if (session) {
    try { session.release(); } catch (_) { /* older ORT builds may not expose release */ }
    session = null;
  }
  voiceConfig = config;
  activeVoiceId = voiceId;
  session = await ort.InferenceSession.create(modelBuffer, {
    executionProviders: ['wasm'],
    graphOptimizationLevel: 'all'
  });
  post('READY', { voiceId });
}

async function synthesize({ requestId, text, speed }) {
  if (!session || !voiceConfig) throw new Error('Voice chưa được khởi tạo.');
  const phrases = await phonemize(text, voiceConfig.espeak?.voice || 'vi');
  const phonemeText = Array.isArray(phrases) ? phrases.join(', ') : String(phrases || '');
  const ids = toPhonemeIds(phonemeText);
  const lengthScale = 1 / Math.max(.75, Math.min(1.5, Number(speed) || 1));
  const inference = voiceConfig.inference || {};
  const input = new ort.Tensor('int64', BigInt64Array.from(ids, value => BigInt(value)), [1, ids.length]);
  const inputLengths = new ort.Tensor('int64', BigInt64Array.from([BigInt(ids.length)]), [1]);
  const scales = new ort.Tensor('float32', Float32Array.from([
    Number(inference.noise_scale ?? .667),
    lengthScale,
    Number(inference.noise_w ?? .8)
  ]), [3]);
  const inputs = { input, input_lengths: inputLengths, scales };
  if (Number(voiceConfig.num_speakers) > 1) {
    inputs.sid = new ort.Tensor('int64', BigInt64Array.from([0n]), [1]);
  }
  const outputs = await session.run(inputs);
  const output = outputs.output || outputs[Object.keys(outputs)[0]];
  if (!output?.data) throw new Error('Piper không trả về audio.');
  const audio = Float32Array.from(output.data);
  post('AUDIO', {
    requestId,
    voiceId: activeVoiceId,
    sampleRate: Number(voiceConfig.audio?.sample_rate || 22050),
    audio: audio.buffer
  }, [audio.buffer]);
}

async function drain() {
  if (processing) return;
  processing = true;
  while (taskQueue.length) {
    const task = taskQueue.shift();
    try {
      if (task.type === 'LOAD') await loadVoice(task);
      if (task.type === 'SYNTHESIZE') await synthesize(task);
    } catch (error) {
      console.error('TTS worker task error:', task?.type, error);
      post('ERROR', { requestId: task.requestId, voiceId: task.voiceId || activeVoiceId, message: error?.message || 'TTS worker lỗi.' });
    }
  }
  processing = false;
}

self.addEventListener('message', event => {
  const data = event.data || {};
  if (data.type === 'DISPOSE') {
    taskQueue = [];
    if (session) {
      try { session.release(); } catch (_) { /* best effort */ }
    }
    session = null;
    activeVoiceId = null;
    post('DISPOSED');
    return;
  }
  taskQueue.push(data);
  drain();
});
