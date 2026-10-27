"""Synthetic claim files with known answers, for end-to-end testing.

No public dataset has real Indian claim documents with fraud labels, so we
generate them: each claim is a full PDF file (claim form, discharge summary,
bills, reports...) plus a ground-truth sheet listing every issue seeded into it.
Everything here is fictional: people, hospitals, doctors, IDs and policies.
"""
