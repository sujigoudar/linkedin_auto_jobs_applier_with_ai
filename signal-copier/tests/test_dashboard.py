from fastapi.testclient import TestClient

import app.main as main_module


def test_dashboard_route_serves_html():
    client = TestClient(main_module.app)
    with client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    # it's the actual polling dashboard, not a placeholder page
    assert "/positions" in body
    assert "/brokers" in body
    assert "/providers" in body
    assert "/signals" in body
    assert "/orders" in body
