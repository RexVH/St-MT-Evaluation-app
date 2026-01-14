from __future__ import annotations

import json
from io import BytesIO
from typing import Dict, List, Optional, Tuple

import streamlit as st

from mt_eval.config import load_config, RunConfig
from mt_eval.xlsx_io import load_workbook_from_upload, save_workbook_to_bytes, now_iso_utc
from mt_eval.validation import parse_display_map, validate_row, is_row_incomplete
from mt_eval.hashing import compute_row_eval_hash
from mt_eval.stats import aggregate_da_stats, time_per_sentence_seconds, summarize_times


st.set_page_config(page_title="MT Human Evaluation", layout="wide")


# ---------------- Session State ----------------
def ss_init():
    st.session_state.setdefault("cfg", None)
    st.session_state.setdefault("wb_state", None)  # WorkbookState
    st.session_state.setdefault("current_item_idx", 0)
    st.session_state.setdefault("row_reasons_cache", {})  # item_id -> reasons list (invalid)
    st.session_state.setdefault("invalid_item_ids", [])
    st.session_state.setdefault("incomplete_item_ids", [])
    st.session_state.setdefault("upload_name", None)


ss_init()


# ---------------- Helpers ----------------
def _write_da_from_widget(widget_key: str, model_key: str):
    st.session_state[model_key] = int(st.session_state[widget_key])


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
    st.session_state.current_item_idx = state.item_ids.index(target_item_id)


def _jump_first_incomplete():
    inc = st.session_state.incomplete_item_ids
    if inc:
        _jump_to_item_id(inc[0])


def _jump_next_invalid():
    inv = st.session_state.invalid_item_ids
    if not inv:
        return
    state = st.session_state.wb_state
    cur_item_id = state.item_ids[st.session_state.current_item_idx]
    after = [x for x in inv if x > cur_item_id]
    _jump_to_item_id(after[0] if after else inv[0])


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
        file_name=fname.replace(".xlsx", "") + "_checkpoint.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )


def _auto_order_da_by_bucket(
    *,
    cfg,
    cur_item_id: int,
    label_to_key: dict,
    gap: int = 1,
):
    """
    Rewrites DA values in session_state so that:
      Poor < OK < Good < Best with strict separation (by >= gap)

    Only uses the bucket assignments already selected in the UI.
    Writes ONLY to the model keys: da_val_{cur_item_id}_{pos}
    """
    high_to_low = [b.key for b in cfg.buckets]   # e.g. ["best","good","ok","poor"]
    low_to_high = list(reversed(high_to_low))   # ["poor","ok","good","best"]

    placeholder = "Select…"

    # items: (pos, bucket_key, cur_da)
    items = []
    for pos in range(1, cfg.num_translations + 1):
        bucket_label = st.session_state.get(f"bucket_pos_{cur_item_id}_{pos}", placeholder)
        if bucket_label == placeholder:
            raise ValueError("All buckets must be selected before auto-ordering DA.")
        b_key = label_to_key[str(bucket_label)]

        da_model_key = f"da_val_{cur_item_id}_{pos}"
        cur_da = st.session_state.get(da_model_key, cfg.da_min)
        try:
            cur_da = int(cur_da)
        except Exception:
            cur_da = int(cfg.da_min)

        items.append((pos, b_key, cur_da))

    # Group by bucket (low->high), sort within bucket by current DA
    grouped = {k: [] for k in low_to_high}
    for pos, b_key, cur_da in items:
        grouped[b_key].append((pos, cur_da))

    for b in low_to_high:
        grouped[b].sort(key=lambda x: x[1])

    current_floor = int(cfg.da_min)

    for b in low_to_high:
        bucket_items = grouped[b]
        if not bucket_items:
            continue

        # preserve relative spacing within bucket, but start at current_floor
        das = [cur_da for _, cur_da in bucket_items]
        base = das[0]
        rel = [d - base for d in das]
        proposed = [current_floor + r for r in rel]

        # clamp/compress into range if needed
        max_allowed = int(cfg.da_max)
        if proposed[-1] > max_allowed:
            if len(proposed) == 1:
                proposed = [min(max_allowed, current_floor)]
            else:
                span = proposed[-1] - proposed[0]
                target_span = max_allowed - current_floor
                if span <= 0:
                    proposed = [current_floor for _ in proposed]
                else:
                    proposed = [int(round(current_floor + (r / span) * target_span)) for r in rel]

        # ensure non-decreasing after rounding
        for i in range(1, len(proposed)):
            if proposed[i] < proposed[i - 1]:
                proposed[i] = proposed[i - 1]

        # write back to MODEL keys only
        for (pos, _), newv in zip(bucket_items, proposed):
            newv = max(int(cfg.da_min), min(int(cfg.da_max), int(newv)))
            st.session_state[f"da_val_{cur_item_id}_{pos}"] = newv

        # next bucket must start >= (max in this bucket + gap)
        current_floor = min(int(cfg.da_max), int(proposed[-1]) + int(gap))


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


# ---------------- UI: Upload / Init ----------------
st.title("Machine Translation Human Evaluation")

try:
    cfg = load_config("config.yaml")
    st.session_state.cfg = cfg
except Exception as e:
    st.error(f"Config error: {e}")
    st.stop()

with st.expander("Run configuration (frozen for this run)", expanded=False):
    st.json(
        {
            "num_translations": cfg.num_translations,
            "da": {"min": cfg.da_min, "max": cfg.da_max, "integer_only": cfg.da_integer_only},
            "buckets": [{"key": b.key, "label": b.label} for b in cfg.buckets],
            "validation": {
                "enforce_bucket_ordering": cfg.enforce_bucket_ordering,
                "allow_empty_buckets": cfg.allow_empty_buckets,
            },
        }
    )

uploaded = st.file_uploader("Upload evaluation XLSX", type=["xlsx"], accept_multiple_files=False)

if uploaded is None and st.session_state.wb_state is None:
    st.info("Upload the researcher-provided XLSX to begin or resume.")
    st.stop()

if uploaded is not None:
    try:
        file_bytes = uploaded.read()
        st.session_state.upload_name = uploaded.name
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
def get_cur_idx():
    idx = st.session_state.current_item_idx
    return max(0, min(idx, N_items - 1))

cur_idx = get_cur_idx()
r = _get_row_index(cur_idx)
cur_item_id = state.item_ids[cur_idx]
progress = f"Sentence {cur_idx + 1} / {N_items}"

top_cols = st.columns([3, 2, 3, 2])
with top_cols[0]:
    st.subheader(progress)

with top_cols[1]:
    if cfg.ui_show_jump_to:
        chosen = st.selectbox(
            "Jump to",
            options=state.item_ids,
            index=cur_idx,
            format_func=lambda x: f"item_id={x}",
            label_visibility="collapsed",
        )
        if chosen != cur_item_id:
            _jump_to_item_id(int(chosen))
            st.rerun()

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
st.markdown("### Source")
st.write(source)

st.markdown("### Translations (blind, grouped by bucket)")

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

# Determine display order (always grouped best->poor, then DA desc)
positions = [pos for (pos, *_rest) in pos_to_current]
positions = sorted(positions, key=_rank_for_pos)

# Render per translation (in sorted order)
for pos in positions:
    # Look up original tuple by pos
    for tup in pos_to_current:
        if tup[0] == pos:
            _, tcol, t_idx, text, existing_bucket_key, existing_da = tup
            break

    with st.container(border=True):
        col1, col2 = st.columns([1, 1], vertical_alignment="top")
        with col1:
            st.markdown(f"**{pos}) {source}**")
            st.write(text)

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

            st.radio(
                f"Bucket {pos}",
                options=bucket_options,
                index=bucket_default_idx,
                horizontal=True,
                key=bucket_key,
                label_visibility="collapsed",
            )

        with col2:
            st.write(f"Input the direct assessment (DA) score for translation {pos} as an integer and press <Enter>.")
            # Number input = SOURCE OF TRUTH
            st.number_input(
                f"DA {pos}",
                min_value=int(cfg.da_min),
                max_value=int(cfg.da_max),
                step=1,
                value=int(st.session_state[da_model_key]),
                key=f"da_number_{cur_item_id}_{pos}",
                on_change=lambda k=f"da_number_{cur_item_id}_{pos}", m=da_model_key: (
                    st.session_state.__setitem__(m, int(st.session_state[k]))
                ),
                label_visibility="collapsed",
            )

            # Slider = MIRROR (read-only)
            st.slider(
                f"DA slider {pos}",
                min_value=int(cfg.da_min),
                max_value=int(cfg.da_max),
                step=1,
                value=int(st.session_state[da_model_key]),
                disabled=True,
                label_visibility="collapsed",
            )


auto_cols = st.columns([2, 8])
with auto_cols[0]:
    if st.button("Auto-order DA by bucket", use_container_width=True):
        try:
            _auto_order_da_by_bucket(
                cfg=cfg,
                cur_item_id=cur_item_id,
                label_to_key=label_to_key,
                gap=1,
            )
            # Force DA widgets to re-instantiate with updated model values
            st.session_state[epoch_key] += 1
            st.success("Re-ordered DA scores to respect bucket ordering.")
            st.rerun()
        except Exception as e:
            st.error(str(e))

st.markdown("### Comment (optional)")
comment = st.text_area("Comment", value=str(comment_val), key=f"comment_{cur_item_id}", label_visibility="collapsed")

# Show current-row validation state (live)
bucket_by_t_live, da_by_t_live = _collect_current_inputs()
live_ok, live_reasons = _soft_validate_live(cfg, bucket_by_t_live, da_by_t_live)

if live_reasons:
    st.warning("Current sentence issues:\n- " + "\n- ".join(live_reasons))

# Controls
ctrl = st.columns([1, 1, 1, 6])
with ctrl[0]:
    back_disabled = (cur_idx == 0)
    if cfg.ui_show_back and st.button("Back", disabled=back_disabled, use_container_width=True):
        st.session_state.current_item_idx = max(0, cur_idx - 1)
        st.rerun()

with ctrl[1]:
    if st.button("Save (checkpoint)", use_container_width=True):
        st.success("Checkpoint ready — use Download checkpoint.")
        _recompute_global_status()

with ctrl[2]:
    if st.button("Next", use_container_width=True, type="primary"):
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
        st.session_state.current_item_idx = cur_idx + 1
        st.session_state.current_item_idx = min(
            st.session_state.current_item_idx, N_items - 1
        )
        st.rerun()


# ---------------- Finish / Summary ----------------
all_complete = (len(st.session_state.incomplete_item_ids) == 0)
no_invalid = (len(st.session_state.invalid_item_ids) == 0)

st.divider()

finish_cols = st.columns([2, 2, 8])
with finish_cols[0]:
    _download_button("Download current XLSX")

with finish_cols[1]:
    if st.button("Finish", disabled=not (all_complete and no_invalid), use_container_width=True):
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

    st.markdown("### DA statistics (all translations pooled)")
    st.json(da_stats)

    st.markdown("### Bucket distribution (all translations pooled)")
    st.json({cfg.bucket_labels_by_key[k]: bucket_counts[k] for k in cfg.bucket_keys})

    st.markdown("### Time per sentence (seconds)")
    st.json(time_stats)

    st.info("Download the final XLSX and send it to the research team. No per-system statistics are shown.")
