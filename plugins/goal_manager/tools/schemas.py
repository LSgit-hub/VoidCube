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
        "仅在当前会话没有已绑定项目时创建项目；已有 Goal Manager project_id 时禁止调用本工具，必须复用现有根节点。",
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
    "goal_intent_contract_set": _schema(
        "记录目标意图契约：结果、成功标准、范围、约束、假设和待澄清问题。",
        {"projectId": {"type": "string"}, "outcome": {"type": "string"},
         "successCriteria": {"type": "array", "items": {"type": "string"}},
         "scope": {"type": "array", "items": {"type": "string"}},
         "constraints": {"type": "array", "items": {"type": "string"}},
         "assumptions": {"type": "array", "items": {"type": "string"}},
         "openQuestions": {"type": "array", "items": {"type": "string"}},
         "reason": {"type": "string"}, **COMMON_CONTEXT},
        ["projectId", "outcome", "reason"],
    ),
    "goal_protocol_next_action": _schema(
        "读取目标协议建议：澄清、调查、执行、阻塞、重规划或完成。",
        {"projectId": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100},
         **COMMON_CONTEXT},
        ["projectId"],
    ),
    "goal_plan_review": _schema(
        "审查当前计划是否具备意图契约、验收条件和可执行下一步。",
        {"projectId": {"type": "string"}, **COMMON_CONTEXT},
        ["projectId"],
    ),
    "goal_replan": _schema(
        "创建一次重规划版本，记录当前计划快照和相对上一版本的差异。",
        {"projectId": {"type": "string"}, "reason": {"type": "string"}, **COMMON_CONTEXT},
        ["projectId", "reason"],
    ),
    "goal_lifecycle_get": _schema(
        "读取节点执行生命周期：执行结果、观察、证据核验和结果验收。",
        {"nodeId": {"type": "string"}, **COMMON_CONTEXT},
        ["nodeId"],
    ),
    "goal_record_execution_result": _schema(
        "记录一次执行动作的结果；不自动完成目标。",
        {"nodeId": {"type": "string"}, "status": {"type": "string", "enum": ["succeeded", "failed", "partial"]},
         "summary": {"type": "string"}, "outputs": {"oneOf": [{"type": "array"}, {"type": "object"}]},
         "reason": {"type": "string"}, **COMMON_CONTEXT},
        ["nodeId", "status", "summary", "reason"],
    ),
    "goal_record_observation": _schema(
        "记录执行后的观察信号，可关联执行结果。",
        {"nodeId": {"type": "string"}, "executionResultId": {"type": "string"},
         "summary": {"type": "string"}, "signals": {"oneOf": [{"type": "array"}, {"type": "object"}]},
         "reason": {"type": "string"}, **COMMON_CONTEXT},
        ["nodeId", "summary", "reason"],
    ),
    "goal_verify_evidence": _schema(
        "写入证据核验记录；通过后仍需显式应用到验收条件。",
        {"nodeId": {"type": "string"}, "evidenceId": {"type": "string"}, "accepted": {"type": "boolean"},
         "summary": {"type": "string"}, "criterionIndex": {"type": "integer", "minimum": 0},
         "reason": {"type": "string"}, **COMMON_CONTEXT},
        ["nodeId", "accepted", "summary", "reason"],
    ),
    "goal_apply_evidence_verification": _schema(
        "按节点版本锁把已通过的核验记录应用到对应验收条件。",
        {"nodeId": {"type": "string"}, "verificationId": {"type": "string"},
         "expectedVersion": {"type": "integer"}, "reason": {"type": "string"}, **COMMON_CONTEXT},
        ["nodeId", "verificationId", "expectedVersion", "reason"],
    ),
    "goal_submit_for_review": _schema(
        "在所有完成门槛满足后，把节点提交到人工审核状态。",
        {"nodeId": {"type": "string"}, "expectedVersion": {"type": "integer"},
         "reason": {"type": "string"}, **COMMON_CONTEXT},
        ["nodeId", "expectedVersion", "reason"],
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
