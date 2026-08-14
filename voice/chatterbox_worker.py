#!/usr/bin/env python3
"""
Chatterbox TTS worker - runs inside the sidecar virtualenv.

Emma's main .venv runs Python 3.14, but chatterbox-tts is tested on 3.11
and pins torch builds for it, so the model lives in its own uv-managed
virtualenv (`.venv-chatterbox`). This script is spawned as a subprocess by
`voice/tts_chatterbox.py` and speaks a tiny JSON-lines protocol:

    {"cmd": "load", "variant": "turbo", "reference_wav": "/path/ref.wav"}
        -> {"ok": true, "sr": 24000, "variant": "turbo",
            "load_s": 45.2, "rtf": 1.6}
    {"id": 0, "text": "Hello there."}
        -> {"id": 0, "ok": true, "pcm_b64": "...", "audio_ms": 4200}
        -> {"id": 0, "ok": false, "error": "..."}
    {"cmd": "quit"}
        -> (process exits)

`rtf` is the synth wall-time divided by the audio duration from a
calibration sentence - >1.0 means slower than realtime, which the engine
uses to decide whether to fall back to the smaller Nano model on slow
CPUs. stdout carries ONLY protocol lines: the model's prints, warnings
and progress bars are redirected to stderr so the channel stays clean.
"""

import base64
import json
import os
import sys
import time
import traceback
from pathlib import Path

_proto = sys.stdout
sys.stdout = sys.stderr

# Model weights live inside the project, not in the HuggingFace hub cache:
# voice/models/chatterbox/<variant>/. First run downloads them there via
# snapshot_download(local_dir=...) - after that everything is passed to
# from_local() and no HuggingFace code or network is touched again.
MODELS_DIR = Path(__file__).resolve().parent / "models" / "chatterbox"


def _emit(obj):
    _proto.write(json.dumps(obj) + "\n")
    _proto.flush()


def _hf_token():
    token = os.environ.get("HF_TOKEN")
    if token:
        return token
    # Fallback: read the project .env directly so the worker works even
    # when the parent didn't export the variable.
    env_file = Path.cwd() / ".env"
    if env_file.is_file():
        for raw in env_file.read_text().splitlines():
            key, _, value = raw.partition("=")
            if key.strip() == "HF_TOKEN":
                return value.strip().strip('"').strip("'")
    return None


def _obtain(repo_id: str, local_dir: Path, checkpoint_name: str) -> Path:
    """
    Return a local directory with the model files in it.

    If the files are already there (first-run download did it), the local
    copy is used directly - no HuggingFace calls, no hub cache, fully
    offline. Otherwise the repo is downloaded once straight into the
    project-local directory.
    """
    required = local_dir / checkpoint_name
    if required.is_file() and (local_dir / "ve.safetensors").is_file():
        return local_dir

    os.environ.setdefault("HF_TOKEN", _hf_token() or "")
    local_dir.mkdir(parents=True, exist_ok=True)
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=repo_id,
        local_dir=str(local_dir),
        token=os.environ["HF_TOKEN"] or None,
        allow_patterns=["*.safetensors", "*.json", "*.txt", "*.pt", "*.model"],
    )
    return local_dir


def _load_turbo(reference_wav):
    from chatterbox.tts_turbo import ChatterboxTurboTTS

    ckpt_dir = _obtain("ResembleAI/chatterbox-turbo", MODELS_DIR / "turbo", "t3_turbo_v1.safetensors")
    model = ChatterboxTurboTTS.from_local(str(ckpt_dir), device="cpu")
    if reference_wav:
        model.prepare_conditionals(reference_wav)
    return model


_GPT2_SMALL_CONFIG = {
    "activation_function": "gelu_new",
    "architectures": ["GPT2LMHeadModel"],
    "attn_pdrop": 0.1,
    "bos_token_id": 50256,
    "embd_pdrop": 0.1,
    "eos_token_id": 50256,
    "initializer_range": 0.02,
    "layer_norm_epsilon": 1e-05,
    "model_type": "gpt2",
    "n_ctx": 8196,
    "n_embd": 768,
    "hidden_size": 768,
    "n_head": 12,
    "n_layer": 12,
    "n_positions": 8196,
    "n_special": 0,
    "predict_special_tokens": True,
    "resid_pdrop": 0.1,
    "summary_activation": None,
    "summary_first_dropout": 0.1,
    "summary_proj_to_labels": True,
    "summary_type": "cls_index",
    "summary_use_proj": True,
    "task_specific_params": {"text-generation": {"do_sample": True, "max_length": 50}},
    "vocab_size": 50276,
}


def _load_nano(reference_wav):
    """
    Loads Chatterbox-Nano (110M). The installed chatterbox-tts 0.1.7 does
    not expose nano and only knows the GPT2_medium backbone of Turbo;
    upstream's loader treats nano as Turbo with a smaller GPT2 backbone,
    its own checkpoint, and a public repo. This mirrors that exactly: the
    GPT2_small config is registered into the installed model config table
    (same shape as GPT2_medium, just 768/12/12 instead of 1024/16/24),
    then the shared code path loads the nano checkpoint.
    """
    import torch
    from safetensors.torch import load_file
    from transformers import AutoTokenizer

    from chatterbox.models.s3gen import S3Gen
    from chatterbox.models.t3 import T3, llama_configs
    from chatterbox.models.t3.modules.t3_config import T3Config
    from chatterbox.models.voice_encoder import VoiceEncoder
    from chatterbox.tts_turbo import Conditionals, ChatterboxTurboTTS

    llama_configs.LLAMA_CONFIGS["GPT2_small"] = _GPT2_SMALL_CONFIG

    ckpt_dir = _obtain("ResembleAI/chatterbox-nano", MODELS_DIR / "nano", "t3_nano_v1.safetensors")
    map_location = torch.device("cpu")

    ve = VoiceEncoder()
    ve.load_state_dict(load_file(ckpt_dir / "ve.safetensors"))
    ve.to("cpu").eval()

    hp = T3Config(text_tokens_dict_size=50276)
    hp.llama_config_name = "GPT2_small"
    hp.speech_tokens_dict_size = 6563
    hp.input_pos_emb = None
    hp.speech_cond_prompt_len = 375
    hp.use_perceiver_resampler = False
    hp.emotion_adv = False

    t3 = T3(hp)
    t3_state = load_file(ckpt_dir / "t3_nano_v1.safetensors")
    if "model" in t3_state.keys():
        t3_state = t3_state["model"][0]
    t3.load_state_dict(t3_state)
    del t3.tfmr.wte
    t3.to("cpu").eval()

    s3gen = S3Gen(meanflow=True)
    s3gen.load_state_dict(load_file(ckpt_dir / "s3gen_meanflow.safetensors"), strict=True)
    s3gen.to("cpu").eval()

    tokenizer = AutoTokenizer.from_pretrained(ckpt_dir)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    conds = None
    builtin_cond = ckpt_dir / "conds.pt"
    if builtin_cond.exists():
        conds = Conditionals.load(builtin_cond, map_location=map_location).to("cpu")

    model = ChatterboxTurboTTS(t3, s3gen, ve, tokenizer, "cpu", conds=conds)
    if reference_wav:
        model.prepare_conditionals(reference_wav)
    return model


def _synth(model, text):
    import torch

    wav = model.generate(text)
    audio_ms = int(wav.shape[1] / model.sr * 1000)
    pcm = (
        (wav[0] * 32767).clamp(-32768, 32767).round().to(torch.int16).numpy().tobytes()
    )
    return pcm, audio_ms


def _load(variant, reference_wav):
    if variant == "turbo":
        return _load_turbo(reference_wav)
    if variant == "nano":
        return _load_nano(reference_wav)
    raise ValueError(f"unknown variant {variant!r}")


def main():
    model = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            _emit({"ok": False, "error": "bad request"})
            continue

        cmd = req.get("cmd")
        if cmd == "quit":
            return

        if cmd == "load":
            try:
                variant = req.get("variant", "turbo")
                t0 = time.monotonic()
                model = _load(variant, req.get("reference_wav"))
                load_s = time.monotonic() - t0
                # Calibration sentence: measure realtime factor on real work.
                t1 = time.monotonic()
                _pcm, audio_ms = _synth(model, "This is a quick calibration of my speech.")
                rtf = (time.monotonic() - t1) / (audio_ms / 1000)
                _emit(
                    {
                        "ok": True,
                        "sr": model.sr,
                        "variant": variant,
                        "load_s": round(load_s, 1),
                        "rtf": round(rtf, 2),
                    }
                )
            except Exception:  # noqa: BLE001 - report cleanly over the protocol
                _emit({"ok": False, "error": traceback.format_exc()[-500:]})
            continue

        if model is None:
            _emit({"ok": False, "error": "model not loaded"})
            continue

        text = req.get("text")
        if not text:
            _emit({"id": req.get("id"), "ok": False, "error": "empty text"})
            continue
        try:
            pcm, audio_ms = _synth(model, text)
            _emit(
                {
                    "id": req.get("id"),
                    "ok": True,
                    "pcm_b64": base64.b64encode(pcm).decode(),
                    "audio_ms": audio_ms,
                }
            )
        except Exception:  # noqa: BLE001 - report cleanly over the protocol
            _emit({"id": req.get("id"), "ok": False, "error": traceback.format_exc()[-500:]})


if __name__ == "__main__":
    main()