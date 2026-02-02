instructions_md = (
"""
# Machine Translation Evaluation — Rater Instructions

Thank you for participating in this machine-translation evaluation study. Please read these instructions before you start rating.

---

## What you are doing in this app

For each **source sentence**, you will rate **multiple candidate translations**. The app **blinds** the candidate translations, such that raters should not know which system produced which translation.

Your ratings are recorded in two parts for **each translation**:

1. **Bucket** (qualitative grouping): Best / Good / OK / Poor  
2. **Direct Assessment (DA)** (numeric score): an integer score **within the allowed range for that bucket**

When you click **Next**, the current sentence is **committed** (saved) into the workbook.

---

## 1) Uploading and using the `.xlsx` file (IMPORTANT!)

1. Open the app and use the **Upload evaluation XLSX** uploader.
2. Upload the provided .xlsx file **exactly as received**.
   - Do **not** open and resave it in Excel (that can break integrity checks).
   - You may rename the file, but do not change any values directly.
3. The app loads your workbook in memory and starts you at the **first incomplete sentence**.

**Important:** The app does **not** store your ratings anywhere else.  
Your work only exists in the **downloaded** .xlsx checkpoints you save to your computer.

---

## 2) Navigation and progress

At the top of the screen you will see:

- **Sentence X / N** progress
- Optional **Jump to sentence** box (type a sentence number to go directly to that sentence)
- **First incomplete** (takes you to the earliest unfinished sentence)
- **Next invalid** (takes you to the next sentence with a committed-but-invalid evaluation, if any)
- **Download checkpoint** (saves your current progress to a file)

A banner will also show status:
- ✅ All complete and valid → you may Finish
- 📝 Some incomplete → Finish is blocked until everything is committed
- ⚠️ Some invalid committed rows → Finish is blocked until fixed

---

## 3) How to rate a sentence

For each translation card:

### A) Choose a bucket (required)
Select one:

- Best: optimal translation, demonstrating highest fluency and adequacy
- Good: high-quality translation with minor issues, not detracting from overall meaning
- OK: acceptable translation with noticeable issues, but still understandable
- Poor: inadequate translation with significant problems affecting comprehension

**The list WILL re-order immediately after changing a bucket.** This is expected: the UI groups translations by bucket to make comparison easier.

### B) Set the Direct Assessment (DA) score (required)
Enter the DA score using the slider.

**Important behavior:**
- The DA slider is **disabled until you choose a bucket**.
- The **slider range depends on the chosen bucket** (a restricted “quartile” range).
  Example: The acceptable DA range for 'Poor' is 1 - 7, while the range for 'Best' is 21 - 28.
- DA must be an **integer** within the allowed range shown on the slider.

If you change the bucket after setting DA, the app will **remap** your DA into the new bucket's allowed range to keep the relative value of your choice.

### C) Optional comment
You may add a sentence-level comment in the **Comment (optional)** box.

---

## 4) Saving your work (PLEASE do this often)

- Click **Download checkpoint** regularly (top or bottom of the page).
- The “Save (checkpoint)” button does **not** download a file — it only prepares a checkpoint-xlsx file for download.  
  **To actually save your work, you MUST download the checkpoint file.**

Best practice:
- Download a checkpoint every few sentences.
- Keep multiple backups (e.g., `checkpoint_050.xlsx`, `checkpoint_100.xlsx`).
  - Note that a timestamp is appended to the end of the downloaded filename for your convenience.

If you close the browser or refresh the page **without downloading**, your progress WILL be lost.

---

## 5) Committing a sentence (the Next button)

Click **Next** when you are done with the current sentence.

- Next will **validate** that every translation has:
  - a bucket selected, and
  - a DA score within the allowed range
- If something is missing or out of range, you will see an error and cannot proceed.
- If valid, the app writes your ratings into the workbook, timestamps the commit, and moves to the next sentence.

---

## 6) Randomization and Consistency Checks (important)

- The position of translations is **not tied to any system**.
- You may see the order change as you bucket/score translations.
- Do not try to track or guess systems by position. Rate what you see.
- The provided .xlsx file has built-in integrity checks which verifies that the .xlsx file is internally consistent.  
  If you see an integrity error, **stop and contact the research team**.

---

## 7) Finishing

The 'Finish' button is only enabled when **all sentences are complete and valid**.
When you click **Finish**, the app shows a blind, aggregate completion summary.  
**You must download the final checkpoint XLSX and send it to the research team.**
**Your evaluation is not complete until you download the final .xlsx file and send it to the research team.**

---

## 8) If something looks wrong

Stop and contact the research team if you see:

- Upload / workbook errors
- Integrity / hash mismatch errors
- App crashes or unusual behavior that could corrupt the file

Do not “repair” the workbook by opening/resaving it in Excel.

---

Thank you for your careful work — consistency matters more than speed.
""".strip()
)
