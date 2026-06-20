from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Protocol

from agent.skill_utils import parse_frontmatter


class SelfLearningToolRunnerProtocol(Protocol):
    def run_tool(self, name: str, args: Dict[str, Any], *, task_id: str) -> str: ...


class SelfLearningToolRunner:
    """Executor-side bounded runner for self-learning evidence collection."""

    ALLOWED_TOOLS = frozenset({"web_search", "search_files", "read_file"})

    def run_tool(self, name: str, args: Dict[str, Any], *, task_id: str) -> str:
        if name not in self.ALLOWED_TOOLS:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Tool '{name}' is not allowed for self-learning evidence collection.",
                },
                ensure_ascii=False,
            )

        from tools.model_tools import handle_function_call

        return handle_function_call(
            name,
            dict(args),
            task_id=task_id,
            user_task="self-learning evidence collection",
            enabled_tools=sorted(self.ALLOWED_TOOLS),
        )


class SelfLearningSkillDelegate:
    """Runs the bundled self-learning skill through a bounded executor-side tool runner."""

    def __init__(
        self,
        skill_dir: str | Path | None = None,
        *,
        tool_runner: SelfLearningToolRunnerProtocol | None = None,
        max_search_queries: int = 2,
        max_local_searches: int = 2,
        max_reference_files: int = 3,
    ) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        self.skill_dir = Path(skill_dir or repo_root / "skills" / "self-learning").resolve()
        self.tool_runner = tool_runner or SelfLearningToolRunner()
        self.max_search_queries = max(0, int(max_search_queries))
        self.max_local_searches = max(0, int(max_local_searches))
        self.max_reference_files = max(0, int(max_reference_files))

    def execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        task = dict(request.get("task") or {})
        evidence = dict(task.get("evidence") or {})
        constraints = dict(task.get("constraints") or {})
        task_id = str(task.get("task_id") or request.get("task_id") or "self-learning")
        topic = str(task.get("title") or request.get("title") or "Self-learning follow-up").strip()
        summary = str(task.get("summary") or request.get("summary") or "").strip()
        planned_minutes = int(constraints.get("planned_minutes", 20))

        skill_md = self.skill_dir / "SKILL.md"
        guide_path = self.skill_dir / "references" / "technology-evaluation-guide.md"
        summary_template_path = self.skill_dir / "templates" / "learning-session-summary.md"

        if not skill_md.exists():
            raise FileNotFoundError(f"Self-learning skill not found: {skill_md}")

        raw_skill = skill_md.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(raw_skill)
        guide_excerpt = self._read_excerpt(guide_path)
        summary_template_excerpt = self._read_excerpt(summary_template_path)
        search_queries = self._build_search_queries(topic)
        evaluation_dimensions = self._evaluation_dimensions(guide_excerpt)
        evidence_plan = self._build_evidence_plan(
            search_queries,
            evidence=evidence,
            constraints=constraints,
        )
        tool_execution = self._run_evidence_tools(
            evidence_plan,
            task_id=task_id,
        )
        observations = [
            f"Loaded bundled self-learning skill '{frontmatter.get('name', 'self-learning')}'.",
            f"Prepared {len(search_queries)} search query variant(s) for topic '{topic}'.",
            "Prepared technology evaluation dimensions from the skill reference guide.",
            "Prepared learning session summary template for conclusion writeback.",
            self._tool_execution_observation(tool_execution),
        ]
        if summary:
            observations.append(summary)
        for observation in evidence.get("observations") or []:
            observations.append(str(observation))

        web_searches = [
            call
            for call in tool_execution["calls"]
            if call["tool"] == "web_search" and call["success"]
        ]

        return {
            "status": "skill_delegate_executed",
            "delegate": "SelfLearningSkillDelegate",
            "backend": "bounded_tool_runner",
            "skill": {
                "name": str(frontmatter.get("name") or "self-learning"),
                "version": str(frontmatter.get("version") or ""),
                "description": str(frontmatter.get("description") or ""),
                "path": str(skill_md),
                "guide_path": str(guide_path),
                "summary_template_path": str(summary_template_path),
            },
            "learning_plan": {
                "topic": topic,
                "summary": summary,
                "planned_minutes": planned_minutes,
                "search_queries": search_queries,
                "evaluation_dimensions": evaluation_dimensions,
                "evidence_plan": evidence_plan,
                "note_targets": ["core", "archive", "references", "learning-history"],
            },
            "evidence": {
                "skill_body_excerpt": self._first_nonempty_lines(body, limit=5),
                "guide_excerpt": guide_excerpt,
                "summary_template_excerpt": summary_template_excerpt,
                "observations": observations,
                "comparisons": [
                    "bundled self-learning skill guidance",
                    "bounded executor-side tool runner",
                    "ad-hoc learn-only recorder",
                ],
            },
            "tool_execution": tool_execution,
            "capability_boundary": {
                "uses_agent_skill_contract": True,
                "performs_external_search": bool(web_searches),
                "performs_body_mutation": False,
                "performs_memory_mutation": False,
            },
        }

    def _run_evidence_tools(
        self,
        evidence_plan: Dict[str, Any],
        *,
        task_id: str,
    ) -> Dict[str, Any]:
        calls: List[Dict[str, Any]] = []
        planned_calls = list(evidence_plan.get("calls") or [])
        for planned in planned_calls:
            calls.append(self._run_tool_call(planned, task_id=task_id))

        succeeded = sum(1 for call in calls if call["success"])
        failed = len(calls) - succeeded
        return {
            "status": (
                "completed"
                if calls and failed == 0
                else "partial"
                if succeeded
                else "failed"
                if calls
                else "skipped"
            ),
            "allowed_tools": sorted(SelfLearningToolRunner.ALLOWED_TOOLS),
            "evidence_plan": evidence_plan,
            "planned_calls": planned_calls,
            "calls": calls,
            "summary": {
                "total": len(calls),
                "succeeded": succeeded,
                "failed": failed,
            },
        }

    def _build_evidence_plan(
        self,
        search_queries: List[str],
        *,
        evidence: Dict[str, Any],
        constraints: Dict[str, Any],
    ) -> Dict[str, Any]:
        requested_tools = self._requested_tools(constraints)
        allowed_tools = set(SelfLearningToolRunner.ALLOWED_TOOLS)
        enabled_tools = [tool for tool in requested_tools if tool in allowed_tools]
        rejected_tools = [tool for tool in requested_tools if tool not in allowed_tools]
        calls: List[Dict[str, Any]] = []

        if "web_search" in enabled_tools:
            calls.extend(
                {
                    "tool": "web_search",
                    "source_type": "external_web",
                    "purpose": "collect current external evidence",
                    "args": {"query": query},
                }
                for query in search_queries[: self.max_search_queries]
            )

        if "search_files" in enabled_tools:
            local_patterns = self._string_list(
                constraints.get("local_search_patterns")
                or constraints.get("repo_search_patterns")
                or evidence.get("local_search_patterns")
                or evidence.get("repo_search_patterns")
            )
            local_search_path = str(
                constraints.get("local_search_path")
                or evidence.get("local_search_path")
                or "."
            )
            calls.extend(
                {
                    "tool": "search_files",
                    "source_type": "local_repository",
                    "purpose": "collect local repository evidence",
                    "args": {
                        "pattern": pattern,
                        "path": local_search_path,
                        "limit": 20,
                        "output_mode": "content",
                    },
                }
                for pattern in local_patterns[: self.max_local_searches]
            )

        if "read_file" in enabled_tools:
            reference_files = self._string_list(
                constraints.get("reference_files")
                or constraints.get("reference_paths")
                or evidence.get("reference_files")
                or evidence.get("reference_paths")
            )
            calls.extend(
                {
                    "tool": "read_file",
                    "source_type": "local_reference",
                    "purpose": "read task-provided reference evidence",
                    "args": {
                        "path": path,
                        "offset": 1,
                        "limit": 160,
                    },
                }
                for path in reference_files[: self.max_reference_files]
            )

        source_mix = sorted(
            {
                str(call.get("source_type"))
                for call in calls
                if call.get("source_type")
            }
        )
        return {
            "status": "planned" if calls else "skipped",
            "policy": {
                "allowed_tools": sorted(allowed_tools),
                "requested_tools": requested_tools,
                "enabled_tools": enabled_tools,
                "rejected_tools": rejected_tools,
                "max_search_queries": self.max_search_queries,
                "max_local_searches": self.max_local_searches,
                "max_reference_files": self.max_reference_files,
            },
            "source_mix": source_mix,
            "calls": calls,
        }

    def _requested_tools(self, constraints: Dict[str, Any]) -> List[str]:
        requested = self._string_list(constraints.get("evidence_tools"))
        if not requested:
            requested = sorted(SelfLearningToolRunner.ALLOWED_TOOLS)
        seen: set[str] = set()
        result: List[str] = []
        for tool in requested:
            if tool in seen:
                continue
            seen.add(tool)
            result.append(tool)
        return result

    def _run_tool_call(self, planned_call: Dict[str, Any], *, task_id: str) -> Dict[str, Any]:
        name = str(planned_call.get("tool") or "")
        args = dict(planned_call.get("args") or {})
        base_record = {
            "tool": name,
            "args": args,
            "source_type": planned_call.get("source_type"),
            "purpose": planned_call.get("purpose"),
        }
        try:
            raw_result = self.tool_runner.run_tool(name, args, task_id=task_id)
        except Exception as exc:
            return {
                **base_record,
                "success": False,
                "result": None,
                "raw_result": "",
                "error": str(exc),
            }

        parsed = self._parse_tool_result(raw_result)
        return {
            **base_record,
            "success": self._tool_result_success(parsed),
            "result": self._summarize_tool_result(parsed),
            "raw_result": self._truncate(str(raw_result), limit=4000),
            "error": self._tool_result_error(parsed),
        }

    def _parse_tool_result(self, raw_result: Any) -> Any:
        if not isinstance(raw_result, str):
            return raw_result
        try:
            return json.loads(raw_result)
        except json.JSONDecodeError:
            return raw_result

    def _tool_result_success(self, result: Any) -> bool:
        if isinstance(result, dict):
            if result.get("success") is False or result.get("error"):
                return False
            return True
        return bool(result)

    def _tool_result_error(self, result: Any) -> str | None:
        if not isinstance(result, dict):
            return None
        error = result.get("error")
        if error:
            return str(error)
        return None

    def _summarize_tool_result(self, result: Any) -> Any:
        if isinstance(result, dict):
            if "data" in result and isinstance(result["data"], dict):
                web_results = result["data"].get("web")
                if isinstance(web_results, list):
                    return {
                        "web": [
                            {
                                "title": str(item.get("title") or ""),
                                "url": str(item.get("url") or ""),
                                "description": self._truncate(str(item.get("description") or ""), limit=500),
                            }
                            for item in web_results[:5]
                            if isinstance(item, dict)
                        ]
                    }
            if "matches" in result and isinstance(result["matches"], list):
                return {
                    "matches": result["matches"][:10],
                    "truncated": bool(result.get("truncated", False)),
                }
            return result
        if isinstance(result, str):
            return self._truncate(result, limit=1000)
        return result

    def _tool_execution_observation(self, tool_execution: Dict[str, Any]) -> str:
        summary = tool_execution["summary"]
        return (
            "Executed bounded self-learning tool runner: "
            f"{summary['succeeded']} succeeded, {summary['failed']} failed, "
            f"{summary['total']} total."
        )

    def _truncate(self, text: str, *, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def _string_list(self, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, list):
            values = value
        else:
            return []
        result: List[str] = []
        for item in values:
            text = str(item).strip()
            if text:
                result.append(text)
        return result

    def _build_search_queries(self, topic: str) -> List[str]:
        base = topic or "self-learning follow-up"
        return [
            f"{base} latest trends 2026",
            f"{base} best practices 2025 2026",
            f"{base} production ready GitHub",
            f"{base} state of the art",
        ]

    def _evaluation_dimensions(self, guide_excerpt: str) -> List[Dict[str, Any]]:
        known = [
            ("practicality", 30),
            ("cutting_edge", 20),
            ("maturity", 20),
            ("learning_cost", 15),
            ("long_term_value", 15),
        ]
        return [
            {
                "name": name,
                "weight": weight,
                "source": "technology-evaluation-guide.md" if guide_excerpt else "default",
            }
            for name, weight in known
        ]

    def _read_excerpt(self, path: Path, *, limit: int = 8) -> str:
        if not path.exists():
            return ""
        return "\n".join(self._first_nonempty_lines(path.read_text(encoding="utf-8"), limit=limit))

    def _first_nonempty_lines(self, text: str, *, limit: int) -> List[str]:
        lines: List[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            lines.append(stripped)
            if len(lines) >= limit:
                break
        return lines
