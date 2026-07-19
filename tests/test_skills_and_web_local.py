import json

import pytest

import agent.skill_utils as skill_utils
import tools.skills_tool as skills_tool
import tools.web_tools as web_tools
import tools.web_tools_local as web_tools_local


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
