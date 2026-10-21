"""Starting values for the rules table: status, weight and thresholds per check.

These are only the defaults written when a rule is first created. After that the rules table is the source of
truth, so thresholds can be tuned (Step 12) without a release. Every threshold a check uses lives here, never
inside the check.
"""

from app.rules.catalogue import CHECKS

WEIGHT_BY_SEVERITY = {"red": 30, "amber": 15, "info": 5}

DEFAULT_CONFIG: dict[str, dict] = {
    "C-01": {
        "always": ["claim_form", "discharge_summary", "final_bill", "policy_copy", "id_proof"],
        "cashless_stages": ["final_cashless", "preauth", "enhancement"],
        "cashless": ["preauth_form"],
        "surgical": ["ot_notes"],
        "when_pharmacy_billed": ["pharmacy_bill"],
    },
    "C-05": {"accident_words": ["accident", "rta", "road traffic", "fall from", "assault", "injury", "fracture",
                                "burn", "poisoning", "dog bite", "snake bite"]},
    "F-01": {"editing_software": ["photoshop", "gimp", "canva", "paint.net", "pixelmator", "affinity photo",
                                  "pdfescape", "sejda", "pdf editor", "foxit phantompdf", "inkscape", "illustrator"],
             "grace_hours": 24},
    "F-03": {"max_hash_distance": 6},
    "X-01": {"name_word_similarity": 0.75, "name_word_similarity_scan": 0.6, "age_tolerance_years": 1},
    "X-02": {"tolerance_hours": 1},
    "X-03": {"pre_admission_days_if_no_policy_term": 30, "post_discharge_days": 0},
    "X-09": {"name_similarity": 0.8},
    "M-03": {"repeat_window_hours": 24, "kinds": ["imaging"]},
    "M-05": {"extra_days_allowed": 2},
    "B-01": {"tolerance_rs": 1.0},
    "B-02": {"tolerance_rs": 1.0},
    "B-03": {"over_benchmark_pct": 25, "skip_categories": ["room", "icu", "pharmacy", "implant", "package", "other",
                                                           "non_medical"]},
    "B-07": {"per_day": {"syringe": 8, "cannula": 2, "infusion set": 2, "gloves": 10, "mask": 6}},
    "B-09": {"second_procedure_pct": 50, "further_procedure_pct": 25, "full_rate_share": 0.9},
    "B-10": {"tolerance_rs": 0.01},
    "P-04": {"days_after_inception": 90},
    "P-06": {"tolerance_rs": 1.0},
    "R-01": {"name_similarity": 0.8},
    "R-02": {"name_similarity": 0.75},
}


def default_config(rule_id: str) -> dict:
    return dict(DEFAULT_CONFIG.get(rule_id, {}))


def default_status(rule_id: str) -> str:
    """Medical-necessity rules start as drafts: amber at most until a clinician has reviewed them."""
    return "draft" if CHECKS[rule_id].layer == "medical" else "active"


def default_weight(rule_id: str) -> int:
    return WEIGHT_BY_SEVERITY[CHECKS[rule_id].max_severity]
