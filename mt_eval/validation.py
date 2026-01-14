from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
import json

from .config import RunConfig


@dataclass
class RowStatus:
    item_id: int
    incomplete: bool
    invalid: bool
    reasons: List[str]


def parse_display_map(display_map_json: str, n: int) -> Dict[int, str]:
    m = json.loads(display_map_json)
    out: Dict[int, str] = {}
    for i in range(1, n + 1):
        k = str(i)
        if k not in m:
            raise ValueError("display_map_json missing key " + k)
        out[i] = str(m[k])
    return out


def is_row_incomplete(*, bucket_by_t: Dict[str, str | None], da_by_t: Dict[str, int | None], committed_at: str | None) -> bool:
    if not committed_at:
        return True
    for v in bucket_by_t.values():
        if v is None or str(v).strip() == "":
            return True
    for v in da_by_t.values():
        if v is None:
            return True
    return False


def validate_row(
    *,
    cfg: RunConfig,
    bucket_by_t: Dict[str, str | None],
    da_by_t: Dict[str, int | None],
    committed_at: str | None,
    require_complete_for_next: bool = True,
) -> Tuple[bool, List[str]]:
    """
    Returns (is_valid, reasons).
    """
    reasons: List[str] = []

    # Completeness gate (for Next)
    if require_complete_for_next and is_row_incomplete(bucket_by_t=bucket_by_t, da_by_t=da_by_t, committed_at=committed_at):
        reasons.append("Row is incomplete (missing bucket/DA and/or committed_at).")
        return False, reasons

    # Bucket key validation
    allowed = set(cfg.bucket_keys)
    for t, b in bucket_by_t.items():
        if b is None:
            continue
        if b not in allowed:
            reasons.append(f"Unknown bucket '{b}' for {t}.")

    # DA validation
    for t, d in da_by_t.items():
        if d is None:
            continue
        if cfg.da_integer_only and not isinstance(d, int):
            reasons.append(f"DA for {t} must be an integer.")
        if int(d) < cfg.da_min or int(d) > cfg.da_max:
            reasons.append(f"DA for {t} out of range [{cfg.da_min}, {cfg.da_max}].")

    # Strict bucket ordering (only if asked)
    if cfg.enforce_bucket_ordering:
        # Bucket order is cfg.buckets in best -> ... -> poor.
        # Requirement: max(lower) < min(higher) across adjacent non-empty buckets.
        # So for order [best, good, ok, poor], check:
        # max(good) < min(best), max(ok) < min(good), max(poor) < min(ok)
        keys = cfg.bucket_keys
        scores_by_bucket: Dict[str, List[int]] = {k: [] for k in keys}
        for t, b in bucket_by_t.items():
            d = da_by_t.get(t)
            if b is None or d is None:
                continue
            scores_by_bucket[b].append(int(d))

        def _nonempty(k: str) -> bool:
            return len(scores_by_bucket[k]) > 0

        # adjacent comparisons: (higher, lower)
        for higher, lower in zip(keys[:-1], keys[1:]):
            if not _nonempty(higher) or not _nonempty(lower):
                # empty bucket allowed -> skip
                continue
            if max(scores_by_bucket[lower]) >= min(scores_by_bucket[higher]):
                reasons.append(
                    f"Bucket ordering violated: max({lower}) must be < min({higher})."
                )

    return (len(reasons) == 0), reasons
