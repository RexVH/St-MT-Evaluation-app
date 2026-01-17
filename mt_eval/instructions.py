instructions_md = (
"""
# Machine Translation Evaluation — Rater Instructions

Thank you for participating in this machine-translation evaluation study. Your careful, consistent ratings are essential to the quality of the research. Please read all instructions before beginning.

---

## 1. Uploading and Using the `.xlsx` File

1. Start the app and locate the file uploader at the top of the page.
2. Upload the provided `.xlsx` evaluation file **exactly as received**.
   - Do **not** rename the file.
   - Do **not** open and resave the file in Excel or another spreadsheet editor before uploading.
3. Once uploaded, the app will validate the file and load the first source sentence and its translations.
4. All ratings you provide are written **back into this same `.xlsx` file**.
5. Use the **Download / Save checkpoint** button frequently to save the updated file to your computer.

**Important:** The exported `.xlsx` file is the **only record** of your evaluations. The app does not store your ratings anywhere else.

---

## 2. Evaluation Flow (How to Rate)

Each source sentence is evaluated in **two required stages**, performed in order.

### Step 1: Bucket Ratings (Qualitative Grouping)

For each translation, first assign it to one of the following buckets:

- **Best**
- **Good**
- **OK**
- **Poor**

These buckets capture your *relative quality judgment* across translations for the same source sentence.

**Expected behavior:**
- Translations are **randomly ordered** each time a new sentence is loaded.
- When you change a translation’s bucket, the **display order may immediately change**.
  - This is expected.
  - The interface dynamically groups translations by bucket to support comparison.

---

### Step 2: Direct Assessment (Numeric Scoring)

After bucket assignment, provide a **numeric Direct Assessment (DA) score** (e.g., 0–100) for each translation.

- Numeric scores should **refine** your bucket judgment.
- Scores should generally be:
  - Higher within *Best*
  - Lower within *Poor*
- You may revise bucket assignments if your numeric judgments change.

---

## 3. Randomization of Translations

- For **every source sentence**, translations are displayed in a **new random order**.
- There is **no fixed position** for any system.
- Do not attempt to track systems by position.

This randomization is intentional and critical for reducing evaluation bias.

---

## 4. File Integrity and Hash Checking

The provided `.xlsx` file includes an internal **hash-based integrity check**.

- The app verifies this hash automatically when the file is uploaded.
- If you encounter a **hash mismatch or hash error**:
  - Stop rating immediately.
  - Do **not** attempt to repair or resave the file.
  - Contact the research team for assistance.

Hash errors typically occur if the file was:
- Opened and resaved in Excel
- Modified outside the evaluation app
- Partially corrupted during transfer

This safeguard protects the validity of the research data.

---

## 5. Saving Your Work (Critical)

- **Save early and save often.**
- Browser sessions are temporary.
- Closing the browser, refreshing the page, or losing connection will **not** preserve your work unless you download the `.xlsx` file.

**Best practice:**
- Download a checkpoint every few sentences.
- Keep multiple backups (e.g., `eval_checkpoint_050.xlsx`, `eval_checkpoint_100.xlsx`).

The downloaded `.xlsx` file is the **only authoritative record** of your evaluations.

---

## 6. Additional Guidance for High-Quality Ratings

- Evaluate each sentence **independently**.
- Do not assume system quality is consistent across sentences.
- Focus on:
  - Meaning preservation
  - Fluency and grammatical correctness
  - Naturalness and appropriateness
- Avoid over-weighting minor stylistic preferences unless they affect meaning.
- Take breaks as needed — fatigue negatively impacts rating quality.

---

## 7. Questions or Technical Issues

If you encounter:
- Hash or upload errors
- App crashes or freezes
- Unexpected behavior
- Navigation or save issues

Please contact the research team **before continuing**. Do not attempt workarounds that could compromise the data.

---

Thank you for your time and careful work. Your evaluations directly support the quality, reliability, and impact of this research.
""".strip()
)