from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import math


def _try_parse_dt(s: str | None) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def aggregate_da_stats(all_scores: List[int]) -> Dict[str, float]:
    if not all_scores:
        return {"count": 0, "mean": math.nan, "median": math.nan, "std": math.nan, "min": math.nan, "max": math.nan}
    xs = sorted(all_scores)
    n = len(xs)
    mean = sum(xs) / n
    median = xs[n // 2] if n % 2 == 1 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0
    var = sum((x - mean) ** 2 for x in xs) / n
    std = math.sqrt(var)
    return {"count": n, "mean": mean, "median": median, "std": std, "min": min(xs), "max": max(xs)}


def time_per_sentence_seconds(started_at: str | None, committed_at: str | None) -> Optional[float]:
    s = _try_parse_dt(started_at)
    c = _try_parse_dt(committed_at)
    if not s or not c:
        return None
    delta = c - s
    return max(0.0, delta.total_seconds())


def summarize_times(times: List[float]) -> Dict[str, float]:
    if not times:
        return {"count": 0, "mean": math.nan, "median": math.nan, "min": math.nan, "max": math.nan}
    xs = sorted(times)
    n = len(xs)
    mean = sum(xs) / n
    median = xs[n // 2] if n % 2 == 1 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0
    return {"count": n, "mean": mean, "median": median, "min": xs[0], "max": xs[-1]}
