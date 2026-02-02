from __future__ import annotations

import json, datetime as dt
import hashlib
from io import BytesIO
from typing import Dict, List, Optional, Tuple

import streamlit as st

from mt_eval.config import load_config, RunConfig
from mt_eval.xlsx_io import load_workbook_from_upload, save_workbook_to_bytes, now_iso_utc
from mt_eval.validation import parse_display_map, validate_row, is_row_incomplete
from mt_eval.hashing import compute_row_eval_hash
from mt_eval.stats import aggregate_da_stats, time_per_sentence_seconds, summarize_times
from mt_eval.instructions import instructions_md

st.set_page_config(page_title="MT Human Evaluation", layout="wide")
st.title("Machine Translation Human Evaluation")
st.sidebar.page_link("app.py", label="Evaluation System", icon="📝")
if st.query_params.get("type") == "admin":
    st.sidebar.page_link(
        "pages/01_Generate_Template.py",
        label="Generate Template (admin)",
        icon="🧪",
    )


# ---------------- Session State ----------------
def ss_init():
    st.session_state.setdefault("cfg", None)
    st.session_state.setdefault("wb_state", None)  # WorkbookState
    st.session_state.setdefault("current_item_idx", 0)
    st.session_state.setdefault("row_reasons_cache", {})  # item_id -> reasons list (invalid)
    st.session_state.setdefault("invalid_item_ids", [])
    st.session_state.setdefault("incomplete_item_ids", [])
    st.session_state.setdefault("upload_name", None)
    st.session_state.setdefault("upload_digest", None)
    # Jump-to sentence number widget state
    st.session_state.setdefault("jump_to_sentence", 1)

ss_init()


def _ss_key(*parts: object) -> str:
    return "__".join(str(p) for p in parts)


def _get_order_key(item_id: int) -> str:
    return _ss_key("display_order", item_id)


def _get_order_grouped_key(item_id: int) -> str:
    # Tracks whether the current display order is known to be bucket-grouped.
    return _ss_key("display_order_is_grouped", item_id)


# ---------------- Helpers ----------------
def gts():
    return dt.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")


def _write_da_from_widget(widget_key: str, model_key: str):
    st.session_state[model_key] = int(st.session_state[widget_key])


def _bucket_range_for_key(*, bucket_key: str, intra: int, bucket_order_best_first: List[str]) -> Tuple[int, int]:
    """Return allowed DA (min,max) for a bucket.

    bucket_order_best_first: e.g. ["best","good","ok","poor"].
    DA ranges increase from poor->best.
    Example (intra=3):
      poor: 1-3, ok: 4-6, good: 7-9, best: 10-12
    """
    if intra <= 0:
        intra = 1

    if bucket_key not in bucket_order_best_first:
        # Unknown bucket -> no restriction other than global range
        return (1, max(1, 4 * intra))

    best_first_rank = bucket_order_best_first.index(bucket_key)  # best=0..poor=3
    low_to_high_rank = (len(bucket_order_best_first) - 1) - best_first_rank  # poor=0..best=3
    mn = 1 + (low_to_high_rank * intra)
    mx = mn + intra - 1
    return (int(mn), int(mx))


def _remap_da_between_buckets(*, old_da: int, old_range: Tuple[int, int], new_range: Tuple[int, int]) -> int:
    """Map a DA value from one bucket range to another preserving relative position.

    Because all buckets share the same intra-bucket size, we preserve the offset from
    the old min (clamped) and apply it to the new min.
    """
    o_min, o_max = old_range
    n_min, n_max = new_range

    if o_max < o_min:
        o_min, o_max = o_max, o_min
    if n_max < n_min:
        n_min, n_max = n_max, n_min

    try:
        v = int(old_da)
    except Exception:
        v = int(o_min)

    v = max(int(o_min), min(int(o_max), v))
    offset = v - int(o_min)
    mapped = int(n_min) + int(offset)
    return max(int(n_min), min(int(n_max), int(mapped)))


def _soft_validate_live(cfg, bucket_by_t, da_by_t):
    """
    Live checks: bucket selected + DA in range.
    Do NOT enforce bucket ordering during live editing.
    """
    reasons = []
    # Bucket completeness
    for t, b in bucket_by_t.items():
        if b is None:
            reasons.append(f"Missing bucket for {t}.")
    # DA presence + range
    for t, d in da_by_t.items():
        if d is None:
            reasons.append(f"Missing DA for {t}.")
            continue
        if int(d) < cfg.da_min or int(d) > cfg.da_max:
            reasons.append(f"DA for {t} out of range [{cfg.da_min}, {cfg.da_max}].")
    return (len(reasons) == 0), reasons


def _get_row_index(item_idx: int) -> int:
    # openpyxl row index: header row is 1, first data row is 2
    return 2 + item_idx


def _set_jump_to_sentence(n: int) -> None:
    """
    Defer updating the jump-to widget value until the *next* rerun.
    This avoids StreamlitAPIException from mutating a widget-bound key
    after the widget is instantiated.
    """
    st.session_state["jump_to_sentence_pending"] = int(n)

def _recompute_global_status():
    state = st.session_state.wb_state
    cfg: RunConfig = st.session_state.cfg
    wb = state.wb
    inputs_ws = wb["inputs"]
    eval_ws = wb["eval"]
    ic = state.inputs_col
    ec = state.eval_col

    invalid: List[int] = []
    incomplete: List[int] = []
    reasons_cache: Dict[int, List[str]] = {}

    for idx, item_id in enumerate(state.item_ids):
        r = _get_row_index(idx)

        bucket_by_t = {
            f"t{i}": (eval_ws.cell(row=r, column=ec[f"bucket_t{i}"]).value or None)
            for i in range(1, cfg.num_translations + 1)
        }
        da_by_t = {
            f"t{i}": (eval_ws.cell(row=r, column=ec[f"da_t{i}"]).value)
            for i in range(1, cfg.num_translations + 1)
        }
        # normalize ints if present
        for k, v in list(da_by_t.items()):
            if v is None:
                continue
            try:
                da_by_t[k] = int(v)
            except Exception:
                # leave as-is; validator will complain
                pass

        committed_at = eval_ws.cell(row=r, column=ec["committed_at"]).value

        if is_row_incomplete(bucket_by_t=bucket_by_t, da_by_t=da_by_t, committed_at=committed_at):
            incomplete.append(int(item_id))

        ok, reasons = validate_row(
            cfg=cfg,
            bucket_by_t=bucket_by_t,
            da_by_t=da_by_t,
            committed_at=committed_at,
            require_complete_for_next=False,  # global validity: allow incomplete, but still track invalid reasons if complete-but-wrong
        )

        # Treat complete-but-invalid as invalid. Incomplete rows aren’t “invalid” until filled.
        if committed_at and not ok:
            invalid.append(int(item_id))
            reasons_cache[int(item_id)] = reasons

    st.session_state.invalid_item_ids = invalid
    st.session_state.incomplete_item_ids = incomplete
    st.session_state.row_reasons_cache = reasons_cache


def _jump_to_item_id(target_item_id: int):
    state = st.session_state.wb_state
    if target_item_id not in state.item_ids:
        return
    new_idx = state.item_ids.index(target_item_id)
    st.session_state.current_item_idx = new_idx
    _set_jump_to_sentence(int(new_idx + 1))


def _jump_first_incomplete():
    inc = st.session_state.incomplete_item_ids
    if inc:
        _jump_to_item_id(int(inc[0]))


def _jump_next_invalid():
    inv = st.session_state.invalid_item_ids
    if not inv:
        return
    state = st.session_state.wb_state
    cur_item_id = state.item_ids[st.session_state.current_item_idx]
    after = [x for x in inv if x > cur_item_id]
    _jump_to_item_id(int(after[0] if after else inv[0]))


def _render_banner():
    inv = st.session_state.invalid_item_ids
    inc = st.session_state.incomplete_item_ids
    if inv:
        st.error(f"⚠️ {len(inv)} sentence(s) have invalid committed evaluations. Finish is blocked.", icon="⚠️")
        cols = st.columns([1, 1, 6])
        with cols[0]:
            if st.button("Next invalid", use_container_width=True):
                _jump_next_invalid()
                st.rerun()
        with cols[1]:
            if st.button("First incomplete", use_container_width=True):
                _jump_first_incomplete()
                st.rerun()
    elif inc:
        st.info(f"📝 {len(inc)} sentence(s) incomplete. Finish is blocked until all are committed.", icon="📝")
        if st.button("Jump to first incomplete"):
            _jump_first_incomplete()
            st.rerun()
    else:
        st.success("✅ All sentences complete and valid. You can Finish and download the final XLSX.", icon="✅")


def _download_button(label: str):
    state = st.session_state.wb_state
    data = save_workbook_to_bytes(state)
    fname = st.session_state.upload_name or "mt_eval_checkpoint.xlsx"
    st.download_button(
        label=label,
        data=data,
        file_name=fname.replace(".xlsx", "") + f"_checkpoint__{gts()}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )


# def _auto_order_da_by_bucket(
#     *,
#     cfg,
#     cur_item_id: int,
#     label_to_key: dict,
#     gap: int = 1,
# ):
#     """
#     Rewrites DA values in session_state so that:
#       Poor < OK < Good < Best with strict separation (by >= gap)

#     Only uses the bucket assignments already selected in the UI.
#     Writes ONLY to the model keys: da_val_{cur_item_id}_{pos}
#     """
#     high_to_low = [b.key for b in cfg.buckets]   # e.g. ["best","good","ok","poor"]
#     low_to_high = list(reversed(high_to_low))   # ["poor","ok","good","best"]

#     placeholder = "Select…"

#     # items: (pos, bucket_key, cur_da)
#     items = []
#     for pos in range(1, cfg.num_translations + 1):
#         bucket_label = st.session_state.get(f"bucket_pos_{cur_item_id}_{pos}", placeholder)
#         if bucket_label == placeholder:
#             raise ValueError("All buckets must be selected before auto-ordering DA.")
#         b_key = label_to_key[str(bucket_label)]

#         da_model_key = f"da_val_{cur_item_id}_{pos}"
#         cur_da = st.session_state.get(da_model_key, cfg.da_min)
#         try:
#             cur_da = int(cur_da)
#         except Exception:
#             cur_da = int(cfg.da_min)

#         items.append((pos, b_key, cur_da))

#     # Group by bucket (low->high), sort within bucket by current DA
#     grouped = {k: [] for k in low_to_high}
#     for pos, b_key, cur_da in items:
#         grouped[b_key].append((pos, cur_da))

#     for b in low_to_high:
#         grouped[b].sort(key=lambda x: x[1])

#     current_floor = int(cfg.da_min)

#     for b in low_to_high:
#         bucket_items = grouped[b]
#         if not bucket_items:
#             continue

#         # preserve relative spacing within bucket, but start at current_floor
#         das = [cur_da for _, cur_da in bucket_items]
#         base = das[0]
#         rel = [d - base for d in das]
#         proposed = [current_floor + r for r in rel]

#         # clamp/compress into range if needed
#         max_allowed = int(cfg.da_max)
#         if proposed[-1] > max_allowed:
#             if len(proposed) == 1:
#                 proposed = [min(max_allowed, current_floor)]
#             else:
#                 span = proposed[-1] - proposed[0]
#                 target_span = max_allowed - current_floor
#                 if span <= 0:
#                     proposed = [current_floor for _ in proposed]
#                 else:
#                     proposed = [int(round(current_floor + (r / span) * target_span)) for r in rel]

#         # ensure non-decreasing after rounding
#         for i in range(1, len(proposed)):
#             if proposed[i] < proposed[i - 1]:
#                 proposed[i] = proposed[i - 1]

#         # write back to MODEL keys only
#         for (pos, _), newv in zip(bucket_items, proposed):
#             newv = max(int(cfg.da_min), min(int(cfg.da_max), int(newv)))
#             st.session_state[f"da_val_{cur_item_id}_{pos}"] = newv

#         # next bucket must start >= (max in this bucket + gap)
#         current_floor = min(int(cfg.da_max), int(proposed[-1]) + int(gap))


# ---------------- Validate & Commit ----------------
def _collect_current_inputs() -> Tuple[Dict[str, Optional[str]], Dict[str, Optional[int]]]:
    bucket_by_t: Dict[str, Optional[str]] = {}
    da_by_t: Dict[str, Optional[int]] = {}

    bucket_placeholder = "Select…"

    for pos in range(1, cfg.num_translations + 1):
        tcol = display_map[pos]  # e.g., "t7"

        # bucket radio value is a LABEL (or placeholder)
        bucket_label = st.session_state.get(f"bucket_pos_{cur_item_id}_{pos}", bucket_placeholder)
        if bucket_label == bucket_placeholder:
            bucket_by_t[tcol] = None
        else:
            bucket_by_t[tcol] = label_to_key[str(bucket_label)]

        # DA comes from model key
        da_val = st.session_state.get(f"da_val_{cur_item_id}_{pos}")
        da_by_t[tcol] = int(da_val) if da_val is not None else None

    return bucket_by_t, da_by_t


def _write_eval_row(bucket_by_t: Dict[str, str], da_by_t: Dict[str, int]) -> None:
    # Write bucket_t# / da_t# in canonical t order
    for i in range(1, cfg.num_translations + 1):
        tcol = f"t{i}"
        eval_ws.cell(row=r, column=ec[f"bucket_t{i}"]).value = bucket_by_t[tcol]
        eval_ws.cell(row=r, column=ec[f"da_t{i}"]).value = int(da_by_t[tcol])

    eval_ws.cell(row=r, column=ec["comment"]).value = comment or ""

    # Commit semantics
    prev_committed = eval_ws.cell(row=r, column=ec["committed_at"]).value
    if prev_committed:
        # increment edit_count
        prev = eval_ws.cell(row=r, column=ec["edit_count"]).value
        try:
            prev_i = int(prev) if prev is not None else 0
        except Exception:
            prev_i = 0
        eval_ws.cell(row=r, column=ec["edit_count"]).value = prev_i + 1
    else:
        eval_ws.cell(row=r, column=ec["edit_count"]).value = 0

    eval_ws.cell(row=r, column=ec["committed_at"]).value = now_iso_utc()

    # Compute row_eval_hash
    row_input_hash = str(eval_ws.cell(row=r, column=ec["row_input_hash"]).value or "")
    row_eval_hash = compute_row_eval_hash(
        bucket_by_t=bucket_by_t,
        da_by_t=da_by_t,
        comment=comment or "",
        display_map_json=display_map_json,
        row_input_hash=row_input_hash,
        run_id=state.run_id,
    )
    eval_ws.cell(row=r, column=ec["row_eval_hash"]).value = row_eval_hash


def _current_committed_at() -> Optional[str]:
    return eval_ws.cell(row=r, column=ec["committed_at"]).value


def get_cur_idx():
    idx = st.session_state.current_item_idx
    return max(0, min(idx, N_items - 1))


def _rank_for_pos(pos: int) -> Tuple[int, int, int]:
    """
    Sort key: (bucket_rank_best_first, -da, pos)
    Unselected bucket goes to bottom.
    """
    bucket_label = st.session_state.get(f"bucket_pos_{cur_item_id}_{pos}", BUCKET_PLACEHOLDER)
    if bucket_label == BUCKET_PLACEHOLDER:
        bucket_rank = 999
    else:
        b_key = label_to_key.get(str(bucket_label))
        bucket_rank = bucket_key_to_rank_best_first.get(b_key, 999)

    da_val = st.session_state.get(f"da_val_{cur_item_id}_{pos}", cfg.da_min)
    try:
        da_i = int(da_val)
    except Exception:
        da_i = int(cfg.da_min)

    return (bucket_rank, -da_i, pos)


def _ensure_display_order(*, cfg: RunConfig, cur_item_id: int) -> List[int]:
    """Ensure a stable display order list exists for the current sentence.

    The order is stored as a list of display positions (1..num_translations).
    """
    order_key = _get_order_key(cur_item_id)
    grouped_key = _get_order_grouped_key(cur_item_id)

    expected = list(range(1, cfg.num_translations + 1))
    cur = st.session_state.get(order_key)

    if not isinstance(cur, list) or len(cur) != len(expected) or sorted(cur) != expected:
        st.session_state[order_key] = expected
        st.session_state[grouped_key] = False

    return list(st.session_state[order_key])


def _reorder_by_bucket(*, cfg: RunConfig, cur_item_id: int) -> None:
    """Manual reorder: group best->poor, keep within-bucket stable."""
    order_key = _get_order_key(cur_item_id)
    grouped_key = _get_order_grouped_key(cur_item_id)

    cur_order = _ensure_display_order(cfg=cfg, cur_item_id=cur_item_id)
    idx = {p: i for i, p in enumerate(cur_order)}

    def _k(p: int):
        bucket_rank, _neg_da, _pos = _rank_for_pos(p)
        return (bucket_rank, idx.get(p, 9999))

    st.session_state[order_key] = sorted(cur_order, key=_k)
    st.session_state[grouped_key] = True


def _reorder_by_bucket_and_da(*, cfg: RunConfig, cur_item_id: int) -> None:
    """Manual reorder: group best->poor, then DA desc within bucket."""
    order_key = _get_order_key(cur_item_id)
    grouped_key = _get_order_grouped_key(cur_item_id)

    cur_order = _ensure_display_order(cfg=cfg, cur_item_id=cur_item_id)
    st.session_state[order_key] = sorted(cur_order, key=_rank_for_pos)
    st.session_state[grouped_key] = True


def _maybe_auto_reorder_on_bucket_change(*, cfg: RunConfig, cur_item_id: int) -> None:
    """Automatic reorder after a bucket change (if enabled)."""
    if not st.session_state.get("auto_reorder_on_bucket_select", True):
        return
    # If DA auto-reorder is enabled, use full ranking. Otherwise keep DA-stable.
    if st.session_state.get("auto_reorder_on_da_select", True):
        _reorder_by_bucket_and_da(cfg=cfg, cur_item_id=cur_item_id)
    else:
        _reorder_by_bucket(cfg=cfg, cur_item_id=cur_item_id)


def _maybe_auto_reorder_on_da_change(*, cfg: RunConfig, cur_item_id: int) -> None:
    """Automatic reorder after a DA change (if enabled).

    Note: DA ordering is *within bucket*, so we only auto-reorder if bucket auto-reorder
    is enabled, or if the user has manually bucket-grouped this sentence.
    """
    if not st.session_state.get("auto_reorder_on_da_select", True):
        return
    if st.session_state.get("auto_reorder_on_bucket_select", True) or st.session_state.get(
        _get_order_grouped_key(cur_item_id), False
    ):
        _reorder_by_bucket_and_da(cfg=cfg, cur_item_id=cur_item_id)

# ---------------- UI: Upload / Init ----------------
try:
    cfg = load_config("config.yaml")
    st.session_state.cfg = cfg
except Exception as e:
    st.error(f"Config error: {e}")
    st.stop()

# --- Derived DA ranges based on intra-bucket options (quartiles)
# New YAML keys (recommended):
#   da_intra_bucket_options: <int>
#   bucket_colors:
#     best: {bg: "#...", border: "#..."}
#     good: {bg: "#...", border: "#..."}
#     ok:   {bg: "#...", border: "#..."}
#     poor: {bg: "#...", border: "#..."}
bucket_order_best_first = [b.key for b in cfg.buckets]

da_intra = getattr(cfg, "da_intra_bucket_options", None)
if da_intra is None:
    # Safe default to keep the app functional if config hasn't been updated yet.
    da_intra = 3
    st.warning(
        "config.yaml is missing 'da_intra_bucket_options'. Defaulting to 3. "
        "Add it to control the slider ranges.",
        icon="⚠️",
    )

try:
    da_intra = int(da_intra)
except Exception:
    da_intra = 3

da_intra = max(1, da_intra)

# Force DA global range to match 4 buckets * intra options, and keep integer-only semantics.
derived_da_min = 1
derived_da_max = int(len(bucket_order_best_first) * da_intra)
try:
    setattr(cfg, "da_min", int(derived_da_min))
    setattr(cfg, "da_max", int(derived_da_max))
except Exception:
    # If cfg is immutable, we still use the derived values locally in the UI.
    pass

# Default bucket colors (tinted bg + strong border)
default_bucket_colors = {
    "best": {"bg": "#E9F7EF", "border": "#1E8E3E"},   # green
    "good": {"bg": "#FFF4E5", "border": "#FB8C00"},   # orange
    "ok":   {"bg": "#E8F1FF", "border": "#1A73E8"},   # blue
    "poor": {"bg": "#FDECEC", "border": "#D93025"},   # red
}
bucket_colors = getattr(cfg, "bucket_colors", None) or default_bucket_colors

# Global CSS for bucket highlighting.
# IMPORTANT: Only change border color (no background tint), and scope selectors tightly
# so we don't accidentally style large parent blocks.
st.markdown(
    """
<style>
    .bucket-marker { display:none; }

/* Remove vertical margins added by st.markdown for bucket title */
div[data-testid="stLayoutWrapper"]:has(span.mt-card-hook)
  .bucket-title {
    display: inline-block;
    margin: 0 !important;
    padding: 0 !important;
    line-height: 1.0;
}

/* Tighten padding inside bordered containers used as MT cards */
/* Make hook span layout-neutral */
    .mt-card-hook {
    display: block;
    height: 0;
    margin: 0 !important;
    padding: 0 !important;
    }

/* Tighten padding inside MT cards */
    div[data-testid="stLayoutWrapper"]:has(span.mt-card-hook) > div {
    padding: 0.0rem 0.75rem !important;
    }

/* Remove top margin from first child inside card */
    div[data-testid="stLayoutWrapper"]:has(span.mt-card-hook)
    > div
    > *:first-child {
        margin-top: 0 !important;
    }

/* Kill the element spacing that Streamlit adds around markdown blocks INSIDE a card */
    div[data-testid="stLayoutWrapper"]:has(span.mt-card-hook) .element-container {
    margin-top: 0 !important;
    margin-bottom: 0 !important;
    }

    div[data-testid="stLayoutWrapper"]:has(span.mt-card-hook) div[data-testid="stMarkdownContainer"] {
    margin-top: 0 !important;
    margin-bottom: 0 !important;
    padding-top: 0 !important;
    padding-bottom: 0 !important;
    }

/* Also remove the default paragraph margin inside that markdown container */
    div[data-testid="stLayoutWrapper"]:has(span.mt-card-hook) div[data-testid="stMarkdownContainer"] p {
    margin-top: 0 !important;
    margin-bottom: 0 !important; /* tweak to taste */
    }

div[data-testid="stLayoutWrapper"]:has(span.mt-card-hook) .mt-translation {
  margin: 0 0 0 0;
  padding: 0;
}

/* Style the wrapper that actually exists around each translation "card" */
    div[data-testid="stLayoutWrapper"]:has(span.mt-card-hook):has(span.bucket-marker.bucket-best) {
        border-radius: 12px;
        border: 2px solid transparent;
    }
    div[data-testid="stLayoutWrapper"]:has(span.mt-card-hook):has(span.bucket-marker.bucket-good) {
    border-radius: 12px;
    border: 2px solid transparent;
    }
    div[data-testid="stLayoutWrapper"]:has(span.mt-card-hook):has(span.bucket-marker.bucket-ok) {
        border-radius: 12px;
        border: 2px solid transparent;
    }
    div[data-testid="stLayoutWrapper"]:has(span.mt-card-hook):has(span.bucket-marker.bucket-poor) {
        border-radius: 12px;
        border: 2px solid transparent;
    }

    div[data-testid="stLayoutWrapper"]:has(span.bucket-marker.bucket-best) { border-color: var(--best-border) !important; }
    div[data-testid="stLayoutWrapper"]:has(span.bucket-marker.bucket-good) { border-color: var(--good-border) !important; }
    div[data-testid="stLayoutWrapper"]:has(span.bucket-marker.bucket-ok)   { border-color: var(--ok-border)   !important; }
    div[data-testid="stLayoutWrapper"]:has(span.bucket-marker.bucket-poor) { border-color: var(--poor-border) !important; }

    .bucket-title { font-weight: 700; }
    div[data-testid="stLayoutWrapper"]:has(span.bucket-marker.bucket-best) .bucket-title { color: var(--best-border) !important; }
    div[data-testid="stLayoutWrapper"]:has(span.bucket-marker.bucket-good) .bucket-title { color: var(--good-border) !important; }
    div[data-testid="stLayoutWrapper"]:has(span.bucket-marker.bucket-ok)   .bucket-title { color: var(--ok-border)   !important; }
    div[data-testid="stLayoutWrapper"]:has(span.bucket-marker.bucket-poor) .bucket-title { color: var(--poor-border) !important; }
</style>
""",
    unsafe_allow_html=True,
)

# Set CSS variables from config / defaults
def _css_var_block(colors: dict) -> str:
    def g(k, field, fallback):
        try:
            return str(colors.get(k, {}).get(field, fallback))
        except Exception:
            return str(fallback)

    return f"""
<style>
  :root {{
    --best-bg: {g('best','bg', default_bucket_colors['best']['bg'])};
    --best-border: {g('best','border', default_bucket_colors['best']['border'])};
    --good-bg: {g('good','bg', default_bucket_colors['good']['bg'])};
    --good-border: {g('good','border', default_bucket_colors['good']['border'])};
    --ok-bg: {g('ok','bg', default_bucket_colors['ok']['bg'])};
    --ok-border: {g('ok','border', default_bucket_colors['ok']['border'])};
    --poor-bg: {g('poor','bg', default_bucket_colors['poor']['bg'])};
    --poor-border: {g('poor','border', default_bucket_colors['poor']['border'])};
  }}
</style>
"""

st.markdown(_css_var_block(bucket_colors), unsafe_allow_html=True)

with st.expander("Important Instructions. Please Read First. (Click here to show/hide these instructions)", expanded=True):
    st.markdown(
        instructions_md
    )


# ---------------- Sidebar controls ----------------
st.sidebar.markdown("### Dataset")
uploaded = st.sidebar.file_uploader(
    "Upload evaluation XLSX",
    type=["xlsx"],
    accept_multiple_files=False,
)

st.sidebar.markdown("### Ordering")
st.session_state.setdefault("auto_reorder_on_bucket_select", True)
st.session_state.setdefault("auto_reorder_on_da_select", True)

st.sidebar.toggle(
    "Auto-reorder on bucket selection",
    key="auto_reorder_on_bucket_select",
    value=bool(st.session_state.get("auto_reorder_on_bucket_select", True)),
)
st.sidebar.toggle(
    "Auto-reorder on DA selection",
    key="auto_reorder_on_da_select",
    value=bool(st.session_state.get("auto_reorder_on_da_select", True)),
    help="If enabled, DA changes will re-rank translations within their bucket (when bucket grouping is active).",
)

if uploaded is None and st.session_state.wb_state is None:
    st.info("Please Upload the researcher-provided Excel .xslx file to begin or resume.")
    st.stop()

# IMPORTANT: file_uploader retains its value across reruns. Only (re)load the workbook when
# the user actually uploads a *new* file, otherwise navigation will be reset every rerun.
if uploaded is not None:
    try:
        file_bytes = uploaded.read()
        digest = hashlib.sha1(file_bytes).hexdigest()

        is_new_upload = (
            st.session_state.wb_state is None
            or st.session_state.upload_digest != digest
            or st.session_state.upload_name != uploaded.name
        )

        if is_new_upload:
            st.session_state.upload_name = uploaded.name
            st.session_state.upload_digest = digest
            st.session_state.wb_state = load_workbook_from_upload(file_bytes, cfg)

            # On load: jump to first incomplete
            st.session_state.current_item_idx = 0
            _recompute_global_status()
            _jump_first_incomplete()
            st.success(f"Loaded workbook. Run ID: {st.session_state.wb_state.run_id}")
    except Exception as e:
        st.error(f"Failed to load workbook: {e}")
        st.stop()

state = st.session_state.wb_state
wb = state.wb
inputs_ws = wb["inputs"]
eval_ws = wb["eval"]
ic = state.inputs_col
ec = state.eval_col

# Recompute global status every run (fast enough for ~300x12)
_recompute_global_status()

_render_banner()

# ---------------- Navigation header ----------------
N_items = len(state.item_ids)

cur_idx = get_cur_idx()
r = _get_row_index(cur_idx)
cur_item_id = state.item_ids[cur_idx]

# Sidebar: manual reordering controls (also mirrored in main UI below)
st.sidebar.markdown("### Manual reorder")
sb_cols = st.sidebar.columns(2)
with sb_cols[0]:
    if st.sidebar.button("Reorder: buckets", use_container_width=True):
        _reorder_by_bucket(cfg=cfg, cur_item_id=cur_item_id)
        st.rerun()
with sb_cols[1]:
    if st.sidebar.button("Reorder: buckets + DA", use_container_width=True):
        _reorder_by_bucket_and_da(cfg=cfg, cur_item_id=cur_item_id)
        st.rerun()

if "jump_to_sentence_pending" in st.session_state:
    st.session_state["jump_to_sentence"] = int(st.session_state.pop("jump_to_sentence_pending"))

# Initialize jump box once (do not overwrite user edits)
if "jump_to_sentence" not in st.session_state or st.session_state.get("jump_to_sentence") is None:
    st.session_state["jump_to_sentence"] = int(cur_idx + 1)

progress = f"Sentence {cur_idx + 1} / {N_items}"

top_cols = st.columns([3, 2, 3, 2])
with top_cols[0]:
    st.subheader(progress)

with top_cols[1]:
    if cfg.ui_show_jump_to:
        def _on_jump_sentence_change():
            # User enters 1-based sentence number; convert to 0-based index.
            try:
                target_idx = int(st.session_state["jump_to_sentence"]) - 1
            except Exception:
                target_idx = 0
            st.session_state.current_item_idx = max(0, min(target_idx, N_items - 1))
            # Keep the box synced to the new position
            _set_jump_to_sentence(int(st.session_state.current_item_idx) + 1)
            st.rerun()

        st.number_input(
            "Jump to sentence",
            min_value=1,
            max_value=max(1, N_items),
            step=1,
            key="jump_to_sentence",
            on_change=_on_jump_sentence_change,
            label_visibility="collapsed",
        )

with top_cols[2]:
    qc = st.columns(2)
    with qc[0]:
        if st.button("First incomplete", use_container_width=True):
            _jump_first_incomplete()
            st.rerun()
    with qc[1]:
        if st.button(
            "Next invalid",
            use_container_width=True,
            disabled=(len(st.session_state.invalid_item_ids) == 0),
        ):
            _jump_next_invalid()
            st.rerun()

with top_cols[3]:
    _download_button("Download checkpoint")

# ---------------- Load current row data ----------------
source = str(inputs_ws.cell(row=r, column=ic["source"]).value or "")

display_map_json = str(eval_ws.cell(row=r, column=ec["display_map_json"]).value or "")
display_map = parse_display_map(display_map_json, cfg.num_translations)  # pos -> "t#"

# Ensure started_at is set when first opened
started_at = eval_ws.cell(row=r, column=ec["started_at"]).value
if not started_at:
    eval_ws.cell(row=r, column=ec["started_at"]).value = now_iso_utc()

committed_at = eval_ws.cell(row=r, column=ec["committed_at"]).value
comment_val = eval_ws.cell(row=r, column=ec["comment"]).value or ""

# Per-sentence DA widget epoch (used to force DA widgets to re-instantiate after auto-order)
epoch_key = f"da_widget_epoch_{cur_item_id}"
st.session_state.setdefault(epoch_key, 0)

# ---------------- Main UI ----------------
st.markdown(f"<span style='margin-top:-0.5rem;font-weight:bold'>Source:</span><span style='margin-top:-0.5rem; font-weight:normal'> {source}</span>", unsafe_allow_html=True)
#st.markdown(f"<span style='margin-top:-0.5rem;font-weight:bold'>Translations:</span><span style='margin-top:-0.5rem; font-weight:normal'> (blind, grouped by bucket)", unsafe_allow_html=True)

bucket_labels = [b.label for b in cfg.buckets]
label_to_key = {b.label: b.key for b in cfg.buckets}
key_to_label = {b.key: b.label for b in cfg.buckets}

BUCKET_PLACEHOLDER = "Select…"
bucket_options = [BUCKET_PLACEHOLDER] + bucket_labels
assert len(bucket_options) > 1, "No bucket labels loaded from config"

# Build current values by display position
pos_to_current = []
for pos in range(1, cfg.num_translations + 1):
    tcol = display_map[pos]  # "t7", etc.
    t_idx = int(tcol[1:])    # 7
    text = str(inputs_ws.cell(row=r, column=ic[tcol]).value or "")

    existing_bucket = eval_ws.cell(row=r, column=ec[f"bucket_t{t_idx}"]).value
    existing_da = eval_ws.cell(row=r, column=ec[f"da_t{t_idx}"]).value

    # Normalize
    existing_bucket = str(existing_bucket) if existing_bucket else ""
    existing_da = int(existing_da) if existing_da is not None and str(existing_da).strip() != "" else None

    pos_to_current.append((pos, tcol, t_idx, text, existing_bucket, existing_da))

# Helper: best->poor rank using bucket *keys* from config
bucket_key_to_rank_best_first = {b.key: i for i, b in enumerate(cfg.buckets)}  # best=0 ... poor=3

# Initialize session_state defaults from workbook for bucket + DA (so sorting works immediately)
for pos, tcol, t_idx, text, existing_bucket_key, existing_da in pos_to_current:
    bucket_key = f"bucket_pos_{cur_item_id}_{pos}"
    da_model_key = f"da_val_{cur_item_id}_{pos}"

    if bucket_key not in st.session_state:
        if existing_bucket_key and existing_bucket_key in key_to_label:
            st.session_state[bucket_key] = key_to_label[existing_bucket_key]
        else:
            st.session_state[bucket_key] = BUCKET_PLACEHOLDER

    if da_model_key not in st.session_state:
        st.session_state[da_model_key] = int(existing_da) if existing_da is not None else int(cfg.da_min)

# Determine display order (stable unless auto/manual re-ordered)
positions = [pos for (pos, *_rest) in pos_to_current]
order_key = _get_order_key(cur_item_id)

# First time we open this sentence in this session: initialize display order.
# Defaults match prior behavior (group by bucket; optionally sort by DA),
# but subsequent reruns will keep the stored order unless auto/manual reorder happens.
if order_key not in st.session_state:
    st.session_state[order_key] = list(range(1, cfg.num_translations + 1))
    st.session_state[_get_order_grouped_key(cur_item_id)] = False
    if st.session_state.get("auto_reorder_on_bucket_select", True):
        if st.session_state.get("auto_reorder_on_da_select", True):
            _reorder_by_bucket_and_da(cfg=cfg, cur_item_id=cur_item_id)
        else:
            _reorder_by_bucket(cfg=cfg, cur_item_id=cur_item_id)
else:
    _ensure_display_order(cfg=cfg, cur_item_id=cur_item_id)

positions = list(st.session_state[order_key])

# Render per translation (in sorted order)
for pos in positions:
    # Look up original tuple by pos
    for tup in pos_to_current:
        if tup[0] == pos:
            _, tcol, t_idx, text, existing_bucket_key, existing_da = tup
            break

    # Determine current bucket key (for styling + slider range)
    bucket_label_current = st.session_state.get(f"bucket_pos_{cur_item_id}_{pos}", BUCKET_PLACEHOLDER)
    bucket_key_current = label_to_key.get(str(bucket_label_current)) if bucket_label_current != BUCKET_PLACEHOLDER else None

    with st.container(border=True):
        # Marker used by CSS (:has) to tint this whole block by bucket
        # Note: keep marker inside the container.
        st.markdown(f"<span class='mt-card-hook'></span><span class='bucket-marker bucket-{bucket_key_current}'></span>", unsafe_allow_html=True)

        col1, col2 = st.columns([1, 1], vertical_alignment="top")
        with col1:
            st.markdown(f"<span class='bucket-title'>{pos}) {source}</span>", unsafe_allow_html=True)
            st.markdown(f"<div class='mt-translation'>{text}</div>", unsafe_allow_html=True)
            
            # --- Keys
            bucket_key = f"bucket_pos_{cur_item_id}_{pos}"

            # Model key (source of truth)
            da_model_key = f"da_val_{cur_item_id}_{pos}"

            # Widget keys include epoch so we can force re-instantiation after auto-order
            epoch = int(st.session_state[epoch_key])
            da_int_wkey = f"da_int_w_{cur_item_id}_{pos}_{epoch}"
            da_slider_wkey = f"da_slider_w_{cur_item_id}_{pos}_{epoch}"

            left, right = st.columns([4, 2], vertical_alignment="top")

            # Default radio index only used on first instantiation; key state controls thereafter
            bucket_label_current = st.session_state.get(bucket_key, BUCKET_PLACEHOLDER)
            if bucket_label_current in bucket_options:
                bucket_default_idx = bucket_options.index(bucket_label_current)
            else:
                bucket_default_idx = 0

            # Track previous bucket for DA remapping when the bucket changes
            prev_bucket_key_state = f"prev_bucket_key_{cur_item_id}_{pos}"
            if prev_bucket_key_state not in st.session_state:
                prev = label_to_key.get(str(bucket_label_current)) if bucket_label_current != BUCKET_PLACEHOLDER else None
                st.session_state[prev_bucket_key_state] = prev

            def _on_bucket_change(_pos=pos):
                bkey = f"bucket_pos_{cur_item_id}_{_pos}"
                dkey = f"da_val_{cur_item_id}_{_pos}"
                prev_key_state = f"prev_bucket_key_{cur_item_id}_{_pos}"

                new_label = st.session_state.get(bkey, BUCKET_PLACEHOLDER)
                new_bucket_key = label_to_key.get(str(new_label)) if new_label != BUCKET_PLACEHOLDER else None
                old_bucket_key = st.session_state.get(prev_key_state)

                # Remap only when both old and new are real buckets
                try:
                    old_da = int(st.session_state.get(dkey, int(cfg.da_min)))
                except Exception:
                    old_da = int(cfg.da_min)

                if new_bucket_key in bucket_order_best_first:
                    new_rng = _bucket_range_for_key(
                        bucket_key=str(new_bucket_key),
                        intra=int(da_intra),
                        bucket_order_best_first=bucket_order_best_first,
                    )

                    if old_bucket_key in bucket_order_best_first:
                        old_rng = _bucket_range_for_key(
                            bucket_key=str(old_bucket_key),
                            intra=int(da_intra),
                            bucket_order_best_first=bucket_order_best_first,
                        )
                        new_da = _remap_da_between_buckets(old_da=old_da, old_range=old_rng, new_range=new_rng)
                    else:
                        # No old bucket: keep the within-bucket offset based on current DA
                        offset = (max(1, old_da) - 1) % int(da_intra)
                        new_da = int(new_rng[0]) + int(offset)

                    st.session_state[dkey] = int(new_da)

                # Update prev bucket + bump epoch so the slider can re-instantiate with new min/max
                st.session_state[prev_key_state] = new_bucket_key
                st.session_state[epoch_key] = int(st.session_state.get(epoch_key, 0)) + 1

                # Optional auto-reorder (configurable)
                _maybe_auto_reorder_on_bucket_change(cfg=cfg, cur_item_id=cur_item_id)

            st.radio(
                f"Bucket {pos}",
                options=bucket_options,
                index=bucket_default_idx,
                horizontal=True,
                key=bucket_key,
                on_change=_on_bucket_change,
                label_visibility="collapsed",
            )

        with col2:
            # Slider = SOURCE OF TRUTH (range is restricted by the chosen bucket)
            bucket_label_for_slider = st.session_state.get(bucket_key, BUCKET_PLACEHOLDER)
            bucket_key_for_slider = label_to_key.get(str(bucket_label_for_slider)) if bucket_label_for_slider != BUCKET_PLACEHOLDER else None

            if bucket_key_for_slider in bucket_order_best_first:
                s_min, s_max = _bucket_range_for_key(
                    bucket_key=str(bucket_key_for_slider),
                    intra=int(da_intra),
                    bucket_order_best_first=bucket_order_best_first,
                )
                slider_disabled = False
                slider_help = None
            else:
                # Disabled until a bucket is selected
                s_min, s_max = int(cfg.da_min), int(cfg.da_min) + int(da_intra) - 1
                slider_disabled = True
                slider_help = "Choose a quartile bucket to enable the slider"

            # Clamp current model value into the slider range before rendering
            try:
                cur_da_model = int(st.session_state.get(da_model_key, int(cfg.da_min)))
            except Exception:
                cur_da_model = int(cfg.da_min)
            cur_da_model = max(int(s_min), min(int(s_max), int(cur_da_model)))
            st.session_state[da_model_key] = int(cur_da_model)

            def _on_da_slider_change(_skey: str, _mkey: str):
                try:
                    st.session_state[_mkey] = int(st.session_state[_skey])
                except Exception:
                    pass

                # Optional auto-reorder (configurable)
                _maybe_auto_reorder_on_da_change(cfg=cfg, cur_item_id=cur_item_id)
            
            st.write("Enter the direct assessment score for this translation (Note: Range changes based on quartile):")
            st.slider(
                f"DA {pos}",
                min_value=int(s_min),
                max_value=int(s_max),
                step=1,
                value=int(cur_da_model),
                key=da_slider_wkey,
                on_change=lambda sk=da_slider_wkey, mk=da_model_key: _on_da_slider_change(sk, mk),
                disabled=slider_disabled,
                help=slider_help,
                label_visibility="collapsed",
            )


# Manual reorder controls (main area, placed below translations and above comment)
main_reorder_cols = st.columns([1, 1, 6])
with main_reorder_cols[0]:
    if st.button("Reorder: buckets", use_container_width=True):
        _reorder_by_bucket(cfg=cfg, cur_item_id=cur_item_id)
        st.rerun()
with main_reorder_cols[1]:
    if st.button("Reorder: buckets + DA", use_container_width=True):
        _reorder_by_bucket_and_da(cfg=cfg, cur_item_id=cur_item_id)
        st.rerun()

st.markdown("### Comment (optional)")
comment = st.text_area("Comment", value=str(comment_val), key=f"comment_{cur_item_id}", label_visibility="collapsed")

# Show current-row validation state (live)
bucket_by_t_live, da_by_t_live = _collect_current_inputs()
live_ok, live_reasons = _soft_validate_live(cfg, bucket_by_t_live, da_by_t_live)

if live_reasons:
    st.warning("Current sentence issues:\n- " + "\n- ".join(live_reasons))

# Controls
ctrl = st.columns([1, 1, 1, 4])
with ctrl[0]:
    back_disabled = (cur_idx == 0)
    if cfg.ui_show_back and st.button("Back", disabled=back_disabled, use_container_width=True):
        st.session_state.current_item_idx = max(0, cur_idx - 1)
        _set_jump_to_sentence(int(st.session_state.current_item_idx) + 1)
        st.rerun()

with ctrl[1]:
    if st.button("Save (checkpoint)", use_container_width=True):
        st.success("Checkpoint ready — use Download checkpoint.")
        _recompute_global_status()

with ctrl[2]:
    if st.button("Next", key="next_btn", use_container_width=True, type="primary"):
        bucket_by_t, da_by_t = _collect_current_inputs()
        ok, commit_reasons = validate_row(
            cfg=cfg,
            bucket_by_t=bucket_by_t,
            da_by_t=da_by_t,
            committed_at="will_commit",
            require_complete_for_next=True,
        )
        if not ok:
            st.error("Cannot proceed. Fix:\n- " + "\n- ".join(commit_reasons))
        else:
            _write_eval_row(bucket_by_t, da_by_t)
            _recompute_global_status()
            st.session_state.current_item_idx = min(cur_idx + 1, N_items - 1)
            _set_jump_to_sentence(int(st.session_state.current_item_idx) + 1)
            st.rerun()

# ---------------- Finish / Summary ----------------
all_complete = (len(st.session_state.incomplete_item_ids) == 0)
no_invalid = (len(st.session_state.invalid_item_ids) == 0)

st.divider()

finish_cols = st.columns([2, 2, 8])
with finish_cols[0]:
    _download_button("Download checkpoint (xlsx)")

with finish_cols[1]:
    if st.button("Finish", disabled=not (all_complete and no_invalid), use_container_width=True, type="primary"):
        st.session_state["show_summary"] = True

if cfg.ui_show_completion_summary and st.session_state.get("show_summary") and all_complete and no_invalid:
    st.markdown("## Completion summary (blind, aggregate only)")

    # Aggregate DA stats across all translations / all rows
    all_scores: List[int] = []
    bucket_counts: Dict[str, int] = {k: 0 for k in cfg.bucket_keys}
    times: List[float] = []

    for idx, item_id in enumerate(state.item_ids):
        rr = _get_row_index(idx)

        for i in range(1, cfg.num_translations + 1):
            d = eval_ws.cell(row=rr, column=ec[f"da_t{i}"]).value
            b = eval_ws.cell(row=rr, column=ec[f"bucket_t{i}"]).value
            if d is not None:
                try:
                    all_scores.append(int(d))
                except Exception:
                    pass
            if b:
                bk = str(b)
                if bk in bucket_counts:
                    bucket_counts[bk] += 1

        s_at = eval_ws.cell(row=rr, column=ec["started_at"]).value
        c_at = eval_ws.cell(row=rr, column=ec["committed_at"]).value
        t = time_per_sentence_seconds(s_at, c_at)
        if t is not None:
            times.append(t)

    da_stats = aggregate_da_stats(all_scores)
    time_stats = summarize_times(times)

    st.info(
            "PLEASE DOWNLOAD THE CHECKPOINT NOW. This is the only way to save your work!  \n\n"
            "Download the final XLSX and send it to the research team.  \n"
            "Reminder: The analysis is not complete until all you download the checkpoint .xslx file and send it to the research team.  \n"
            "**The final .xlsx WILL NOT be available after you leave this screen, and all evaluations will be lost.**  \n\n"
            "See the total information below. No per-system statistics are shown."
            )

    st.markdown("### DA statistics (all translations pooled)")
    st.json(da_stats)

    st.markdown("### Bucket distribution (all translations pooled)")
    st.json({cfg.bucket_labels_by_key[k]: bucket_counts[k] for k in cfg.bucket_keys})

    st.markdown("### Time per sentence (seconds)")
    st.json(time_stats)




