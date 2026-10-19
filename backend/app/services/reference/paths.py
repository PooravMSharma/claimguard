from pathlib import Path

REFERENCE_DIR = Path(__file__).resolve().parents[3] / "reference_data"
NON_PAYABLES_FILE = REFERENCE_DIR / "irdai_non_payables.csv"
POLICY_TERMS_FILE = REFERENCE_DIR / "policy_terms_standard.json"
CGHS_ADJUSTMENTS_FILE = REFERENCE_DIR / "cghs_2025_adjustments.json"
ICD10_FILE = REFERENCE_DIR / "icd10cm.csv.gz"
