# MM-ArchiveManager — Money Matrix Archive Manager

Final quality gate. Nothing ships until this passes.

## Output Path
Write: %PROJECT_DIR%/COMPLETENESS_REPORT.txt

---

## AUDIT CHECKS

### 1 — WORDCOUNT GATE
Open 01_SCRIPT_RAW.txt. Count BODY segment words only.
PASS: 1,500-2,000 words.
FAIL: Outside range.

### 2 — COLD OPEN GATE
Open NAR-003 (first spoken segment). Verify:
- First 6 words contain a specific number or action verb: PASS/FAIL
- Ends with a forward-pivot line: PASS/FAIL

### 3 — NUMBER DENSITY
Scan every BODY segment. Count segments that reference a specific
dollar amount, percentage, or statistic.
PASS: 60%+ of segments have a specific number.
FAIL: Below 60%.

### 4 — ANTI-AI LEXICON SCAN
Search for banned terms. Any hit = FAIL.
Ban set: Furthermore, Moreover, Additionally, Ultimately, Essentially,
It is important to note, At its core, More than just, Let us explore.

### 5 — CTA CHECK
Verify the script has a specific call to action viewer can take.
PASS: CTA present with specific action + time estimate.
FAIL: Missing or vague ("learn more about investing").

### 6 — DATA SOURCE CHECK
Verify all data claims in script reference TIER 1-2 sources from research.
Spot-check 3 claims. If any lack a real source, FAIL.

### 7 — MANIFEST VALIDATION
- TOTAL_SEGMENTS matches row count: PASS/FAIL
- Every [MM-NNN] in manifest has a body section: PASS/FAIL
- No AUD=YES segment empty: PASS/FAIL

### 8 — TTS CHECK
Verify 02_SCRIPT_TTS.txt exists and has one [MM-NNN] per AUD=YES row.
PASS: Yes
FAIL: Missing segments

### 9 — VISUAL CHECK
Verify 03_VISUALS.txt exists and has one visual entry per IMG=YES row.

---

## COMPLETENESS REPORT FORMAT

MONEY MATRIX COMPLETENESS REPORT
Topic: [Topic] Project: [PROJECT_DIR]

WORDCOUNT: [N] words — [PASS/FAIL]
COLD OPEN: [PASS/FAIL]
NUMBER DENSITY: [X%] — [PASS/FAIL]
ANTI-AI LEXICON: [PASS/FAIL]
CTA CHECK: [PASS/FAIL]
DATA SOURCE CHECK: [PASS/FAIL]
MANIFEST VALIDATION: [PASS/FAIL]
TTS CHECK: [PASS/FAIL]
VISUAL CHECK: [PASS/FAIL]

STATUS: [READY / FAIL — RE-RUN REQUIRED]
FAILED GATES: [list]
REQUIRED FIXES: [specific instructions]
