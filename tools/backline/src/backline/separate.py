from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from backline.audio import read_wav, write_wav
from backline.devices import resolve_device

MODELS = {"fast": "htdemucs", "best": "htdemucs_ft"}
VOCALS_SR = 16000  # the vocal stem is only used for analysis (lyrics timing)

ProgressFn = Callable[[float], None]


def separate(
    original_wav: Path,
    drums_wav: Path,
    *,
    quality: str = "fast",
    device: str = "auto",
    vocals_wav: Path | None = None,
    no_drums_wav: Path | None = None,
    progress: ProgressFn | None = None,
) -> str:
    """Run Demucs and write the drum stem (plus optional vocals / no-drums stems).

    Returns the device actually used.
    """
    import torch

    model_name = MODELS[quality]
    data, sr = read_wav(original_wav)
    mix = torch.from_numpy(data.T.copy())  # [channels, frames]

    def callback(d: dict) -> None:
        if progress is None or d.get("state") != "end":
            return
        models = max(d.get("models", 1), 1)
        length = max(d.get("audio_length", 1), 1)
        done = d.get("model_idx_in_bag", 0) * length + d.get("segment_offset", 0)
        progress(min(done / (models * length), 1.0))

    dev = resolve_device(device)
    try:
        stems = _run(model_name, dev, mix, sr, callback)
    except Exception as e:  # noqa: BLE001
        if dev == "cpu":
            raise
        # MPS/CUDA occasionally fail on specific ops; CPU is slow but always works.
        print(f"demucs on {dev} failed ({type(e).__name__}: {e}); retrying on cpu", flush=True)
        dev = "cpu"
        stems = _run(model_name, dev, mix, sr, callback)

    write_wav(drums_wav, stems["drums"].numpy().T, sr)
    if vocals_wav is not None:
        import librosa
        import soundfile as sf

        mono = stems["vocals"].numpy().mean(axis=0)
        mono = librosa.resample(mono, orig_sr=sr, target_sr=VOCALS_SR)
        sf.write(vocals_wav, mono, VOCALS_SR, subtype="PCM_16")
    if no_drums_wav is not None:
        rest = sum(v for k, v in stems.items() if k != "drums")
        write_wav(no_drums_wav, rest.numpy().T, sr)
    if progress:
        progress(1.0)
    return dev


def _run(model_name: str, device: str, mix, sr: int, callback) -> dict:
    from demucs.api import Separator

    separator = Separator(model=model_name, device=device, progress=False, callback=callback)
    _, stems = separator.separate_tensor(mix, sr)
    return {k: v.detach().cpu() for k, v in stems.items()}
