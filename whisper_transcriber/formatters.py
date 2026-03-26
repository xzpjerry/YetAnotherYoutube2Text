import re
import unicodedata
from datetime import timedelta
from typing import Dict, List

__all__ = ["safe_slug", "format_srt_ts", "to_srt", "to_vtt"]


def safe_slug(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"[^\w\s\-().]", "_", value, flags=re.UNICODE)
    value = re.sub(r"\s+", "_", value)
    return value.strip("_") or "audio"


def format_srt_ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    td = timedelta(seconds=float(seconds))
    hours, remainder = divmod(td.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    hours += td.days * 24
    milliseconds = int(td.microseconds / 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def to_srt(segments: List[Dict]) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        start = format_srt_ts(seg.get("start", 0))
        end = format_srt_ts(seg.get("end", 0))
        text = seg.get("text", "").strip()
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


def to_vtt(segments: List[Dict]) -> str:
    out = ["WEBVTT\n"]
    for seg in segments:
        def vtt_ts(t):
            ts = format_srt_ts(t).replace(",", ".")
            return ts

        start = vtt_ts(seg.get("start", 0))
        end = vtt_ts(seg.get("end", 0))
        text = seg.get("text", "").strip()
        out.append(f"{start} --> {end}\n{text}\n")
    return "\n".join(out)
