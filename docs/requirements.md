# Requirements Document
**Project:** Streamlit MT Human Evaluation App  
**Version:** 1.0  
**Last updated:** 2026-01-13 21:46 UTC

---

## 1. Overview

This application enables efficient, bias-controlled human evaluation of machine translation (MT) outputs in a Streamlit web app. Evaluators upload a researcher-provided XLSX file containing source sentences and multiple target-language translations. The app presents translations in randomized order (blind to system identity), collects bucket assignments and Direct Assessment (DA) scores per translation, and allows evaluators to download an updated XLSX checkpoint at any time. The evaluator’s local XLSX file is the **source of truth**.

---

## 2. Stakeholders and Users

### 2.1 Stakeholders
- **Research team:** prepares input XLSX, configures run parameters, collects final XLSX, performs analysis.
- **Evaluator:** performs blind evaluation for a single target language (one evaluator per run/language).

### 2.2 User Personas
- **Professional evaluator:** fluent in English and the target language, expects a fast, low-friction UI and robust resume behavior.

---

## 3. Scope

### 3.1 In Scope
- Upload researcher-provided XLSX (two sheets: `inputs`, `eval`)
- Evaluate each source sentence’s translations:
  - Assign each translation to one of four buckets (default keys: `best/good/ok/poor`)
  - Provide DA score (integer) within configurable range (default 0–100)
  - Optional one comment per source sentence
- Randomize display order per sentence while saving results back to the correct `t#` columns
- Back navigation and jump-to controls
- Resume from checkpoint XLSX:
  - Validate file on load
  - Auto-jump to **first incomplete** item
- Integrity controls:
  - Hash validation (hard fail on mismatch)
  - Row-level eval hashing
- Completion summary **without revealing system identity** (aggregate-only)
- Download updated XLSX checkpoints

### 3.2 Out of Scope
- Server-side persistence or multi-user concurrency
- Exposing system identity
- Error tagging taxonomy (free-text comment only)
- IRB/PII workflows

---

## 4. Configurability Requirements (RunConfig)

The app must support a run configuration loaded at startup (YAML/JSON) and treated as immutable for the run.

### 4.1 Required configurable parameters
- `num_translations` (N): number of translations per sentence (fixed for the run; v1 default N=12)
- DA scale:
  - `da_min` (v1 default 0)
  - `da_max` (v1 default 100)
  - `integer_only` (v1 true)
- Buckets:
  - ordered list of bucket **keys** (stored in data) and **labels** (shown in UI)
  - v1 keys: `best`, `good`, `ok`, `poor`
- Validation toggles:
  - enforce strict bucket ordering (v1 true)
  - allow empty buckets (v1 true)

### 4.2 Configuration invariants
- Configuration must match the uploaded XLSX schema (e.g., N must match `t1..tN` present).
- Configuration must not change mid-run.

---

## 5. Data Requirements

### 5.1 XLSX Structure (Required)
Input XLSX contains exactly:
- Sheet `inputs`: source + translations + `row_input_hash`
- Sheet `eval`: evaluation columns + `row_input_hash` + `row_eval_hash` + `run_id`

### 5.2 Identifiers
- `item_id` is an integer incremental ID (e.g., 1..300) and must be unique.

### 5.3 Blindness
- Column headers must be neutral (`t1..tN`).
- The UI must not display system identity.

---

## 6. Functional Requirements

### FR-1 Upload and Parse XLSX
- The app shall accept only `.xlsx` uploads.
- The app shall validate the workbook contains required sheets and columns.

### FR-2 Run Initialization
- The app shall generate or read a `run_id` (UUID) stored in `eval.run_id`.
- The app shall validate `num_translations` matches columns `t1..tN`.

### FR-3 Per-Sentence Display
- The app shall display one source sentence at a time.
- The app shall show N target translations in randomized order.
- The app shall render, for each translation:
  - bucket selection via horizontal radio buttons
  - DA score via slider + numeric input (kept consistent)
- The app shall allow **Back** navigation and **Jump-to** (dropdown of all items), plus quick jumps:
  - **First incomplete**
  - **Next invalid**

### FR-4 Randomization and Traceability
- The app shall randomize per sentence using a deterministic shuffle seeded by `hash(run_id + item_id)` (or equivalent stable method).
- The app shall store `display_map_json` in `eval` per `item_id`, mapping display positions to `t#`.

### FR-5 Validation (Current Sentence)
- The app shall require all N buckets and all N DAs to proceed.
- The app shall block **Next** if the current sentence is invalid.
- The app shall enforce strict DA ordering across adjacent non-empty buckets:
  - `max(good) < min(best)`, `max(ok) < min(good)`, `max(poor) < min(ok)`
- DA values must be integers within `[da_min, da_max]`.

### FR-6 Resume Behavior
- On upload, the app shall validate all rows and compute:
  - incomplete rows
  - invalid rows
- The app shall auto-jump to the **first incomplete** row (as defined below).

**Incomplete definition:** a row lacking any bucket value, any DA value, or `committed_at`.

### FR-7 Invalid Rows Banner and Finish Gate
- If any invalid rows exist, the app shall show a prominent banner with count and navigation to errant items.
- The app shall block **Finish** while any invalid rows exist.
- The app shall allow navigation and editing even when invalid rows exist.

### FR-8 Commit Semantics
- On **Next**, the app shall:
  - set `committed_at` to the current timestamp (overwrite if already present)
  - increment `edit_count` if this sentence was previously committed
  - compute and write `row_eval_hash`
- The app shall set `started_at` when a sentence is first opened (persisted in XLSX).

### FR-9 Download Checkpoints
- The app shall provide a **Download checkpoint** action that exports the entire workbook with updated `eval`.
- The evaluator can use a downloaded checkpoint to resume.

### FR-10 Completion Summary (Blind)
- When all items are complete and no invalid rows exist, the app shall show a completion screen with:
  - aggregate DA statistics across all translations
  - aggregate bucket distribution
  - time-per-sentence statistics based on `started_at` and `committed_at`
- The summary must not reveal per-system / per-`t#` statistics.

---

## 7. Non-Functional Requirements

### NFR-1 Reliability and Data Integrity
- Hard fail on:
  - hash mismatch (`row_input_hash` mismatch between `inputs` and `eval`, or recomputation mismatch)
  - schema mismatch (missing columns/sheets)
  - duplicate or missing `item_id`
- Soft fail (banner + Finish blocked) on:
  - invalid evaluations (ordering violations, out-of-range DA, etc.)

### NFR-2 Performance
- Must remain responsive for ~300 sentences × N translations.
- File processing should complete within a few seconds on typical evaluator hardware.

### NFR-3 Usability
- Minimize clicks:
  - radio buckets + DA inputs visible together
- Clear error messages:
  - identify which sentences are invalid and why

### NFR-4 Security / Privacy
- No PII expected; no special IRB constraints.
- System identities must not be shown in UI.

---

## 8. Acceptance Criteria (Checklist)

- [ ] Upload validates required sheets and columns (`inputs`, `eval`)
- [ ] App randomizes translation display order and stores `display_map_json`
- [ ] Evaluator can assign all N buckets and DAs
- [ ] Next is blocked until current sentence passes strict ordering validation
- [ ] Resume jumps to first incomplete row
- [ ] Invalid banner appears when any invalid rows exist; Finish blocked
- [ ] Download checkpoint works and contains all saved work
- [ ] Hash mismatch produces hard fail
- [ ] Completion summary shows aggregate-only statistics
