"""Importing this package registers every check function with the engine."""

from app.rules.checks import (
    billing,
    completeness,
    consistency,
    forensics,
    judgement,
    medical,
    policy,
    registry,
)

__all__ = ["billing", "completeness", "consistency", "forensics", "judgement", "medical", "policy", "registry"]
