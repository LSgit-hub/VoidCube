import builtins
import json

import pytest

import agent.skill_utils as skill_utils
import tools.skills_hub as skills_hub
import tools.skills_tool as skills_tool
import tools.web_tools as web_tools
import tools.web_tools_local as web_tools_local


@pytest.mark.unit
def test_firecrawl_sdk_is_only_required_when_backend_client_is_created(monkeypatch):
    real_import = builtins.__import__

    def import_without_firecrawl(name, *args, **kwargs):
        if name == "firecrawl":
            raise ImportError("firecrawl intentionally unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    monkeypatch.setattr(web_tools, "_firecrawl_client", None)
    monkeypatch.setattr(web_tools, "_firecrawl_client_config", None)
    monkeypatch.setattr(builtins, "__import__", import_without_firecrawl)

    with pytest.raises(ValueError, match="optional web dependencies"):
        web_tools._get_firecrawl_client()


@pytest.mark.unit
def test_skill_view_reads_repo_bundled_skill_when_user_skills_dir_missing(monkeypatch, tmp_path):
    bundled_root = tmp_path / "repo-skills"
    skill_dir = bundled_root / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: demo-skill\n"
        "description: Demo skill\n"
        "---\n"
        "\n"
        "# Demo Skill\n"
        "\n"
        "Bundled content.\n",
        encoding="utf-8",
    )

    missing_user_skills = tmp_path / "missing-user-skills"

    monkeypatch.setattr(skills_tool, "SKILLS_DIR", missing_user_skills)
    monkeypatch.setattr(
        skill_utils,
        "get_all_skills_dirs",
        lambda: [missing_user_skills, bundled_root],
    )

    payload = json.loads(skills_tool.skill_view("demo-skill"))

    assert payload["success"] is True
    assert payload["readiness_status"] == "available"
    assert "Bundled content." in payload["content"]


@pytest.mark.unit
def test_install_skill_from_sources_quarantines_scans_and_honors_non_overridable_policy(monkeypatch, tmp_path):
    bundle = skills_hub.SkillBundle(
        name="demo",
        files={"SKILL.md": "---\nname: demo\n---\n"},
        source="community",
        identifier="demo-id",
        trust_level="community",
    )

    class Source:
        def source_id(self):
            return "community"

        def search(self, query, limit):
            assert (query, limit) == ("demo", 1)
            return [
                skills_hub.SkillMeta(
                    name="demo",
                    description="demo",
                    source="community",
                    identifier="demo-id",
                    trust_level="community",
                )
            ]

        def fetch(self, _identifier):
            return bundle

    quarantined = tmp_path / "quarantine" / "demo"
    events: list[object] = []
    monkeypatch.setattr(skills_hub, "quarantine_bundle", lambda value: events.append(("quarantine", value)) or quarantined)
    scan = object()
    monkeypatch.setattr(skills_hub, "scan_skill", lambda path, *, source: events.append(("scan", path, source)) or scan)
    monkeypatch.setattr(skills_hub, "should_allow_install", lambda value: events.append(("policy", value)) or (True, "allowed"))
    monkeypatch.setattr(skills_hub, "install_from_quarantine", lambda *args: events.append(("install", args)))

    success, message, installed = skills_hub.install_skill_from_sources("demo", sources=[Source()])

    assert (success, message, installed) == (True, "", "demo")
    assert events == [
        ("quarantine", bundle),
        ("scan", quarantined, "community"),
        ("policy", scan),
        ("install", (quarantined, "demo", "", bundle, scan)),
    ]

    monkeypatch.setattr(skills_hub, "should_allow_install", lambda _value: (False, "Blocked by non-overridable project integration policy"))
    success, message, installed = skills_hub.install_skill_from_sources("demo", sources=[Source()])

    assert (success, message, installed) == (
        False,
        "Blocked by non-overridable project integration policy",
        "demo",
    )
    assert not any(event[0] == "install" for event in events[4:])


@pytest.mark.unit
def test_web_search_local_backend_uses_local_crawler(monkeypatch):
    monkeypatch.setattr(web_tools, "_get_backend", lambda: "local")

    def fake_local_web_search(query: str, limit: int = 5):
        assert query == "python"
        assert limit == 1
        return [
            {
                "title": "Python",
                "url": "https://www.python.org/",
                "description": "Official site",
            }
        ]

    monkeypatch.setattr("tools.web_tools_local.local_web_search", fake_local_web_search)

    payload = json.loads(web_tools.web_search_tool("python", limit=1))

    assert payload["success"] is True
    assert payload["data"]["web"][0]["title"] == "Python"
    assert payload["data"]["web"][0]["url"] == "https://www.python.org/"


@pytest.mark.unit
def test_local_web_backend_is_reported_available(monkeypatch):
    monkeypatch.setattr(web_tools, "_get_backend", lambda: "local")

    assert web_tools.check_web_api_key() is True


@pytest.mark.unit
def test_local_web_search_falls_back_to_bing(monkeypatch):
    calls = []

    def fake_duckduckgo(query: str, limit: int, region: str, timeout: float):
        calls.append("duckduckgo")
        raise TimeoutError("duckduckgo timeout")

    def fake_bing(query: str, limit: int, timeout: float):
        calls.append("bing")
        return [
            {
                "title": "Python",
                "url": "https://www.python.org/",
                "description": "Official site",
            }
        ]

    monkeypatch.setattr(web_tools_local, "_search_duckduckgo", fake_duckduckgo)
    monkeypatch.setattr(web_tools_local, "_search_bing", fake_bing)

    results = web_tools_local.local_web_search("python", limit=1)

    assert calls == ["duckduckgo", "bing"]
    assert len(results) == 1
    assert results[0]["url"] == "https://www.python.org/"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_web_extract_local_backend_uses_local_crawler(monkeypatch):
    monkeypatch.setattr(web_tools, "_get_backend", lambda: "local")
    monkeypatch.setattr(web_tools, "check_auxiliary_model", lambda **_: False)

    def fake_local_web_extract(url: str):
        assert url == "https://example.com"
        return {
            "url": url,
            "title": "Example",
            "content": "Local content",
            "metadata": {"title": "Example"},
            "success": True,
        }

    monkeypatch.setattr("tools.web_tools_local.local_web_extract", fake_local_web_extract)

    payload = json.loads(
        await web_tools.web_extract_tool(
            ["https://example.com"],
            use_llm_processing=False,
        )
    )

    assert payload["results"][0]["title"] == "Example"
    assert payload["results"][0]["content"] == "Local content"
    assert payload["results"][0]["error"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_web_extract_firecrawl_falls_back_to_local_when_unavailable(monkeypatch):
    monkeypatch.setattr(web_tools, "_get_backend", lambda: "firecrawl")
    monkeypatch.setattr(web_tools, "check_firecrawl_api_key", lambda: False)
    monkeypatch.setattr(web_tools, "check_auxiliary_model", lambda **_: False)

    def fake_local_web_extract(url: str):
        assert url == "https://example.com/fallback"
        return {
            "url": url,
            "title": "Fallback Page",
            "content": "Fallback content",
            "metadata": {"title": "Fallback Page"},
            "success": True,
        }

    monkeypatch.setattr("tools.web_tools_local.local_web_extract", fake_local_web_extract)

    payload = json.loads(
        await web_tools.web_extract_tool(
            ["https://example.com/fallback"],
            use_llm_processing=False,
        )
    )

    assert "fell back to local extraction" in payload["_warning"]
    assert payload["results"][0]["title"] == "Fallback Page"
    assert payload["results"][0]["content"] == "Fallback content"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_web_crawl_local_backend_uses_local_crawler(monkeypatch):
    monkeypatch.setattr(web_tools, "_get_backend", lambda: "local")
    monkeypatch.setattr(web_tools, "check_auxiliary_model", lambda **_: False)
    monkeypatch.setattr(web_tools, "is_safe_url", lambda _url: True)
    monkeypatch.setattr(web_tools, "check_website_access", lambda _url: None)

    def fake_local_web_crawl(url: str, max_depth: int, max_pages: int):
        assert url == "https://example.com/docs"
        assert max_depth == 2
        assert max_pages == 20
        return {
            "success": True,
            "pages": [
                {
                    "url": url,
                    "title": "Documentation",
                    "content": "Crawled locally",
                }
            ],
        }

    monkeypatch.setattr(
        "tools.web_tools_local.local_web_crawl",
        fake_local_web_crawl,
    )

    payload = json.loads(
        await web_tools.web_crawl_tool(
            "example.com/docs",
            depth="advanced",
            use_llm_processing=False,
        )
    )

    assert payload["results"] == [
        {
            "url": "https://example.com/docs",
            "title": "Documentation",
            "content": "Crawled locally",
            "error": None,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_web_crawl_blocks_private_url_before_local_crawler(monkeypatch):
    monkeypatch.setattr(web_tools, "_get_backend", lambda: "local")
    monkeypatch.setattr(web_tools, "check_auxiliary_model", lambda **_: False)
    monkeypatch.setattr(web_tools, "is_safe_url", lambda _url: False)

    def unexpected_crawl(*_args, **_kwargs):
        raise AssertionError("private URL must not reach the local crawler")

    monkeypatch.setattr(
        "tools.web_tools_local.local_web_crawl",
        unexpected_crawl,
    )

    payload = json.loads(
        await web_tools.web_crawl_tool(
            "http://127.0.0.1/private",
            use_llm_processing=False,
        )
    )

    assert "private or internal" in payload["results"][0]["error"]


@pytest.mark.unit
def test_local_web_extract_resolves_relative_links(monkeypatch):
    class FakeResponse:
        text = (
            "<html><head><title>Docs</title></head><body><main>"
            '<a href="/guide/start">Start</a>'
            '<a href="mailto:test@example.com">Mail</a>'
            "</main></body></html>"
        )

        def raise_for_status(self):
            return None

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, _url, headers):
            assert headers["User-Agent"]
            return FakeResponse()

    monkeypatch.setattr(
        web_tools_local.httpx,
        "Client",
        lambda **_kwargs: FakeClient(),
    )

    result = web_tools_local.local_web_extract(
        "https://example.com/docs/",
        extract_links=True,
    )

    assert result["links"] == [
        {"url": "https://example.com/guide/start", "text": "Start"}
    ]


@pytest.mark.unit
def test_local_web_extract_uses_matching_saved_account_cookie(monkeypatch):
    captured_headers = {}

    class FakeResponse:
        text = "<html><head><title>Private</title></head><body><main>content</main></body></html>"

        def raise_for_status(self):
            return None

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, _url, headers):
            captured_headers.update(headers)
            return FakeResponse()

    monkeypatch.setattr(
        "systems.supervisor.account_store.cookie_header_for_url",
        lambda url: "SESSDATA=local-session" if url == "https://www.bilibili.com/private" else "",
    )
    monkeypatch.setattr(web_tools_local.httpx, "Client", lambda **_kwargs: FakeClient())

    result = web_tools_local.local_web_extract("https://www.bilibili.com/private")

    assert result["title"] == "Private"
    assert captured_headers["Cookie"] == "SESSDATA=local-session"
