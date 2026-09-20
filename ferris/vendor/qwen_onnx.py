# Adapted from ONNX Community's Qwen3-TTS inference.py (Apache-2.0).
# Source: https://huggingface.co/onnx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign
# Revision: f7d919ae545c1410acbc36d3c9d849c47f66ed76. See LICENSE-qwen.txt.
# Changes: only voice-design inference; local tokenizer; bounded CPU threads;
# omit unused encoders/talker; cancellation/deadline checks. No auto downloads.
import json
import sys
from pathlib import Path
import numpy as np
SR = 24000
DEC_FRAMES = 25
N_GROUPS = 16


def _apply_repetition_penalty(logits, prev_ids, penalty):
    if penalty == 1.0 or not prev_ids:
        return logits
    idx = np.array(sorted(set(int(i) for i in prev_ids)), dtype=np.int64)
    sc = logits[idx]
    logits[idx] = np.where(sc < 0, sc * penalty, sc / penalty)
    return logits

def _sample(logits, do_sample, top_k, top_p, temperature, rng):
    logits = logits.astype(np.float64)
    if not do_sample or temperature <= 0:
        return int(np.argmax(logits))
    logits = logits / max(temperature, 1e-6)
    if top_k and top_k > 0:
        k = min(top_k, logits.shape[-1])
        kth = np.partition(logits, -k)[-k]
        logits = np.where(logits < kth, -np.inf, logits)
    logits -= logits.max()
    probs = np.exp(logits)
    probs /= probs.sum()
    if top_p and top_p < 1.0:
        order = np.argsort(probs)[::-1]
        csum = np.cumsum(probs[order])
        cut = np.searchsorted(csum, top_p) + 1
        keep = order[:cut]
        mask = np.zeros_like(probs)
        mask[keep] = probs[keep]
        probs = mask / mask.sum()
    return int(rng.choice(len(probs), p=probs))

class Pipeline:

    def __init__(self, model_path: str, tts_dir: str=None):
        import onnxruntime as ort
        self.root = Path(model_path)
        self.manifest = json.loads((self.root / 'manifest.json').read_text())
        sm = self.manifest['sub_models']
        prov = self.manifest.get('execution_provider', 'CPUExecutionProvider')
        avail = ort.get_available_providers()
        if prov not in avail:
            print(f'  [warn] manifest EP {prov} unavailable; falling back to CPU', file=sys.stderr)
            prov = 'CPUExecutionProvider'
        self.provider = prov
        so = ort.SessionOptions()
        so.log_severity_level = 3
        so.intra_op_num_threads = 4
        so.inter_op_num_threads = 1

        def sess(name):
            if name not in sm:
                return None
            return ort.InferenceSession(str(self.root / sm[name]['filename']), so, providers=[prov])
        self.text_embed = sess('text_embed')
        self.codec_embed = sess('codec_embed')
        self.talker = None
        self.code_predictor = sess('code_predictor')
        self.residual_embed = sess('residual_embed')
        self.tok_encoder = None
        self.tok_decoder = sess('tok_decoder')
        self.speaker_encoder = None
        self.talker_cache = sess('talker_cache')
        if self.talker_cache is not None:
            self._past_names = [i.name for i in self.talker_cache.get_inputs()][3:]
        self.tts_dir = tts_dir
        self._cfg = None
        self._tok = None
        if tts_dir is not None:
            self._cfg = json.loads((Path(tts_dir) / 'config.json').read_text())

    def embed_text(self, text_ids):
        return self.text_embed.run(None, {'text_ids': np.asarray(text_ids, np.int64)})[0]

    def embed_codec(self, codec_ids):
        return self.codec_embed.run(None, {'codec_ids': np.asarray(codec_ids, np.int64)})[0]

    def talker_cache_step(self, inputs_embeds, position_ids, attention_mask, past):
        """KV-cache talker: → (logits[B,cur,V], hidden[B,cur,2048], present[list of 56]).
        `past`/`present` are ordered lists of the flattened K/V tensors (layer0_k, layer0_v,
        layer1_k, …). Empty past = prefill; len-1 cur = decode."""
        feed = {'inputs_embeds': inputs_embeds.astype(np.float32), 'position_ids': np.asarray(position_ids, np.int64), 'attention_mask': np.asarray(attention_mask, np.int64)}
        for name, t in zip(self._past_names, past):
            feed[name] = t.astype(np.float32)
        out = self.talker_cache.run(None, feed)
        return (out[0], out[1], list(out[2:]))

    def predict_residual(self, talker_hidden, codec_ids):
        return self.code_predictor.run(None, {'talker_hidden': talker_hidden.astype(np.float32), 'codec_ids': np.asarray(codec_ids, np.int64)})[0]

    def step_embed(self, codec_ids):
        return self.residual_embed.run(None, {'codec_ids': np.asarray(codec_ids, np.int64)})[0]

    def decode(self, codes):
        return self.tok_decoder.run(None, {'audio_codes': np.asarray(codes, np.int64)})[0]

    def decode_chunked(self, codes):
        """Decode arbitrary-length codes through the fixed-25-frame decoder by
        tiling each 25-frame chunk; tail is padded by repetition then trimmed."""
        F = codes.shape[1]
        outs = []
        for s in range(0, F, DEC_FRAMES):
            chunk = codes[:, s:s + DEC_FRAMES]
            if chunk.shape[1] < DEC_FRAMES:
                idx = np.arange(DEC_FRAMES) % chunk.shape[1]
                chunk = chunk[:, idx]
                wav = self.decode(chunk)
                keep = int(round(wav.shape[-1] * (F - s) / DEC_FRAMES))
                outs.append(wav[..., :keep])
                break
            outs.append(self.decode(chunk))
        return np.concatenate(outs, axis=-1)

    @property
    def cfg(self):
        if self._cfg is None:
            raise RuntimeError('Pass --tts-dir (the HF model dir) for generation: config token ids + tokenizer live there.')
        return self._cfg

    @property
    def model_type(self):
        """tts_model_type from config: 'voice_design' | 'custom_voice' | 'base'.
        Each model exposes different features (see _check_features)."""
        c = self.cfg
        return c.get('tts_model_type') or c.get('talker_config', {}).get('tts_model_type') or 'unknown'

    def _check_features(self, instruct=None, speaker=None, ref_audio=None):
        """Gate features by model type so a flag that the loaded model can't honor
        fails loudly instead of silently doing nothing:
          voice_design → instruct (natural-language style); no speaker/ref
          custom_voice → speaker (built-in voices) + optional instruct; no ref
          base         → voice cloning (ref_audio/ref_text); no speaker/instruct
        """
        mt = self.model_type
        if speaker and mt != 'custom_voice':
            raise ValueError(f"--speaker is a CustomVoice feature, but this model is '{mt}'. Use a customvoice checkpoint, or drop --speaker.")
        if instruct and mt not in ('voice_design', 'custom_voice'):
            raise ValueError(f"--instruct is a VoiceDesign/CustomVoice feature, but this model is '{mt}'. Drop --instruct (Base clones from --ref-audio instead).")
        if ref_audio and mt != 'base':
            raise ValueError(f"voice cloning (--ref-audio) is a Base-model feature, but this model is '{mt}'. Use a base checkpoint.")
        if mt == 'custom_voice' and (not speaker):
            print("  [note] CustomVoice with no --speaker → model's default voice.", file=sys.stderr)

    def _ids(self, text):
        from transformers import AutoTokenizer
        if self._tok is None:
            self._tok = AutoTokenizer.from_pretrained(self.tts_dir, trust_remote_code=False, local_files_only=True)
        return np.asarray([self._tok.encode(text, add_special_tokens=False)], dtype=np.int64)

    def generate(self, text, language='Auto', instruct=None, speaker=None, ref_audio=None, ref_text=None, max_new_tokens=2048, do_sample=True, top_k=50, top_p=1.0, temperature=0.9, repetition_penalty=1.05, sub_do_sample=True, sub_top_k=50, sub_top_p=1.0, sub_temperature=0.9, seed=0, verbose=True):
        """Mirror Qwen3TTSForConditionalGeneration.generate (non_streaming_mode=True).
        Features are gated by model type (voice_design=instruct, custom_voice=speaker,
        base=clone). Returns codes [T,16] (int64). Decode with `decode_chunked(codes[None])`.
        """
        cfg = self.cfg
        self._check_features(instruct=instruct, speaker=speaker, ref_audio=ref_audio)
        if ref_audio:
            raise ValueError("Voice cloning is not enabled")
        tc = cfg['talker_config']
        H = tc['hidden_size']
        rng = np.random.default_rng(seed)
        tts_bos, tts_eos, tts_pad = (cfg['tts_bos_token_id'], cfg['tts_eos_token_id'], cfg['tts_pad_token_id'])
        codec_eos = tc['codec_eos_token_id']
        codec_pad, codec_bos = (tc['codec_pad_id'], tc['codec_bos_id'])
        vocab = tc['vocab_size']
        assistant = f'<|im_start|>assistant\n{text}<|im_end|>\n<|im_start|>assistant\n'
        input_id = self._ids(assistant)
        if input_id.shape[1] < 9:
            raise ValueError('text tokenized too short for the assistant template')
        spec = self.embed_text([[tts_bos, tts_eos, tts_pad]])
        bos_e, eos_e, pad_e = (spec[:, 0:1], spec[:, 1:2], spec[:, 2:3])
        lang = (language or 'auto').lower()
        if lang == 'auto' or lang not in tc.get('codec_language_id', {}):
            language_id = None
        else:
            language_id = tc['codec_language_id'][lang]
        if language_id is None:
            codec_prefill = [[tc['codec_nothink_id'], tc['codec_think_bos_id'], tc['codec_think_eos_id']]]
        else:
            codec_prefill = [[tc['codec_think_id'], tc['codec_think_bos_id'], language_id, tc['codec_think_eos_id']]]
        codec0 = self.embed_codec(codec_prefill)
        codec1 = self.embed_codec([[codec_pad, codec_bos]])
        speaker_embed = None
        if speaker:
            spk_map = tc.get('spk_id', {})
            if speaker.lower() not in spk_map:
                raise ValueError(f"Speaker '{speaker}' not in spk_id {list(spk_map)[:8]}…")
            speaker_embed = self.embed_codec([[spk_map[speaker.lower()]]])
        if speaker_embed is None:
            codec_input = np.concatenate([codec0, codec1], axis=1)
        else:
            codec_input = np.concatenate([codec0, speaker_embed, codec1], axis=1)
        prefix = []
        if instruct:
            instruct_text = f'<|im_start|>user\n{instruct}<|im_end|>\n'
            prefix.append(self.embed_text(self._ids(instruct_text)))
        role = self.embed_text(input_id[:, :3])
        pad_block = np.concatenate([np.repeat(pad_e, codec_input.shape[1] - 2, axis=1), bos_e], axis=1)
        talker_in = np.concatenate([role, pad_block + codec_input[:, :-1]], axis=1)
        body_ids = input_id[:, 3:-5]
        Ltext = body_ids.shape[1]
        text_body = self.embed_text(body_ids)
        block1 = np.concatenate([text_body, eos_e], axis=1) + self.embed_codec([[codec_pad] * (Ltext + 1)])
        block2 = pad_e + self.embed_codec([[codec_bos]])
        talker_in = np.concatenate([talker_in, block1, block2], axis=1)
        if prefix:
            talker_in = np.concatenate(prefix + [talker_in], axis=1)
        trailing = pad_e[:, 0]
        return self._ar_loop(talker_in, trailing, vocab, codec_eos, max_new_tokens, do_sample, top_k, top_p, temperature, repetition_penalty, sub_do_sample, sub_top_k, sub_top_p, sub_temperature, seed, verbose)

    def _ar_loop(self, talker_in, trailing, vocab, codec_eos, max_new_tokens, do_sample, top_k, top_p, temperature, repetition_penalty, sub_do_sample, sub_top_k, sub_top_p, sub_temperature, seed, verbose):
        """AR talker loop (MROPE→arange). Uses the O(n) KV-cache talker if exported,
        else the no-cache O(n²) talker. Shared by all generation paths. Returns codes [T,16]."""
        if self.talker_cache is not None:
            return self._ar_loop_cached(talker_in, trailing, vocab, codec_eos, max_new_tokens, do_sample, top_k, top_p, temperature, repetition_penalty, sub_do_sample, sub_top_k, sub_top_p, sub_temperature, seed, verbose)
        raise RuntimeError('The cached talker model is required.')

    def _ar_loop_cached(self, talker_in, trailing, vocab, codec_eos, max_new_tokens, do_sample, top_k, top_p, temperature, repetition_penalty, sub_do_sample, sub_top_k, sub_top_p, sub_temperature, seed, verbose):
        """O(n) KV-cache AR loop: prefill once, then decode one token/step feeding the cache.
        Numerically identical to the no-cache loop (same positions, full causal attention)."""
        rng = np.random.default_rng(seed)
        suppress = np.array([i for i in range(vocab - 1024, vocab) if i != codec_eos], dtype=np.int64)
        past = [np.zeros((1, 8, 0, 128), np.float32) for _ in self._past_names]
        T0 = talker_in.shape[1]
        pos = np.broadcast_to(np.arange(T0), (3, 1, T0)).copy()
        logits, hidden, past = self.talker_cache_step(talker_in, pos, np.ones((1, T0), np.int64), past)
        total = T0
        all_codes, prev_first = ([], [])
        for step in range(max_new_tokens):
            self.check_cancelled()
            first = logits[0, -1].astype(np.float64).copy()
            first[suppress] = -np.inf
            first = _apply_repetition_penalty(first, prev_first, repetition_penalty)
            code0 = _sample(first, do_sample, top_k, top_p, temperature, rng)
            if code0 == codec_eos:
                break
            prev_first.append(code0)
            th = hidden[0, -1][None].astype(np.float32)
            codes16 = np.zeros((1, N_GROUPS), dtype=np.int64)
            codes16[0, 0] = code0
            for j in range(1, N_GROUPS):
                gl = self.predict_residual(th, codes16)
                codes16[0, j] = _sample(gl[0, j - 1], sub_do_sample, sub_top_k, sub_top_p, sub_temperature, rng)
            all_codes.append(codes16[0].copy())
            nxt = self.step_embed(codes16)[:, None] + trailing[:, None]
            pos = np.broadcast_to(np.array([total]), (3, 1, 1)).copy()
            logits, hidden, past = self.talker_cache_step(nxt, pos, np.ones((1, total + 1), np.int64), past)
            total += 1
            if verbose and (step + 1) % 25 == 0:
                print(f'    …{step + 1} frames (cached)', file=sys.stderr)
        codes = np.stack(all_codes, axis=0).astype(np.int64) if all_codes else np.zeros((0, N_GROUPS), np.int64)
        if verbose:
            print(f'  generated {codes.shape[0]} frames (KV-cache)')
        return codes
