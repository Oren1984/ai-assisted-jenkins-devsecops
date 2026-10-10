from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready():
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_quote_returns_total():
    response = client.post("/quote", json={"sku": "widget", "quantity": 2})
    assert response.status_code == 200
    assert response.json()["total_cents"] == 2000


def test_quote_with_discount_code():
    response = client.post(
        "/quote", json={"sku": "gadget", "quantity": 1, "discount_code": "WELCOME10"}
    )
    assert response.status_code == 200
    assert response.json()["total_cents"] == 2250


def test_quote_rejects_invalid_quantity():
    response = client.post("/quote", json={"sku": "widget", "quantity": 0})
    assert response.status_code == 422


def test_quote_rejects_malformed_body():
    response = client.post("/quote", json={"quantity": "many"})
    assert response.status_code == 422


def test_quote_unknown_sku():
    response = client.post("/quote", json={"sku": "sprocket", "quantity": 1})
    assert response.status_code == 404


def test_quote_unknown_discount_code():
    response = client.post(
        "/quote", json={"sku": "widget", "quantity": 1, "discount_code": "NOPE"}
    )
    assert response.status_code == 400
