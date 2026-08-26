"""M2 static UI contracts for the Goal Manager plugin."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.goal_manager.server import create_app
from voidcube.systems.supervisor.ui_routes import mount_plugin_web_routes


WEB_ROOT = Path("plugins/goal_manager/web/dist")


def test_goal_manager_static_bundle_is_self_contained_and_interactive():
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")
    javascript = (WEB_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'src="./app.js"' in html
    assert 'href="./styles.css"' in html
    assert "id=\"radial-svg\"" in html
    assert "id=\"overview-svg\"" in html
    assert "focusNode" in javascript
    assert "navigateBack" in javascript
    assert "renderOverview" in javascript
    assert "EventSource" in javascript
    assert 'method: "PATCH"' in javascript
    assert "data-criterion-index" in javascript
    assert "computeOverviewLayout" in javascript
    assert "computeOverviewLayoutInWorker" in javascript
    assert "nodes.length > 500" in javascript
    assert "new Worker" in javascript
    assert "https://cdn." not in javascript.lower()
    assert '<script src="./app.js"></script>' in html
    assert "@media (max-width: 560px)" in css
    assert ".status-blocked" in css


def test_goal_manager_ui_is_mounted_by_supervisor():
    app = FastAPI()
    mount_plugin_web_routes(app)
    with TestClient(app) as client:
        page = client.get("/ui/goal-manager/")
        stylesheet = client.get("/ui/goal-manager/styles.css")
        script = client.get("/ui/goal-manager/app.js")

    assert page.status_code == 200
    assert "目标管理" in page.text
    assert stylesheet.status_code == 200
    assert "radial-wrap" in stylesheet.text
    assert script.status_code == 200
    assert "loadOverview" in script.text


def test_goal_service_allows_supervisor_origin_for_browser_calls(tmp_path):
    app = create_app({"db_path": str(tmp_path / "goals.db")})
    with TestClient(app) as client:
        response = client.options(
            "/api/goals/projects",
            headers={
                "Origin": "http://127.0.0.1:6002",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:6002"
