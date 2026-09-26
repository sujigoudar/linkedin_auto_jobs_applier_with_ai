"""C12/C13: Chart.js and Tabulator are vendored locally (no CDN) and
served from /static/vendor -- the dashboard's <script>/<link> tags
reference these exact paths."""
from fastapi.testclient import TestClient

import app.main as main_module


def test_vendor_files_are_served():
    client = TestClient(main_module.app)
    for path in ("chart.umd.min.js", "tabulator.min.js", "tabulator.min.css"):
        response = client.get(f"/static/vendor/{path}")
        assert response.status_code == 200, path
        assert len(response.content) > 1000, path


def test_dashboard_references_the_vendored_files_not_a_cdn():
    client = TestClient(main_module.app)
    html = client.get("/").text
    assert "/static/vendor/chart.umd.min.js" in html
    assert "/static/vendor/tabulator.min.js" in html
    assert "/static/vendor/tabulator.min.css" in html
    assert "cdn." not in html
