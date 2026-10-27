"""Claim processing pipeline, run by the worker. Steps are added as the build progresses:
pages (Step 6) -> OCR and page types (Step 7) -> extraction (Step 8) -> checks (Steps 9-10) -> scoring (Step 11).
"""
