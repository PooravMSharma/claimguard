from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root():
    response = client.get("/api")
    assert response.status_code == 200
    assert response.json()["app"] == "ClaimGuard"


def test_health_shape():
    # Services may be down in some test setups; the endpoint must still answer.
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert set(body["checks"]) == {"database", "queue", "storage", "worker"}
