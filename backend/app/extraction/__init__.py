"""Step 8: turn page text into the structured claim record.

Rules read the labelled fields and tables first (exact, free, with word positions).
The AI reader (ai_extract.py) fills gaps and reads poor scans from the page image.
Every value keeps its page and box so findings can point at the evidence.
"""
