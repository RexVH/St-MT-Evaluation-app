from __future__ import annotations

import hashlib
import json
from typing import List, Dict, Any


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def stable_seed_int(run_id: str, item_id: int) -> int:
    """
    Deterministic seed derived from SHA256(run_id + ':' + item_id).
    """
    h = hashlib.sha256(f"{run_id}:{item_id}".encode("utf-8")).digest()
    return int.from_bytes(h[:8], byteorder="big", signed=False)


def compute_row_input_hash(source: str, translations_in_t_order: List[str]) -> str:
    """
    Canonical hash of the input content.
    Uses JSON to avoid ambiguity with separators.
    """
    payload = {"source": source, "translations": translations_in_t_order}
    s = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return sha256_hex(s)


def compute_row_eval_hash(
    *,
    bucket_by_t: Dict[str, str],
    da_by_t: Dict[str, int],
    comment: str | None,
    display_map_json: str,
    row_input_hash: str,
    run_id: str,
) -> str:
    """
    Canonical hash of evaluation fields only (not timestamps).
    """
    payload: Dict[str, Any] = {
        "bucket_by_t": {k: bucket_by_t[k] for k in sorted(bucket_by_t.keys())},
        "da_by_t": {k: int(da_by_t[k]) for k in sorted(da_by_t.keys())},
        "comment": comment or "",
        "display_map_json": display_map_json,
        "row_input_hash": row_input_hash,
        "run_id": run_id,
    }
    s = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return sha256_hex(s)
