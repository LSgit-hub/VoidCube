import json

from systems.supervisor.endogenous_research import build_external_research_evidence


def test_external_research_evidence_is_disabled_without_loading_files(tmp_path):
    source = tmp_path / "research.json"
    source.write_text(json.dumps({"title": "ignored"}), encoding="utf-8")

    result = build_external_research_evidence(
        enabled=False,
        entries=[],
        file_entries=[str(source)],
        repo_root=tmp_path,
    )

    assert result == []


def test_external_research_evidence_normalizes_entries_and_relative_files(tmp_path):
    source = tmp_path / "research.json"
    source.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "title": "File research",
                        "summary": "A grounded file summary",
                        "source": "file-source",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = build_external_research_evidence(
        enabled=True,
        entries=[
            "Inline research::An inline summary",
        ],
        file_entries=["research.json"],
        repo_root=tmp_path,
    )

    assert [item["title"] for item in result] == ["Inline research", "File research"]
    assert result[0]["source"] == "configured_external_research"
    assert result[1]["source"] == "file-source"
    assert result[1]["source_path"].endswith("research.json")
