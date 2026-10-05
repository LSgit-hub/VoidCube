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
    assert 'id="review-token-input"' in html
    assert "exchangeReviewSession" in javascript
    assert "Goal Service 返回了无效响应" in javascript
    assert "AbortController" in javascript
    assert "Goal Service 请求超时，请稍后重试" in javascript
    assert 'networkError.code = "network_error"' in javascript
    assert 'response.ok && (!payload || typeof payload !== "object" || Array.isArray(payload))' in javascript
    assert "reviewSessionError" in javascript
    assert "if (!ready) throw reviewSessionError()" in javascript
    assert 'message === "审核会话不可用，请重新输入审核凭证"' in javascript
    assert 'reviewInput.focus()' in javascript
    assert 'error.status === 409' in javascript
    assert "目标已被更新，已刷新当前数据，请重新检查后重试" in javascript
    assert 'href="./styles.css"' in html
    assert "id=\"radial-svg\"" in html
    assert "id=\"overview-svg\"" in html
    assert "focusNode" in javascript
    assert "navigateBack" in javascript
    assert "function updateNodeHash(nodeId)" in javascript
    assert "function readNodeHash()" in javascript
    assert "updateNodeHash(null);" in javascript
    assert "updateNodeHash(state.selected.id);" in javascript
    assert "selectNode(nodeId);" in javascript
    assert "loadProjects(null).then(function ()" in javascript
    assert "if (!state.project) return null;" in javascript
    assert "state.selected = null;" in javascript
    assert "renderDetail(null);" in javascript
    assert "if (!state.project || !nodeId) return Promise.resolve(null);" in javascript
    assert "updateNodeHash(state.focus && state.focus.focus && state.focus.focus.id)" in javascript
    assert "renderOverview" in javascript
    assert "EventSource" in javascript
    assert 'method: "PATCH"' in javascript
    assert "data-criterion-index" in javascript
    assert "computeOverviewLayout" in javascript
    assert "computeOverviewLayoutInWorker" in javascript
    assert "nodes.length > 500" in javascript
    assert "new Worker" in javascript
    assert "create-child-form" in javascript
    assert '"/api/goals/batch"' in javascript
    assert "loadReviewQueue" in javascript
    assert "待审核队列" in javascript
    assert "lifecycle-apply" in javascript
    assert '"/apply-evidence-verification"' in javascript
    assert '"/submit-for-review"' in javascript
    assert '"/approve-review"' in javascript
    assert '"/reject-review"' in javascript
    assert "confirm_token" in javascript
    assert "function runDialogAction()" in javascript
    assert 'if (!action || $("goal-dialog").hidden) return;' in javascript
    assert 'event.key === "Enter"' in javascript
    assert "dialogPreviousFocus" in javascript
    assert "previousFocus !== document.activeElement" in javascript
    assert 'event.key === "Tab"' in javascript
    assert 'aria-describedby="dialog-content"' in html
    assert 'id="goal-dialog" class="dialog-backdrop" role="presentation" aria-hidden="true" hidden' in html
    assert 'setAttribute("aria-hidden", "false")' in javascript
    assert 'role="status" aria-live="polite"' in html
    assert 'id="empty-state" class="empty-state" role="status" aria-live="polite" hidden' in html
    assert 'id="overview-empty" class="overview-empty" role="status" aria-live="polite" hidden' in html
    assert 'setAttribute("aria-busy"' in javascript
    assert '$("refresh-button").disabled = loading;' in javascript
    assert 'var disabled = state.loading || !state.project;' in javascript
    assert "var projectId = state.project.id" in javascript
    assert "state.project.id === projectId" in javascript
    assert "var projectRequestId = 0" in javascript
    assert "requestId !== projectRequestId" in javascript
    assert "var detailRequestId = 0" in javascript
    assert "requestId !== detailRequestId" in javascript
    assert "loadOverview(projectId)" in javascript
    assert "loadProjectSummary(projectId)" in javascript
    assert "loadReviewQueue(projectId)" in javascript
    assert "var requestId = projectRequestId" in javascript
    assert "详情加载失败" in javascript
    assert 'throw new Error("当前目标已不属于所选项目，请刷新后重试")' in javascript
    assert "if (!state.project || !state.focus) return;" in javascript
    assert "loadHistory(projectId)" in javascript
    assert "var requestId = projectRequestId" in javascript
    assert "当前目标已不属于所选项目，请刷新后重试" in javascript
    assert "currentProjectValid(projectId)" in javascript
    assert "当前目标已不属于所选项目，请刷新后重试" in javascript
    assert "var projectId = state.project && state.project.id" in javascript
    assert "return null;" in javascript
    assert javascript.count("当前目标已不属于所选项目，请刷新后重试") >= 3
    assert "function requestApplyVerification" in javascript
    assert "project_id: projectId" in javascript
    assert "证据已写入" in javascript
    assert "loadProjects(projectId)" in javascript
    assert "var requestId = ++projectRequestId" in javascript
    assert "if (requestId !== projectRequestId) return null;" in javascript
    assert "历史状态加载失败" in javascript
    assert "详情加载失败" in javascript
    assert "requestId !== detailRequestId" in javascript
    assert "项目摘要加载失败" in javascript
    assert "目标图已更新" in javascript
    assert "var projectId = state.project && state.project.id" in javascript
    assert "function requestDelete(node, confirmToken)" in javascript
    assert "function refreshAfterNodeChange(nodeId)" in javascript
    assert "}).catch(function (error) { return showProjectError(error, projectId, requestId); });" in javascript
    assert "currentProjectRequestValid(projectId, requestId)" in javascript
    assert "目标更新检查失败" in javascript
    assert "requestId === projectRequestId" in javascript
    assert "function currentProjectValid(projectId)" in javascript
    assert "if (!state.selected) return;" in javascript
    assert "function currentProjectRequestValid(projectId, requestId)" in javascript
    assert "if (activeStream()) open();" in javascript
    assert "if (!activeStream()) return;" in javascript
    assert "source.close();" in javascript
    assert "function reportProjectError(projectId, message)" in javascript
    assert '$("loading-state").textContent = message;' in javascript
    assert "function showProjectError(error, projectId, requestId)" in javascript
    assert "if (projectId == null)" in javascript
    assert 'message === "Goal Service 不可用"' in javascript
    assert "function invalidatePendingInteraction()" in javascript
    assert "state.menuPreviousFocus = null;" in javascript
    assert "state.dialogPreviousFocus = null;" in javascript
    assert "invalidatePendingInteraction();" in javascript
    assert "state.reviewQueue = [];" in javascript
    assert "renderOverview();" in javascript
    assert '$("project-progress").textContent = "项目进度 --";' in javascript
    assert "function renderOverview(expectedProjectId, expectedRequestId)" in javascript
    assert "currentProjectRequestValid(expectedProjectId, expectedRequestId)" in javascript
    assert "if (!currentProjectRequestValid(projectId, requestId)) return null;" in javascript
    assert "currentProjectRequestValid(projectId, requestId)" in javascript
    assert "var requestId = ++detailRequestId" in javascript
    assert "currentProjectRequestValid(projectId, requestId)" in javascript
    assert javascript.count("currentProjectRequestValid(projectId, requestId)") >= 6
    assert javascript.count("currentProjectValid(projectId)") >= 8
    assert "currentProjectValid(projectId) || requestId !== detailRequestId" in javascript
    assert "if (currentProjectRequestValid(projectId, requestId)) state.reviewQueue = [];" in javascript
    assert "requestId !== projectRequestId" in javascript
    assert "核验记录已写入" in javascript
    assert "正在写入证据..." in javascript
    assert "正在提交审核..." in javascript
    assert javascript.count("currentProjectValid(projectId)") >= 8
    assert "return Promise.resolve(null);" in javascript
    assert "function requestDelete(node, confirmToken)" in javascript
    assert "var projectId = state.project.id" in javascript
    assert "requestId !== projectRequestId" in javascript
    assert "function dialogFocusableElements()" in javascript
    assert 'event.key === "ContextMenu"' in javascript
    assert 'event.key === "F10" && event.shiftKey' in javascript
    assert "function svgPointToClient(svgElement, x, y)" in javascript
    assert "openNodeMenuAtSvgPoint(node, $(\"radial-svg\"), x, y);" in javascript
    assert "openNodeMenuAtSvgPoint(node, $(\"overview-svg\"), position.x, position.y);" in javascript
    assert 'aria-keyshortcuts": "Enter Space Shift+F10"' in javascript
    assert '完成度 " + percent(node.progress)' in javascript
    assert 'var firstItem = menu.querySelector("button");' in javascript
    assert "menuPreviousFocus" in javascript
    assert "previousFocus !== document.activeElement" in javascript
    assert "function nodeMenuFocusableElements()" in javascript
    assert "if (!node || !state.project || (node.projectId && node.projectId !== state.project.id)) return;" in javascript
    assert "if (!node || !state.project || (node.projectId && node.projectId !== state.project.id)) return;" in javascript
    assert "return null;" in javascript
    assert "return loadNodeDetail(nodeId);" in javascript
    assert "data-menu-action=\"create-child\"" in html
    assert "https://cdn." not in javascript.lower()
    assert '<script src="./app.js"></script>' in html
    assert "@media (max-width: 560px)" in css
    assert ".status-blocked" in css
    assert ".review-queue" in css
    assert ".lifecycle-item" in css


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


def test_supervisor_review_session_requires_and_returns_configured_token():
    app = FastAPI()
    from voidcube.systems.supervisor.ui_routes import SupervisorUIRoutePorts, mount_supervisor_ui_routes

    async def empty(*_args, **_kwargs):
        return {"ok": True}

    mount_supervisor_ui_routes(SupervisorUIRoutePorts(
        app=app, enabled=True, ui_path="/ui", get_ui=empty, get_state=empty,
        get_events=empty, get_api_b_thinking_events=empty, get_voice_levels=empty,
        get_media_events=empty, enqueue_media=empty, enqueue_media_playlist=empty,
        get_identity_archive=empty, get_identity_turns=empty, get_evolution_audit=empty,
        get_evolution_candidates=empty, consent_evolution_candidate=empty,
        review_session_token="review-secret",
    ))
    with TestClient(app) as client:
        assert client.post("/ui/review-session").status_code == 401
        response = client.post(
            "/ui/review-session",
            headers={"X-VoidCube-Review-Token": "review-secret"},
        )
    assert response.status_code == 200
    assert response.json()["review_token"]
    assert response.json()["review_token"] != "review-secret"
    assert response.json()["expires_in"] >= 30
    token = response.json()["review_token"]
    assert client.delete(
        "/ui/review-session", headers={"X-VoidCube-Review-Token": token}
    ).json() == {"revoked": True}
    assert client.delete(
        "/ui/review-session", headers={"X-VoidCube-Review-Token": token}
    ).json() == {"revoked": False}
    assert client.delete("/ui/review-session").status_code == 401


def test_supervisor_review_session_prunes_expired_sessions():
    app = FastAPI()
    from voidcube.systems.supervisor.ui_routes import SupervisorUIRoutePorts, mount_supervisor_ui_routes

    async def empty(*_args, **_kwargs):
        return {"ok": True}

    mount_supervisor_ui_routes(SupervisorUIRoutePorts(
        app=app, enabled=True, ui_path="/ui", get_ui=empty, get_state=empty,
        get_events=empty, get_api_b_thinking_events=empty, get_voice_levels=empty,
        get_media_events=empty, enqueue_media=empty, enqueue_media_playlist=empty,
        get_identity_archive=empty, get_identity_turns=empty, get_evolution_audit=empty,
        get_evolution_candidates=empty, consent_evolution_candidate=empty,
        review_session_token="review-secret",
    ))
    app.state.review_sessions = {"expired": 0.0}
    with TestClient(app) as client:
        response = client.post(
            "/ui/review-session",
            headers={"X-VoidCube-Review-Token": "review-secret"},
        )
    assert response.status_code == 200
    assert "expired" not in app.state.review_sessions


def test_supervisor_review_session_can_use_shared_store():
    app = FastAPI()
    from voidcube.systems.supervisor.ui_routes import SupervisorUIRoutePorts, mount_supervisor_ui_routes

    async def empty(*_args, **_kwargs):
        return {"ok": True}

    shared = {}
    mount_supervisor_ui_routes(SupervisorUIRoutePorts(
        app=app, enabled=True, ui_path="/ui", get_ui=empty, get_state=empty,
        get_events=empty, get_api_b_thinking_events=empty, get_voice_levels=empty,
        get_media_events=empty, enqueue_media=empty, enqueue_media_playlist=empty,
        get_identity_archive=empty, get_identity_turns=empty, get_evolution_audit=empty,
        get_evolution_candidates=empty, consent_evolution_candidate=empty,
        review_session_token="review-secret", review_session_store=shared,
    ))
    with TestClient(app) as client:
        response = client.post(
            "/ui/review-session",
            headers={"X-VoidCube-Review-Token": "review-secret"},
        )
    assert response.status_code == 200
    assert response.json()["review_token"] in shared


def test_supervisor_review_session_uses_explicit_empty_store_for_revoke():
    app = FastAPI()
    from voidcube.systems.supervisor.ui_routes import SupervisorUIRoutePorts, mount_supervisor_ui_routes

    async def empty(*_args, **_kwargs):
        return {"ok": True}

    explicit = {}
    app.state.review_sessions = {"unexpected": 9999999999.0}
    mount_supervisor_ui_routes(SupervisorUIRoutePorts(
        app=app, enabled=True, ui_path="/ui", get_ui=empty, get_state=empty,
        get_events=empty, get_api_b_thinking_events=empty, get_voice_levels=empty,
        get_media_events=empty, enqueue_media=empty, enqueue_media_playlist=empty,
        get_identity_archive=empty, get_identity_turns=empty, get_evolution_audit=empty,
        get_evolution_candidates=empty, consent_evolution_candidate=empty,
        review_session_token="review-secret", review_session_store=explicit,
    ))
    with TestClient(app) as client:
        assert client.delete(
            "/ui/review-session", headers={"X-VoidCube-Review-Token": "unexpected"}
        ).json() == {"revoked": False}
    assert "unexpected" not in explicit


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


def test_review_session_is_shared_between_supervisor_and_goal_manager(tmp_path):
    from voidcube.infrastructure.security.review_sessions import SQLiteReviewSessionStore
    from voidcube.systems.supervisor.ui_routes import SupervisorUIRoutePorts, mount_supervisor_ui_routes

    session_db = tmp_path / "review-sessions.db"
    supervisor = FastAPI()

    async def empty(*_args, **_kwargs):
        return {"ok": True}

    mount_supervisor_ui_routes(SupervisorUIRoutePorts(
        app=supervisor, enabled=True, ui_path="/ui", get_ui=empty, get_state=empty,
        get_events=empty, get_api_b_thinking_events=empty, get_voice_levels=empty,
        get_media_events=empty, enqueue_media=empty, enqueue_media_playlist=empty,
        get_identity_archive=empty, get_identity_turns=empty, get_evolution_audit=empty,
        get_evolution_candidates=empty, consent_evolution_candidate=empty,
        review_session_token="review-secret",
        review_session_store=SQLiteReviewSessionStore(session_db),
    ))
    goal = create_app({
        "db_path": str(tmp_path / "goals.db"),
        "human_review_token": "review-secret",
        "review_session_db_path": str(session_db),
    })
    with TestClient(supervisor) as supervisor_client, TestClient(goal) as goal_client:
        issued = supervisor_client.post(
            "/ui/review-session",
            headers={"X-VoidCube-Review-Token": "review-secret"},
        )
        token = issued.json()["review_token"]
        assert goal_client.app.state.review_sessions.get(token) is not None
        assert supervisor_client.delete(
            "/ui/review-session", headers={"X-VoidCube-Review-Token": token}
        ).json() == {"revoked": True}
        assert goal_client.app.state.review_sessions.get(token) is None


def test_goal_manager_defaults_review_session_store_to_runtime_home(monkeypatch, tmp_path):
    monkeypatch.setenv("VOIDCUBE_HOME", str(tmp_path))
    from plugins.goal_manager.config import service_config

    config = service_config({"db_path": str(tmp_path / "goals.db")})
    assert config["review_session_db_path"] == str(tmp_path / "runtime" / "goals" / "review_sessions.db")
    relative = service_config({
        "db_path": str(tmp_path / "goals.db"),
        "review_session_db_path": "shared/review.db",
    })
    assert relative["review_session_db_path"] == str(tmp_path / "shared" / "review.db")


def test_review_session_path_is_stable_when_services_use_different_working_directories(
    monkeypatch, tmp_path
):
    from voidcube.infrastructure.security.review_sessions import resolve_review_session_path

    monkeypatch.setenv("VOIDCUBE_HOME", str(tmp_path / "runtime-home"))
    relative = resolve_review_session_path("shared/review.db")
    monkeypatch.chdir(tmp_path)
    assert resolve_review_session_path("shared/review.db") == relative


def test_review_session_store_close_is_reusable(tmp_path):
    from voidcube.infrastructure.security.review_sessions import SQLiteReviewSessionStore

    store = SQLiteReviewSessionStore(tmp_path / "sessions.db")
    store.close()
    assert store.get("missing") is None


def test_review_session_store_expiry_is_enforced_across_instances(tmp_path):
    from voidcube.infrastructure.security.review_sessions import SQLiteReviewSessionStore

    path = tmp_path / "sessions.db"
    first = SQLiteReviewSessionStore(path)
    second = SQLiteReviewSessionStore(path)
    first.put("expired", 10.0)
    first.put("active", 30.0)

    assert second.get("expired", now=20.0) is None
    assert first.get("expired", now=20.0) is None
    assert second.get("active", now=20.0) == 30.0


def test_goal_manager_shutdown_releases_databases_for_restart(tmp_path):
    from voidcube.infrastructure.security.review_sessions import SQLiteReviewSessionStore

    db_path = tmp_path / "goals.db"
    review_path = tmp_path / "review.db"
    app = create_app({
        "db_path": str(db_path),
        "review_session_db_path": str(review_path),
    })
    with TestClient(app):
        app.state.review_sessions.put("restartable", 9999999999.0)

    restarted = create_app({
        "db_path": str(db_path),
        "review_session_db_path": str(review_path),
    })
    assert restarted.state.review_sessions.get("restartable") == 9999999999.0
    assert SQLiteReviewSessionStore(review_path).get("restartable") == 9999999999.0
