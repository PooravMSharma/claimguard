import pytest
from sqlalchemy import select

from app.api.deps import COOKIE_NAME
from app.cli import create_admin
from app.core.permissions import ROLE_PERMISSIONS, Permission
from app.core.security import create_access_token, verify_password
from app.models.enums import RoleName
from app.models.user import AuditLog, User
from app.services.queue import redis_conn
from app.services.users import create_user

PASSWORD = "Claims#2026secure"
DOMAIN = "claimguard.in"


@pytest.fixture(autouse=True)
def clear_lockouts():
    def wipe():
        try:
            for key in redis_conn.scan_iter(f"login_fail:*@{DOMAIN}"):
                redis_conn.delete(key)
        except Exception:
            pass

    wipe()
    yield
    wipe()


def make_user(db, role: RoleName, name: str | None = None) -> User:
    email = f"{name or role.value}@{DOMAIN}"
    return create_user(db, email=email, full_name=f"Test {role.value}", role=role, password=PASSWORD)


def login(client, email: str, password: str = PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def actions(db) -> list[str]:
    return list(db.scalars(select(AuditLog.action).order_by(AuditLog.id)))


# ---------- login ----------

def test_login_sets_secure_cookie_and_me_works(client, db):
    user = make_user(db, RoleName.ADJUDICATOR)
    res = login(client, user.email)
    assert res.status_code == 200
    cookie = res.headers["set-cookie"].lower()
    assert COOKIE_NAME in cookie and "httponly" in cookie and "samesite=strict" in cookie

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == user.email and body["role"] == "adjudicator"
    assert "claims.review" in body["permissions"] and "users.manage" not in body["permissions"]
    assert db.get(User, user.id).last_login_at is not None


def test_email_is_case_insensitive(client, db):
    user = make_user(db, RoleName.ADJUDICATOR)
    assert login(client, user.email.upper()).status_code == 200


def test_wrong_password_and_unknown_email_give_same_answer(client, db):
    user = make_user(db, RoleName.ADJUDICATOR)
    wrong = login(client, user.email, "Wrong#password1")
    unknown = login(client, f"nobody@{DOMAIN}")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"] == "Incorrect email or password"
    assert actions(db).count("login.failed") == 2


def test_inactive_user_cannot_log_in(client, db):
    user = make_user(db, RoleName.ADJUDICATOR)
    user.is_active = False
    db.flush()
    assert login(client, user.email).status_code == 401


def test_lockout_after_repeated_failures(client, db):
    user = make_user(db, RoleName.ADJUDICATOR)
    for _ in range(5):
        assert login(client, user.email, "Wrong#password1").status_code == 401
    locked = login(client, user.email)  # correct password, still locked
    assert locked.status_code == 429
    assert "login.locked" in actions(db)


def test_successful_login_resets_failure_count(client, db):
    user = make_user(db, RoleName.ADJUDICATOR)
    for _ in range(4):
        login(client, user.email, "Wrong#password1")
    assert login(client, user.email).status_code == 200
    for _ in range(4):
        login(client, user.email, "Wrong#password1")
    assert login(client, user.email).status_code == 200


def test_logout_clears_session(client, db):
    user = make_user(db, RoleName.ADJUDICATOR)
    login(client, user.email)
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    assert "logout" in actions(db)


# ---------- tokens ----------

def test_no_token_is_rejected(client):
    assert client.get("/api/auth/me").status_code == 401


def test_tampered_token_is_rejected(client, db):
    user = make_user(db, RoleName.ADJUDICATOR)
    token, _ = create_access_token(user.id)
    bad = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {bad}"}).status_code == 401


def test_bearer_token_works_for_api_clients(client, db):
    user = make_user(db, RoleName.ADJUDICATOR)
    token, _ = create_access_token(user.id)
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_deactivated_user_loses_access_immediately(client, db):
    user = make_user(db, RoleName.ADJUDICATOR)
    login(client, user.email)
    user.is_active = False
    db.flush()
    assert client.get("/api/auth/me").status_code == 401


# ---------- permissions ----------

@pytest.mark.parametrize("role", list(RoleName))
def test_users_page_only_for_admin(client, db, role):
    user = make_user(db, role)
    login(client, user.email)
    expected = 200 if role == RoleName.ADMIN else 403
    assert client.get("/api/users").status_code == expected


@pytest.mark.parametrize("role", list(RoleName))
def test_audit_log_only_for_admin_and_team_lead(client, db, role):
    user = make_user(db, role)
    login(client, user.email)
    expected = 200 if role in (RoleName.ADMIN, RoleName.TEAM_LEAD) else 403
    assert client.get("/api/audit").status_code == expected


def test_every_role_has_permissions_defined():
    assert set(ROLE_PERMISSIONS) == set(RoleName)
    for perms in ROLE_PERMISSIONS.values():
        assert Permission.CLAIMS_VIEW in perms


# ---------- user management ----------

def test_admin_creates_user_who_can_then_log_in(client, db):
    admin = make_user(db, RoleName.ADMIN)
    login(client, admin.email)
    res = client.post("/api/users", json={
        "email": f"New.Person@{DOMAIN}", "full_name": "New Person", "role": "senior_reviewer", "password": PASSWORD,
    })
    assert res.status_code == 201
    assert res.json()["email"] == f"new.person@{DOMAIN}"

    created = db.scalar(select(User).where(User.email == f"new.person@{DOMAIN}"))
    assert created.password_hash != PASSWORD and verify_password(PASSWORD, created.password_hash)

    client.post("/api/auth/logout")
    assert login(client, f"new.person@{DOMAIN}").json()["role"] == "senior_reviewer"
    entry = db.scalar(select(AuditLog).where(AuditLog.action == "user.created"))
    assert entry.user_id == admin.id and "password" not in str(entry.details).lower()


@pytest.mark.parametrize("password", ["short1!", "onlyletterslong", "1234567890123"])
def test_weak_passwords_rejected(client, db, password):
    admin = make_user(db, RoleName.ADMIN)
    login(client, admin.email)
    res = client.post("/api/users", json={
        "email": f"weak@{DOMAIN}", "full_name": "Weak", "role": "adjudicator", "password": password,
    })
    assert res.status_code == 422


def test_duplicate_email_rejected(client, db):
    admin = make_user(db, RoleName.ADMIN)
    make_user(db, RoleName.ADJUDICATOR)
    login(client, admin.email)
    res = client.post("/api/users", json={
        "email": f"adjudicator@{DOMAIN}", "full_name": "Dup", "role": "adjudicator", "password": PASSWORD,
    })
    assert res.status_code == 409


def test_role_change_applies_immediately_and_is_audited(client, db):
    admin = make_user(db, RoleName.ADMIN)
    target = make_user(db, RoleName.ADJUDICATOR)
    login(client, admin.email)
    res = client.patch(f"/api/users/{target.id}", json={"role": "team_lead"})
    assert res.status_code == 200 and res.json()["role"] == "team_lead"
    entry = db.scalar(select(AuditLog).where(AuditLog.action == "user.updated"))
    assert entry.details["role"] == {"from": "adjudicator", "to": "team_lead"}


def test_admin_cannot_remove_own_admin_access(client, db):
    admin = make_user(db, RoleName.ADMIN)
    login(client, admin.email)
    assert client.patch(f"/api/users/{admin.id}", json={"is_active": False}).status_code == 400
    assert client.patch(f"/api/users/{admin.id}", json={"role": "adjudicator"}).status_code == 400


def test_password_reset(client, db):
    admin = make_user(db, RoleName.ADMIN)
    target = make_user(db, RoleName.ADJUDICATOR)
    login(client, admin.email)
    new_password = "Fresh#Pass2026"
    assert client.post(f"/api/users/{target.id}/password", json={"password": new_password}).status_code == 204
    client.post("/api/auth/logout")
    assert login(client, target.email).status_code == 401
    assert login(client, target.email, new_password).status_code == 200


# ---------- audit log API ----------

def test_audit_log_lists_and_filters(client, db):
    lead = make_user(db, RoleName.TEAM_LEAD)
    login(client, lead.email, "Wrong#password1")
    login(client, lead.email)
    page = client.get("/api/audit", params={"action": "login"}).json()
    found = {item["action"] for item in page["items"]}
    assert {"login.failed", "login.success"} <= found
    assert page["total"] >= 2
    assert all(item["action"].startswith("login") for item in page["items"])


# ---------- CLI ----------

def test_cli_create_admin(db, monkeypatch):
    from app import cli

    class _SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return db

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(cli, "SessionLocal", _SessionFactory())
    create_admin(f"first.admin@{DOMAIN}", "First Admin", PASSWORD)
    user = db.scalar(select(User).where(User.email == f"first.admin@{DOMAIN}"))
    assert user.role.name == RoleName.ADMIN
