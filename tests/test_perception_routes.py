from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from voidcube.systems.supervisor.perception_routes import mount_perception_routes


def test_perception_routes_return_unavailable_without_service() -> None:
    app = FastAPI()
    mount_perception_routes(app, get_service=lambda: None)
    client = TestClient(app)

    assert client.get("/runtime/perception/scene").status_code == 503
    assert client.post("/runtime/perception/context", json={"level": "L0"}).status_code == 503
    assert client.post("/runtime/perception/start", json={"consent": True}).status_code == 503


def test_perception_routes_delegate_read_only_queries() -> None:
    class Service:
        def current_scene(self) -> dict:
            return {"scene": "editing_code"}

        def timeline(self, **kwargs: object) -> list[dict]:
            return [{"summary": "编辑代码"}]

        def context(self, **kwargs: object) -> dict:
            return {"context_level": kwargs["level"], "privacy": {"raw_frames_included": False}}

    app = FastAPI()
    mount_perception_routes(app, get_service=lambda: Service())
    client = TestClient(app)

    assert client.get("/runtime/perception/scene").json()["scene"]["scene"] == "editing_code"
    result = client.post("/runtime/perception/context", json={"level": "L2", "user_query": "总结"})
    assert result.status_code == 200
    assert result.json()["context"]["privacy"]["raw_frames_included"] is False
