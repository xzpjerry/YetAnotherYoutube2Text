from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


Environment = Mapping[str, str]


def get_path(name: str, default: str, environ: Environment | None = None) -> Path:
    source = environ or os.environ
    return Path(source.get(name, default))


def get_int(name: str, default: int, environ: Environment | None = None) -> int:
    source = environ or os.environ
    return int(source.get(name, default))


def get_float(name: str, default: float, environ: Environment | None = None) -> float:
    source = environ or os.environ
    return float(source.get(name, default))
