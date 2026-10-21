# Step 10: AI checks

## What was added

| Check | How it works | Raises |
|---|---|---|
| F-02 amount in a different font | Fonts of the amounts on digital bill pages (pypdfium2). Scans: not evaluable | amber |
| F-04 lab report printed differently | Letterhead font of each in-house lab report vs the lab's other reports or the hospital's documents. Scans: column order | amber |
| M-01 admission needed | AI, clinical call | amber, medical review |
| M-02 test relevant | AI, clinical call. Baseline tests (CBC, RFT, LFT, ECG, chest X-ray...) are never asked | amber, medical review |
| M-04 ICU justified | AI, asked only when ICU is billed or charted | amber, medical review |
| M-06 severity supported | AI, asked only when the diagnosis claims severity (severe, shock, thrombocytopenia...) | amber, medical review |
| X-08 diagnosis change explained | AI, asked only when the pre-auth and final diagnosis wording differ | amber |
| B-11 upcoding | AI, asked only when the billed procedure name differs from the OT notes / discharge summary | amber |
| C-02 (AI part) | Vision model looks at the signature area of a scanned discharge summary | amber |
| X-06 (AI part) | "Same procedure in other words" turns the red rule flag into amber (still flagged) | - |
| M-03 (AI part) | A documented reason for a repeat turns the flag into info | - |

## Rules the AI reviewer follows

- The claim becomes numbered facts (F1, F2...), each tied to its page and box.
- Fixed questions, fixed verdicts, always with "unclear". The model must cite fact ids.
- A finding only on an explicit negative verdict with real citations. "unclear", no answer or invented ids
  raise nothing (the check says "not evaluable").
- Questions are gated by the data, so most genuine claims need one call.
- Answers are cached in `ai_check_cache` (migration 0008); every call is logged in `ai_calls` as `check:*`.
- No model, or a fatal error (Ollama not running): the AI checks report `pending_ai`; the rules still run.
- Settings: `AI_CHECKS=true|false` (default true). `run-checks --no-ai` skips the reviewer.

## Measured (offline, without a model)

- Clean claims (300): rule checks still 100% recall and precision, 0 false reds. F-02 4/4, F-04 4/4.
- Every seeded AI issue on clean claims reaches its question; genuine claims need exactly 1 call.
- Prompts are 3–8k characters (about 1–2k tokens), well inside the 24k context.
- The AI's own accuracy can only be measured with the model running (see CLAUDE.md commands).
