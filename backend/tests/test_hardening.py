"""Step 14: production settings guard, response headers, safe errors, abandoned claims, claim view trail."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.hardening import external_ai_blocked, production_problems
from app.core.middleware import install
from app.core.security import create_access_token
from app.models.claim import Claim
from app.models.enums import ClaimStage, ClaimStatus, RoleName
from app.models.user import AuditLog
from app.services.recovery import NEVER_STARTED, STOPPED, recover_stuck
from app.services.users import create_user

GOOD = dict(app_env="production", secret_key="x" * 48, cors_origins=["https://claims.example.in"], bcrypt_rounds=12,
            access_token_minutes=480, ai_provider="", allow_external_ai=False,
            database_url="postgresql+psycopg://claimguard:Str0ng-db-Passw0rd@postgres:5432/claimguard")


def settings(**changes) -> Settings:
    return Settings(_env_file=None, **{**GOOD, **changes})  # type: ignore[call-arg]


def test_development_allows_everything():
    assert production_problems(Settings(_env_file=None, app_env="development")) == []  # type: ignore[call-arg]


def test_a_safe_production_setup_passes():
    assert production_problems(settings()) == []


@pytest.mark.parametrize("change, words", [
    (dict(secret_key="change-me"), "SECRET_KEY"),
    (dict(database_url="postgresql+psycopg://claimguard:claimguard@postgres:5432/claimguard"), "database password"),
    (dict(cors_origins=["*"]), "CORS_ORIGINS"),
    (dict(cors_origins=["http://claims.example.in"]), "https://"),
    (dict(ai_provider="anthropic", ai_api_key="k"), "ALLOW_EXTERNAL_AI"),
    (dict(bcrypt_rounds=4), "BCRYPT_ROUNDS"),
])
def test_unsafe_production_settings_are_refused(change, words):
    problems = production_problems(settings(**change))
    assert any(words in p for p in problems), problems


def test_external_ai_needs_explicit_approval_outside_development():
    assert external_ai_blocked(settings(ai_provider="anthropic"))
    assert not external_ai_blocked(settings(ai_provider="anthropic", allow_external_ai=True))
    assert not external_ai_blocked(settings(ai_provider="ollama"))
    assert not external_ai_blocked(Settings(_env_file=None, app_env="development", ai_provider="anthropic"))  # type: ignore[call-arg]


def test_ai_client_gives_no_external_provider_when_blocked(monkeypatch):
    from app.ai import client as ai_client

    monkeypatch.setattr(ai_client, "get_settings", lambda: settings(ai_provider="anthropic", ai_api_key="k"))
    assert ai_client._configured() is None


# ---------------------------------------------------------------- responses

def test_api_responses_carry_security_headers(client):
    res = client.get("/api")
    h = res.headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert h["cache-control"] == "no-store" and "default-src 'none'" in h["content-security-policy"]
    assert len(h["x-request-id"]) == 32
    assert client.get("/api", headers={"X-Request-ID": "trace-1234abcd"}).headers["x-request-id"] == "trace-1234abcd"
    assert client.get("/api", headers={"X-Request-ID": "bad id\n"}).headers["x-request-id"] != "bad id\n"


def test_unexpected_errors_hide_details_and_give_a_reference():
    app = FastAPI()
    install(app, production=True)

    @app.get("/boom")
    def boom():
        raise RuntimeError("database password is hunter2")

    res = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert res.status_code == 500
    assert "hunter2" not in res.text and res.headers["x-request-id"] in res.json()["detail"]


# ---------------------------------------------------------------- abandoned claims

def claim(db, status, *, started=None, updated=None) -> Claim:
    c = Claim(claim_number=f"CG-T-{uuid.uuid4().hex[:8]}", stage=ClaimStage.REIMBURSEMENT, status=status,
              processing_started_at=started)
    db.add(c)
    db.flush()
    if updated:
        c.updated_at = updated
        db.flush()
    return c


def test_claims_left_by_a_stopped_worker_are_marked_failed(db):
    now = datetime.now(timezone.utc)
    dead = claim(db, ClaimStatus.PROCESSING, started=now - timedelta(hours=4))
    busy = claim(db, ClaimStatus.PROCESSING, started=now - timedelta(minutes=30))
    lost = claim(db, ClaimStatus.RECEIVED, updated=now - timedelta(minutes=20))
    waiting = claim(db, ClaimStatus.RECEIVED, updated=now - timedelta(hours=6))
    fresh = claim(db, ClaimStatus.RECEIVED, updated=now - timedelta(minutes=2))
    done = recover_stuck(db, now, queued={str(waiting.id)})
    assert set(done) == {dead.claim_number, lost.claim_number}
    assert (dead.status, dead.processing_error) == (ClaimStatus.FAILED, STOPPED)
    assert (lost.status, lost.processing_error) == (ClaimStatus.FAILED, NEVER_STARTED)
    assert busy.status == ClaimStatus.PROCESSING
    assert waiting.status == fresh.status == ClaimStatus.RECEIVED
    assert db.scalar(select(func.count()).where(AuditLog.action == "claim.processing_abandoned")) == 2


def test_received_claims_are_left_alone_when_the_queue_cannot_be_read(db):
    now = datetime.now(timezone.utc)
    lost = claim(db, ClaimStatus.RECEIVED, updated=now - timedelta(hours=1))
    assert recover_stuck(db, now, check_queue=False) == []
    assert lost.status == ClaimStatus.RECEIVED


# ---------------------------------------------------------------- who opened a claim

def test_opening_a_claim_is_recorded_once_per_half_hour(client, db):
    user = create_user(db, email="viewer@hard.claimguard.in", full_name="Viewer", role=RoleName.ADJUDICATOR,
                       password="Claims#2026secure")
    token, _ = create_access_token(user.id)
    c = claim(db, ClaimStatus.READY_FOR_REVIEW)
    db.commit()
    headers = {"Authorization": f"Bearer {token}"}
    for _ in range(3):
        assert client.get(f"/api/claims/{c.id}", headers=headers).status_code == 200
    views = db.scalars(select(AuditLog).where(AuditLog.action == "claim.viewed",
                                              AuditLog.entity_id == str(c.id))).all()
    assert len(views) == 1 and views[0].user_id == user.id
