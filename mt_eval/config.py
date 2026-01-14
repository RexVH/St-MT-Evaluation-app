from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Tuple
import yaml


@dataclass(frozen=True)
class Bucket:
    key: str
    label: str


@dataclass(frozen=True)
class RunConfig:
    num_translations: int
    da_min: int
    da_max: int
    da_integer_only: bool
    buckets: List[Bucket]
    enforce_bucket_ordering: bool
    allow_empty_buckets: bool
    ui_show_back: bool
    ui_show_jump_to: bool
    ui_show_completion_summary: bool

    @property
    def bucket_keys(self) -> List[str]:
        return [b.key for b in self.buckets]

    @property
    def bucket_labels_by_key(self) -> Dict[str, str]:
        return {b.key: b.label for b in self.buckets}


def load_config(path: str | Path = "config.yaml") -> RunConfig:
    p = Path(path)
    if not p.exists():
        # Safe defaults (v1)
        data = {
            "run": {"num_translations": 12},
            "da": {"min": 0, "max": 100, "integer_only": True},
            "buckets": [
                {"key": "best", "label": "Best"},
                {"key": "good", "label": "Good"},
                {"key": "ok", "label": "OK"},
                {"key": "poor", "label": "Poor"},
            ],
            "validation": {"enforce_bucket_ordering": True, "allow_empty_buckets": True},
            "ui": {"show_back_button": True, "show_jump_to": True, "show_completion_summary": True},
        }
    else:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    def _req(obj: Dict[str, Any], key: str, ctx: str) -> Any:
        if key not in obj:
            raise ValueError(f"Missing config key: {ctx}.{key}")
        return obj[key]

    run = _req(data, "run", "root")
    da = _req(data, "da", "root")
    buckets_raw = _req(data, "buckets", "root")
    val = _req(data, "validation", "root")
    ui = _req(data, "ui", "root")

    buckets: List[Bucket] = []
    for b in buckets_raw:
        if "key" not in b or "label" not in b:
            raise ValueError("Each bucket must have {key, label}")
        buckets.append(Bucket(key=str(b["key"]), label=str(b["label"])))

    cfg = RunConfig(
        num_translations=int(_req(run, "num_translations", "run")),
        da_min=int(_req(da, "min", "da")),
        da_max=int(_req(da, "max", "da")),
        da_integer_only=bool(_req(da, "integer_only", "da")),
        buckets=buckets,
        enforce_bucket_ordering=bool(_req(val, "enforce_bucket_ordering", "validation")),
        allow_empty_buckets=bool(_req(val, "allow_empty_buckets", "validation")),
        ui_show_back=bool(_req(ui, "show_back_button", "ui")),
        ui_show_jump_to=bool(_req(ui, "show_jump_to", "ui")),
        ui_show_completion_summary=bool(_req(ui, "show_completion_summary", "ui")),
    )

    # Basic invariants
    if cfg.num_translations <= 0:
        raise ValueError("run.num_translations must be > 0")
    if cfg.da_min >= cfg.da_max:
        raise ValueError("da.min must be < da.max")
    keys = cfg.bucket_keys
    if len(keys) != len(set(keys)):
        raise ValueError("Bucket keys must be unique")

    return cfg
