"""三段式 scene 分域全量验证脚本（基线 §8.1）.

覆盖：
  1. 静态定义：监督者 / Agent / 执行器各自的合法 scene 集合
  2. 边界铁律：监督者永不报 learning/code_editing/executing/body_switch；
              Agent 永不报 body_switch；执行器永不报 learning/code_editing
  3. 网关聚合端点：/admin/scenes 与 /admin/scenes/refresh
  4. CLI 渲染：dashboard 三段式 + status.py 三段式
  5. 运行时聚合单元：使用 Fake aiohttp 跑通 gateway._refresh_*_scene
  6. 语法：所有改动文件 Python 3.14 语法通过
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import json
import re
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(r"f:\My_code\Traecode\VoidCube")

# ── Legal scene sets per baseline §8.1 ─────────────────────────────────
# Supervisor scene values are the 6 it can *own* per §8.1 表格;
# `body_switch` is executor-only — the supervisor reports `dispatch`
# when it has decided to hand off a body-switch request.
SUPERVISOR_LEGAL: frozenset = frozenset(
    {"idle", "planning", "drive", "memory", "maintenance", "dispatch"}
)
AGENT_LEGAL: frozenset = frozenset(
    {"idle", "learning", "code_editing", "executing"}
)
EXECUTOR_LEGAL: frozenset = frozenset({"idle", "body_switch"})


# ──────────────────────────────────────────────────────────────────────
# Tiny reporter
# ──────────────────────────────────────────────────────────────────────

_REPORT: List[Tuple[str, bool, str]] = []


def _check(name: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    suffix = f" -- {detail}" if detail else ""
    print(f"  [{mark}] {name}{suffix}")
    _REPORT.append((name, ok, detail))
    return ok


# ──────────────────────────────────────────────────────────────────────
# TEST 1: Legal-scene frozensets
# ──────────────────────────────────────────────────────────────────────

def test_legal_scenes_constants() -> bool:
    print("TEST 1: 三域 legal scene frozenset 定义正确")
    all_ok = True

    # Supervisor
    src = (ROOT / "systems/supervisor/planning_runtime.py").read_text(encoding="utf-8")
    m = re.search(
        r"SUPERVISOR_LEGAL_SCENES:\s*frozenset\[str\]\s*=\s*frozenset\(\s*\{([^}]+)\}\s*\)",
        src,
    )
    if not m:
        all_ok &= _check("supervisor 法律集未找到", False)
    else:
        defined = {s.strip().strip("\"'") for s in m.group(1).split(",") if s.strip()}
        all_ok &= _check(
            f"supervisor = {sorted(defined)}",
            defined == SUPERVISOR_LEGAL,
            detail=f"expected {sorted(SUPERVISOR_LEGAL)}" if defined != SUPERVISOR_LEGAL else "",
        )

    # Agent
    src = (ROOT / "systems/agent/run_agent_instance.py").read_text(encoding="utf-8")
    m = re.search(
        r"AGENT_LEGAL_SCENES:\s*frozenset\s*=\s*frozenset\(\s*\{([^}]+)\}\s*\)",
        src,
    )
    if not m:
        all_ok &= _check("agent 法律集未找到", False)
    else:
        defined = {s.strip().strip("\"'") for s in m.group(1).split(",") if s.strip()}
        all_ok &= _check(
            f"agent = {sorted(defined)}",
            defined == AGENT_LEGAL,
        )

    # Executor
    src = (ROOT / "systems/execution/service.py").read_text(encoding="utf-8")
    m = re.search(
        r"EXECUTOR_LEGAL_SCENES:\s*frozenset\s*=\s*frozenset\(\s*\{([^}]+)\}\s*\)",
        src,
    )
    if not m:
        all_ok &= _check("executor 法律集未找到", False)
    else:
        defined = {s.strip().strip("\"'") for s in m.group(1).split(",") if s.strip()}
        all_ok &= _check(
            f"executor = {sorted(defined)}",
            defined == EXECUTOR_LEGAL,
        )
    return all_ok


# ──────────────────────────────────────────────────────────────────────
# TEST 2: Boundary integrity — supervisors never report Agent/Executor scenes
# ──────────────────────────────────────────────────────────────────────

def test_supervisor_boundary() -> bool:
    print()
    print("TEST 2: 监督者源码不含 API-A 专属 scene 字面量")
    # Forbidden scenes for the supervisor (baseline §8.1):
    #   learning / code_editing / executing — Agent (API-A) territory
    #   body_switch                       — Executor territory only
    # ``idle`` is legal for every reporter and must be excluded from the
    # forbidden set so the test does not flag legitimate `scene="idle"`
    # sentinels in the supervisor source.
    forbidden = {"learning", "code_editing", "executing", "body_switch"}
    bad: List[Tuple[str, int, str]] = []
    files = [
        ROOT / "systems/supervisor/planning_runtime.py",
        ROOT / "systems/supervisor/service_runtime.py",
        ROOT / "systems/supervisor/ui_runtime.py",
        ROOT / "systems/supervisor/endogenous_drive.py",
    ]
    for path in files:
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "data-scene=" in line:  # CSS line, ignore
                continue
            for scene in forbidden:
                if f'scene="{scene}"' in line or f"scene='{scene}'" in line:
                    bad.append((str(path), i, scene))
    return _check(
        "无 learning / code_editing / executing / body_switch 字面量",
        not bad,
        detail=f"违规: {bad}" if bad else "",
    )


def test_agent_boundary() -> bool:
    print()
    print("TEST 3: Agent 源码不含 body_switch 字面量")
    src = (ROOT / "systems/agent/run_agent_instance.py").read_text(encoding="utf-8")
    bad: List[int] = []
    for i, line in enumerate(src.splitlines(), 1):
        if 'scene="body_switch"' in line or "scene='body_switch'" in line:
            bad.append(i)
    return _check(
        "Agent 永远不上报 body_switch",
        not bad,
        detail=f"违规行: {bad}" if bad else "",
    )


def test_executor_boundary() -> bool:
    print()
    print("TEST 4: 执行器源码不含 learning / code_editing 字面量")
    src = (ROOT / "systems/execution/service.py").read_text(encoding="utf-8")
    bad: List[Tuple[int, str]] = []
    for i, line in enumerate(src.splitlines(), 1):
        for scene in ("learning", "code_editing"):
            if f'scene="{scene}"' in line or f"scene='{scene}'" in line:
                bad.append((i, scene))
    return _check(
        "执行器永远不上报 learning / code_editing",
        not bad,
        detail=f"违规: {bad}" if bad else "",
    )


# ──────────────────────────────────────────────────────────────────────
# TEST 5: Gateway aggregation endpoint
# ──────────────────────────────────────────────────────────────────────

def test_gateway_scenes_route() -> bool:
    print()
    print("TEST 5: 网关注册 /admin/scenes 与 /admin/scenes/refresh")
    src = (ROOT / "systems/gateway/internal_gateway.py").read_text(encoding="utf-8")
    return _check(
        "两处 add_api_route 路由已注册",
        '"/admin/scenes"' in src
        and '"/admin/scenes/refresh"' in src
        and "get_scenes" in src
        and "refresh_scenes" in src,
    )


def test_gateway_scenes_legal() -> bool:
    print()
    print("TEST 6: 网关自带 legal-scene frozenset 与基线一致")
    src = (ROOT / "systems/gateway/internal_gateway.py").read_text(encoding="utf-8")
    found_sup = re.search(
        r"SUPERVISOR_LEGAL_SCENES:\s*frozenset\s*=\s*frozenset\(\s*\{([^}]+)\}\s*\)",
        src,
    )
    found_agt = re.search(
        r"AGENT_LEGAL_SCENES:\s*frozenset\s*=\s*frozenset\(\s*\{([^}]+)\}\s*\)",
        src,
    )
    found_exe = re.search(
        r"EXECUTOR_LEGAL_SCENES:\s*frozenset\s*=\s*frozenset\(\s*\{([^}]+)\}\s*\)",
        src,
    )
    ok = bool(found_sup and found_agt and found_exe)
    if ok:
        s = {x.strip().strip("\"'") for x in found_sup.group(1).split(",") if x.strip()}
        a = {x.strip().strip("\"'") for x in found_agt.group(1).split(",") if x.strip()}
        e = {x.strip().strip("\"'") for x in found_exe.group(1).split(",") if x.strip()}
        ok = s == SUPERVISOR_LEGAL and a == AGENT_LEGAL and e == EXECUTOR_LEGAL
    return _check(
        "网关三域 legal 与基线一致",
        ok,
    )


# ──────────────────────────────────────────────────────────────────────
# TEST 7: CLI three-segment renderers
# ──────────────────────────────────────────────────────────────────────

def test_cli_dashboard_renderer() -> bool:
    print()
    print("TEST 7: dashboard.print_three_segment_status_bar 已集成")
    src = (ROOT / "VoidCube_cli/ops/dashboard.py").read_text(encoding="utf-8")
    return _check(
        "dashboard.py 含 print_three_segment_status_bar 与三段常量",
        "print_three_segment_status_bar" in src
        and "REPORTER_SEGMENT" in src
        and '"supervisor"' in src
        and '"agent"' in src
        and '"executor"' in src
        and "fetch_scenes_aggregated" in src,
    )


def test_cli_status_renderer() -> bool:
    print()
    print("TEST 8: status._print_three_segment_scene_bar 已注入 show_status")
    src = (ROOT / "VoidCube_cli/status.py").read_text(encoding="utf-8")
    return _check(
        "status.py 含三段式函数并在 show_status 入口处调用",
        "_print_three_segment_scene_bar" in src
        and "/admin/scenes" in src
        and "API-B" in src
        and "API-A" in src,
    )


# ──────────────────────────────────────────────────────────────────────
# TEST 9: 运行时聚合单元 — mock aiohttp 跑通 _refresh_*_scene
# ──────────────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, payload: Dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status = status

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def json(self) -> Dict[str, Any]:
        return self._payload


class _FakeSession:
    def __init__(self, by_url: Dict[str, Dict[str, Any]]) -> None:
        self._by_url = by_url

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    def get(self, url: str, timeout: Any = None) -> _FakeResponse:
        payload = self._by_url.get(url, {"error": "no fixture"})
        return _FakeResponse(payload)


def _make_fake_aiohttp(by_url: Dict[str, Dict[str, Any]]) -> types.ModuleType:
    """Inject a minimal aiohttp stub into sys.modules."""
    mod = types.ModuleType("aiohttp")
    mod.ClientSession = lambda *a, **k: _FakeSession(by_url)  # type: ignore[attr-defined]
    mod.ClientTimeout = lambda *a, **k: None  # type: ignore[attr-defined]
    return mod


def test_gateway_runtime_aggregation() -> bool:
    print()
    print("TEST 9: 运行时聚合 -- 三域 scene 拉取 + 合法性校验")
    # Build the by_url map the fake session will return.
    by_url = {
        "http://sup.local/ui/state": {"scene": "planning", "title": "T", "summary": "S"},
        "http://agent.local/v1/agent/scene": {"scene": "learning", "scene_task_id": "t-123"},
        "http://exec.local/executor/scene": {"scene": "body_switch"},
        # Negative: a malicious supervisor returns an illegal scene.
        "http://bad-sup.local/ui/state": {"scene": "learning"},
    }
    fake_aiohttp = _make_fake_aiohttp(by_url)
    sys.modules["aiohttp"] = fake_aiohttp
    try:
        # Import lazily so the fake module is in place.
        from systems.gateway import internal_gateway as ig  # type: ignore
    except Exception as exc:
        return _check("import systems.gateway.internal_gateway", False, detail=str(exc))
    finally:
        sys.modules.pop("aiohttp", None)

    # Build a gateway instance via the constructor and inject fake services.
    from systems.gateway.internal_gateway import (  # type: ignore
        GatewayConfig,
        InternalGateway,
        ServiceInfo,
    )

    cfg = GatewayConfig()
    gw = InternalGateway(cfg)

    from datetime import datetime as _dt

    now_iso = "2025-01-01T00:00:00+00:00"
    services = [
        ServiceInfo(
            service_id="sup-1",
            service_name="sup-1",
            service_type="supervisor",
            address="http://sup.local",
            health_endpoint="/health",
            metadata={},
            registered_at=_dt.now(),
            last_health_check=_dt.now(),
            healthy=True,
        ),
        ServiceInfo(
            service_id="sup-bad",
            service_name="sup-bad",
            service_type="supervisor",
            address="http://bad-sup.local",
            health_endpoint="/health",
            metadata={},
            registered_at=_dt.now(),
            last_health_check=_dt.now(),
            healthy=True,
        ),
        ServiceInfo(
            service_id="agent-1",
            service_name="agent-1",
            service_type="agent",
            address="http://agent.local",
            health_endpoint="/health",
            metadata={"slot_id": "slot-A"},
            registered_at=_dt.now(),
            last_health_check=_dt.now(),
            healthy=True,
        ),
        ServiceInfo(
            service_id="exec-1",
            service_name="exec-1",
            service_type="executor",
            address="http://exec.local",
            health_endpoint="/health",
            metadata={},
            registered_at=_dt.now(),
            last_health_check=_dt.now(),
            healthy=True,
        ),
    ]
    for s in services:
        gw._services[s.service_id] = s

    # Pre-set scenes to a sentinel that the refresh must overwrite.
    for k in ("supervisor", "agent", "executor"):
        gw._scenes_cache[k]["scene"] = "stale-sentinel"

    async def _run() -> Dict[str, Any]:
        # Re-inject aiohttp in the coroutine because the route fetch uses
        # import aiohttp at call-time.
        sys.modules["aiohttp"] = fake_aiohttp
        try:
            await gw.refresh_scenes()
            return await gw.get_scenes()
        finally:
            sys.modules.pop("aiohttp", None)

    payload = asyncio.run(_run())
    scenes = payload.get("scenes") or {}

    sup = scenes.get("supervisor") or {}
    agt = scenes.get("agent") or {}
    exe = scenes.get("executor") or {}

    all_ok = True
    # The malicious supervisor is rejected; the first healthy supervisor wins
    # and reports "planning".
    all_ok &= _check(
        f"supervisor scene={sup.get('scene')!r}（期望 planning）",
        sup.get("scene") == "planning",
    )
    all_ok &= _check(
        f"agent scene={agt.get('scene')!r}（期望 learning）",
        agt.get("scene") == "learning",
    )
    all_ok &= _check(
        f"agent scene_task_id={agt.get('scene_task_id')!r}（期望 t-123）",
        agt.get("scene_task_id") == "t-123",
    )
    all_ok &= _check(
        f"executor scene={exe.get('scene')!r}（期望 body_switch）",
        exe.get("scene") == "body_switch",
    )
    # summary has three independent scene names
    summary = payload.get("summary") or {}
    all_ok &= _check(
        f"summary 独立三段: {summary}",
        summary.get("supervisor") == "planning"
        and summary.get("agent") == "learning"
        and summary.get("executor") == "body_switch",
    )
    return all_ok


# ──────────────────────────────────────────────────────────────────────
# TEST 10: Python 3.14 syntax across all touched files
# ──────────────────────────────────────────────────────────────────────

def test_syntax() -> bool:
    print()
    print("TEST 10: 所有改动文件 Python 3.14 语法检查")
    files = [
        ROOT / "systems/gateway/internal_gateway.py",
        ROOT / "systems/supervisor/ui_runtime.py",
        ROOT / "systems/supervisor/planning_runtime.py",
        ROOT / "systems/agent/run_agent_instance.py",
        ROOT / "systems/execution/service.py",
        ROOT / "VoidCube_cli/ops/dashboard.py",
        ROOT / "VoidCube_cli/status.py",
    ]
    all_ok = True
    for f in files:
        try:
            ast.parse(f.read_text(encoding="utf-8"))
            all_ok &= _check(f.name + " OK", True)
        except SyntaxError as e:
            all_ok &= _check(f.name + f" -- {e}", False)
    return all_ok


# ──────────────────────────────────────────────────────────────────────
# TEST 11: 端到端 — 起真实 HTTP server 通过 TestClient 调 /admin/scenes
# ──────────────────────────────────────────────────────────────────────

def test_e2e_admin_scenes_endpoint() -> bool:
    """Spin up the FastAPI app via TestClient and walk through:
      - register a fake supervisor / agent / executor
      - call GET  /admin/scenes            → returns cached envelope
      - call POST /admin/scenes/refresh    → forces re-fetch, returns
                                             refreshed envelope
      - verify the response shape matches
        {status, scenes: {supervisor, agent, executor}, summary}
    """
    print()
    print("TEST 11: 端到端 FastAPI — /admin/scenes 与 /admin/scenes/refresh")

    from datetime import datetime as _dt

    # Build a gateway instance + register fake reporters.
    from systems.gateway.internal_gateway import (
        GatewayConfig,
        InternalGateway,
        ServiceInfo,
    )
    cfg = GatewayConfig()
    gw = InternalGateway(cfg)
    services = [
        ServiceInfo(
            service_id="sup-1",
            service_name="sup-1",
            service_type="supervisor",
            address="http://127.0.0.1:1",  # unreachable; aggregator will mark ⛔
            health_endpoint="/health",
            metadata={},
            registered_at=_dt.now(),
            last_health_check=_dt.now(),
            healthy=True,
        ),
        ServiceInfo(
            service_id="agent-1",
            service_name="agent-1",
            service_type="agent",
            address="http://127.0.0.1:1",
            health_endpoint="/health",
            metadata={"slot_id": "slot-A"},
            registered_at=_dt.now(),
            last_health_check=_dt.now(),
            healthy=True,
        ),
        ServiceInfo(
            service_id="exec-1",
            service_name="exec-1",
            service_type="executor",
            address="http://127.0.0.1:1",
            health_endpoint="/health",
            metadata={},
            registered_at=_dt.now(),
            last_health_check=_dt.now(),
            healthy=True,
        ),
    ]
    for s in services:
        gw._services[s.service_id] = s

    # Pre-seed the cache so the GET path can be exercised even when the
    # fake reporter URLs are unreachable.
    gw._scenes_cache["supervisor"]["scene"] = "idle"
    gw._scenes_cache["supervisor"]["reachable"] = True
    gw._scenes_cache["agent"]["scene"] = "idle"
    gw._scenes_cache["agent"]["reachable"] = True
    gw._scenes_cache["executor"]["scene"] = "idle"
    gw._scenes_cache["executor"]["reachable"] = True

    try:
        from fastapi.testclient import TestClient
    except Exception as exc:
        return _check("fastapi.testclient import", False, detail=str(exc))

    client = TestClient(gw.app)

    # 1) GET /admin/scenes — should return the cached envelope.
    all_ok = True
    try:
        resp = client.get("/admin/scenes")
    except Exception as exc:
        return _check("GET /admin/scenes", False, detail=str(exc))
    all_ok &= _check(
        f"GET /admin/scenes status={resp.status_code}（期望 200）",
        resp.status_code == 200,
    )
    body = resp.json() if resp.status_code == 200 else {}
    all_ok &= _check(
        f"envelope 含 scenes 与 summary: keys={sorted(body.keys())}",
        "scenes" in body and "summary" in body and "status" in body,
    )
    if isinstance(body, dict):
        for k in ("supervisor", "agent", "executor"):
            all_ok &= _check(
                f"scenes.{k} 字段齐全",
                k in (body.get("scenes") or {}),
            )
        summary = body.get("summary") or {}
        all_ok &= _check(
            f"summary 三段独立: {summary}",
            set(summary.keys()) == {"supervisor", "agent", "executor"},
        )

    # 2) GET /admin/scenes?refresh=true — forces a re-fetch.
    try:
        resp = client.get("/admin/scenes", params={"refresh": "true"})
    except Exception as exc:
        return _check("GET /admin/scenes?refresh=true", False, detail=str(exc))
    all_ok &= _check(
        f"GET /admin/scenes?refresh=true status={resp.status_code}（期望 200）",
        resp.status_code == 200,
    )

    # 3) POST /admin/scenes/refresh — force-refresh endpoint.
    try:
        resp = client.post("/admin/scenes/refresh")
    except Exception as exc:
        return _check("POST /admin/scenes/refresh", False, detail=str(exc))
    all_ok &= _check(
        f"POST /admin/scenes/refresh status={resp.status_code}（期望 200）",
        resp.status_code == 200,
    )
    body = resp.json() if resp.status_code == 200 else {}
    all_ok &= _check(
        f"refresh 响应 status=refreshed: {body.get('status')!r}",
        body.get("status") == "refreshed",
    )
    all_ok &= _check(
        "refresh 响应 scenes 仍是三段 dict",
        isinstance(body.get("scenes"), dict)
        and set(body["scenes"].keys()) == {"supervisor", "agent", "executor"},
    )

    # 4) The unreachable fake reporter must be marked not-reachable.
    body = client.get("/admin/scenes").json()
    sup_info = (body.get("scenes") or {}).get("supervisor") or {}
    all_ok &= _check(
        f"不可达 supervisor reachable=False（实际 {sup_info.get('reachable')}）",
        sup_info.get("reachable") is False,
    )
    all_ok &= _check(
        f"不可达 supervisor scene 默认为 idle（实际 {sup_info.get('scene')!r}）",
        sup_info.get("scene") == "idle",
    )
    return all_ok


# ──────────────────────────────────────────────────────────────────────
# Driver
# ──────────────────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 70)
    print(" VoidCube 三段式 scene 分域全量验证 (baseline §8.1)")
    print("=" * 70)

    results = [
        test_legal_scenes_constants(),
        test_supervisor_boundary(),
        test_agent_boundary(),
        test_executor_boundary(),
        test_gateway_scenes_route(),
        test_gateway_scenes_legal(),
        test_cli_dashboard_renderer(),
        test_cli_status_renderer(),
        test_gateway_runtime_aggregation(),
        test_syntax(),
        test_e2e_admin_scenes_endpoint(),
    ]

    print()
    print("=" * 70)
    passed = sum(1 for r in results if r)
    total = len(results)
    if passed == total:
        print(f"  ALL PASS — {passed}/{total}")
        return 0
    failed = [name for (name, ok, _) in _REPORT if not ok]
    print(f"  {passed}/{total} passed; failed: {failed}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
