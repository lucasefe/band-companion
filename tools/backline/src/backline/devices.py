from __future__ import annotations

from typing import Literal

DevicePref = Literal["auto", "mps", "cuda", "cpu"]


def resolve_device(pref: str = "auto") -> str:
    import torch

    if pref != "auto":
        return pref
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"
