"""JSON schemas for Goal Manager Agent tools."""

from __future__ import annotations


def _schema(description: str, properties: dict, required: list[str] | None = None) -> dict:
    result = {"description": description, "parameters": {"type": "object", "properties": properties}}
    if required:
        result["parameters"]["required"] = required
    return result


COMMON_CONTEXT = {
    "actor_type": {"type": "string", "enum": ["user", "agent", "supervisor", "system"]},
    "actor_id": {"type": "string"},
    "session_id": {"type": "string"},
}


SCHEMAS = {
    "goal_project_get": _schema(
        "读取一个目标项目及其根节点和进度汇总。",
        {"projectId": {"type": "string"}, **COMMON_CONTEXT},
        ["projectId"],
    ),
    "goal_project_create": _schema(
        "创建目标项目并在同一事务中创建项目根节点。",
        {"name": {"type": "string"}, "description": {"type": "string"}, "reason": {"type": "string"},
         "createdBy": {"type": "string"}, "idempotencyKey": {"type": "string"}, **COMMON_CONTEXT},
        ["name", "reason"],
    ),
    "goal_get_context": _schema(
        "规划前置读取节点、直接子节点、依赖、阻塞、证据和最近事件。",
        {"nodeId": {"type": "string"}, **COMMON_CONTEXT},
        ["nodeId"],
    ),
    "goal_graph_query": _schema(
        "读取深度不超过 3 的目标子图。",
        {"projectId": {"type": "string"}, "startNode": {"type": "string"},
         "depth": {"type": "integer", "minimum": 0, "maximum": 3},
         "edgeTypes": {"type": "array", "items": {"type": "string"}}, **COMMON_CONTEXT},
        ["projectId", "startNode"],
    ),
    "goal_node_create": _schema(
        "创建一个目标节点；服务端会写入审计事件。",
        {"projectId": {"type": "string"}, "type": {"type": "string"},
         "title": {"type": "string"}, "description": {"type": "string"},
         "status": {"type": "string"}, "progress": {"type": "number", "minimum": 0, "maximum": 1},
         "progress_mode": {"type": "string"}, "priority": {"type": "integer"},
         "acceptance_criteria": {"type": "array", "items": {"type": "object"}},
         "createdBy": {"type": "string"}, "reason": {"type": "string"}, **COMMON_CONTEXT},
        ["projectId", "type", "title", "reason"],
    ),
    "goal_node_update": _schema(
        "按乐观锁版本更新目标节点。",
        {"nodeId": {"type": "string"}, "expectedVersion": {"type": "integer"},
         "patch": {"type": "object"}, "reason": {"type": "string"}, **COMMON_CONTEXT},
        ["nodeId", "expectedVersion", "patch", "reason"],
    ),
    "goal_node_delete": _schema(
        "软删除目标节点；删除根节点或大范围删除需要确认令牌。",
        {"nodeId": {"type": "string"}, "cascade": {"type": "boolean"},
         "confirmToken": {"type": "string"}, "reason": {"type": "string"}, **COMMON_CONTEXT},
        ["nodeId", "reason"],
    ),
    "goal_edge_create": _schema(
        "创建目标图边并由服务端检测环。",
        {"sourceId": {"type": "string"}, "targetId": {"type": "string"},
         "edgeType": {"type": "string", "enum": ["decomposes_to", "depends_on", "blocks"]},
         "progressWeight": {"type": "number", "minimum": 0},
         "required": {"type": "boolean"}, "reason": {"type": "string"}, **COMMON_CONTEXT},
        ["sourceId", "targetId", "edgeType", "reason"],
    ),
    "goal_edge_delete": _schema(
        "软删除一条目标图边。",
        {"edgeId": {"type": "string"}, "reason": {"type": "string"}, **COMMON_CONTEXT},
        ["edgeId", "reason"],
    ),
    "goal_batch_apply": _schema(
        "以原子事务批量拆解或修改目标，失败时整体回滚。",
        {"projectId": {"type": "string"}, "reason": {"type": "string"},
         "operations": {"type": "array", "items": {"type": "object"}},
         "confirmToken": {"type": "string"}, "createdBy": {"type": "string"}, **COMMON_CONTEXT},
        ["projectId", "reason", "operations"],
    ),
    "goal_rollback": _schema(
        "按 LIFO 回滚最近一个批次的目标修改。",
        {"batchId": {"type": "string"}, "reason": {"type": "string"},
         "confirm": {"type": "boolean"}, **COMMON_CONTEXT},
        ["batchId"],
    ),
    "goal_redo": _schema(
        "重放项目最近一次回滚的批次；如果回滚后产生了新写入则拒绝重做。",
        {"projectId": {"type": "string"}, "batchId": {"type": "string"},
         "reason": {"type": "string"}, **COMMON_CONTEXT},
        ["projectId"],
    ),
    "goal_next_actions": _schema(
        "按依赖满足、未阻塞、优先级和期限推荐下一步。",
        {"projectId": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100},
         "filters": {"type": "object"}, **COMMON_CONTEXT},
        ["projectId"],
    ),
    "goal_attach_evidence": _schema(
        "把测试、CI、Git、PR 或手工证据附加到目标节点。",
        {"nodeId": {"type": "string"},
         "evidenceType": {"type": "string", "enum": ["test_result", "ci_build", "git_commit", "pr", "issue", "note", "file", "manual"]},
         "title": {"type": "string"}, "content": {"type": "string"}, "uri": {"type": "string"},
         "createdBy": {"type": "string"}, "reason": {"type": "string"}, **COMMON_CONTEXT},
        ["nodeId", "evidenceType", "reason"],
    ),
}
