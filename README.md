# St-MT-Evaluation-app
Streamlit based application for machine translation system evaluation

Project: Streamlit MT Human Evaluation App
Audience: Research team, developers
Deployment: Streamlit Community Cloud
Persistence Model: Client-as-Source-of-Truth (2A)

1. Goals and Non-Goals
1.1 Goals

Enable efficient, bias-controlled human evaluation of machine translation (MT) outputs.

Support blind evaluation of multiple MT systems.

Allow resume, backtracking, and jump-to navigation without data loss.

Guarantee data integrity through validation and hashing.

Produce a clean XLSX artifact suitable for downstream research analysis.

Be highly configurable per run without code changes.

1.2 Non-Goals

No exposure of MT system identity to evaluators.

No server-side persistence or multi-user collaboration.

No real-time analytics during evaluation.

No IRB / PII handling.

2. High-Level Architecture

Evaluator (Browser)
→ uploads XLSX
→ Streamlit App (stateless server)
→ evaluator downloads updated XLSX
→ local filesystem is the source of truth

Design principle:
All authoritative state lives in the XLSX file provided to and returned by the evaluator.

3. Run Configuration (Parameterization)
3.1 Configuration Object

Loaded at app startup (YAML or JSON) and frozen per run.

run:
  num_translations: 12

da:
  min: 0
  max: 100
  integer_only: true

buckets:
  - key: best
    label: Best
  - key: good
    label: Good
  - key: ok
    label: OK
  - key: poor
    label: Poor

validation:
  enforce_bucket_ordering: true
  allow_empty_buckets: true

ui:
  show_back_button: true
  show_jump_to: true
  show_completion_summary: true

3.2 Immutability Rule

Configuration is validated against the uploaded XLSX.

Once evaluation begins, configuration must not change.

4. XLSX Data Contract
4.1 Sheet: inputs (Researcher-provided, read-only)
Column	Description
item_id	Incremental sentence ID (e.g., 1–300)
source	Source sentence (English)
t1…tN	Target translations (neutral columns)
row_input_hash	Hash of source + t1..tN
4.2 Sheet: eval (App-generated / updated)
Column	Description
item_id	Foreign key
bucket_t1…bucket_tN	Bucket assignment (best/good/ok/poor)
da_t1…da_tN	Integer DA score
comment	Optional free-text comment
started_at	Timestamp when sentence is first opened
committed_at	Timestamp when evaluator clicks “Next”
edit_count	Number of re-commits
display_map_json	Display-slot → translation-column mapping
row_input_hash	Copied from inputs
row_eval_hash	Hash of evaluation fields
run_id	UUID identifying the evaluation run
5. Randomization Design
5.1 Deterministic Shuffle

For each sentence:

seed = hash(run_id + item_id)
shuffle(t1..tN)

This guarantees:

Different random orders per sentence

Reproducibility within a run

No system identity leakage

5.2 display_map_json

Stored per sentence in the eval sheet.

Example:

{
"1": "t7",
"2": "t3",
"3": "t11",
"4": "t1",
"5": "t9",
"6": "t4",
"7": "t12",
"8": "t2",
"9": "t6",
"10": "t10",
"11": "t5",
"12": "t8"
}

Purpose:

Auditability

Debugging

Reproducibility across UI changes

6. UI Flow (Per Sentence)
6.1 Header

Source sentence

Progress indicator (“Sentence X / N”)

Jump-to dropdown (all sentences)

Quick-jump buttons:

First incomplete

Next invalid

6.2 Translation Panel

For each translation:

Translation text

Horizontal radio buttons for bucket selection

DA input (slider + numeric input)

6.3 Validation

DA range validation

Strict bucket ordering validation

Missing-field detection

6.4 Controls

Back

Next (blocked if current sentence invalid)

Download checkpoint

Finish (blocked if any invalid sentences exist)

7. Validation Model
7.1 Incomplete Sentence

A sentence is incomplete if:

Any bucket_t# missing, OR

Any da_t# missing, OR

committed_at missing

7.2 Invalid Sentence

A sentence is invalid if:

DA outside bounds or non-integer

Unknown bucket label

Strict bucket DA ordering violated

Duplicate item_id

Missing translation text

Hash mismatch

7.3 Resume Behavior

On upload, validate entire file

Jump to first incomplete sentence

Display banner if invalid sentences exist

Allow navigation, but block Finish

8. Timestamp & Timing Semantics

started_at: first time sentence is displayed

committed_at: overwritten on every “Next”

edit_count: increments on re-commit

Time per sentence = committed_at − started_at

9. Completion Summary (Blind)

Displayed only when all sentences are valid:

DA mean / median / std / min / max (across all translations)

Aggregate bucket distribution

Time-per-sentence statistics

No per-system statistics are shown.

10. Error Handling
Hard Fail

Hash mismatch

Malformed XLSX schema

Missing required columns

Incorrect number of translations

Soft Fail

Invalid sentences → banner + Finish blocked

11. Implementation Notes

All logic driven by run configuration

No hard-coded values for N, DA scale, or labels

Stateless server

All persistence via XLSX download

12. Extensibility

This design supports:

Alternative DA scales

Different bucket schemes

Additional metadata

Multi-rater workflows (future)