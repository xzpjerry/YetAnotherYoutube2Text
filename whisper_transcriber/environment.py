from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


Environment = Mapping[str, str]


def get_path(name: str, default: str, environ: Environment | None = None) -> Path:
    source = os.environ if environ is None else environ
    return Path(source.get(name, default))


def get_int(name: str, default: int, environ: Environment | None = None) -> int:
    source = os.environ if environ is None else environ
    return int(source.get(name, default))


def get_float(name: str, default: float, environ: Environment | None = None) -> float:
    source = os.environ if environ is None else environ
    return float(source.get(name, default))
