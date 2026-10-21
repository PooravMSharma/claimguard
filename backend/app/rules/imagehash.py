"""Comparing page image hashes (F-03).

The hash itself is the 64-bit difference hash the pipeline stores for every page (app.pipeline.pages.dhash):
re-scans, compression and small edits change only a few bits; different page layouts differ in many.
"""


def distance(a: str, b: str) -> int:
    """Number of differing bits between two hex hashes of the same length."""
    if not a or not b or len(a) != len(b):
        return 1 << 16
    return bin(int(a, 16) ^ int(b, 16)).count("1")
