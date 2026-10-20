from __future__ import annotations

import contextlib
import io
from pathlib import Path

import numpy as np

FPS = 100

# ADTOF LABELS_5 order: [35 kick, 38 snare, 47 toms, 42 hi-hat, 49 cymbals]
CLASSES = ("kick", "snare", "tom", "hihat", "cymbal")
DEFAULT_THRESHOLDS = (0.22, 0.24, 0.32, 0.22, 0.30)


def transcribe(drums_wav: Path, activations_npy: Path) -> np.ndarray:
    """Run ADTOF on the drum stem and save frame activations shaped [frames, 5] at 100 fps."""
    import torch
    from adtof_pytorch import (
        calculate_n_bins,
        create_frame_rnn_model,
        get_default_weights_path,
        load_audio_for_model,
        load_pytorch_weights,
    )

    model = create_frame_rnn_model(calculate_n_bins())
    with contextlib.redirect_stdout(io.StringIO()):  # the loader prints the weights path
        model = load_pytorch_weights(model, get_default_weights_path(), strict=False)
    model.eval()
    # The CRNN is small; ADTOF only supports cuda/cpu and cpu is a few seconds per song.
    x = load_audio_for_model(str(drums_wav))
    with torch.no_grad():
        pred = model(x).cpu().numpy()
    activations = pred[0].astype(np.float32)  # [frames, 5]
    np.save(activations_npy, activations)
    return activations


def load_activations(activations_npy: Path) -> np.ndarray:
    return np.load(activations_npy)
