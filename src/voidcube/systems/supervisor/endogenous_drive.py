from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .endogenous_candidate_pipeline import EndogenousTaskCandidate
from .endogenous_materialization import (
    materialize_lm_proposals_for_deliberation,
)
from .endogenous_candidate_stream import (
    assemble_prepared_candidate_stream,
    prepare_candidate_stream,
)
from .endogenous_drive_models import DriveDeliberationReport
from .endogenous_drive_context import (
    build_drive_context,
    get_shell_slot_meta,
    normalize_drive_input,
)
from .endogenous_deliberation import (
    build_deliberation_report as build_deliberation_report_projection,
)
from .endogenous_lm_evidence import (
    build_lm_evidence_packet_from_runtime_config,
)
from .endogenous_cognitive_posture_context import (
    build_cognitive_posture_context,
)
from .endogenous_meta_cognition import (
    build_proposal_drift_memory,
)
from .endogenous_self_model import build_recent_reference_alignment
from .endogenous_research import build_external_research_evidence
from .endogenous_shell_profile import build_shell_body_profile
from .endogenous_evidence import normalize_recent_learning_evidence
from .endogenous_proposals import (
    execute_lm_task_generation_from_runtime_config,
    is_lm_task_generation_enabled,
)
from .endogenous_generation_state import LmGenerationStateOwner
_API_B_JUDGEMENT_BLOCKAGE = "api_b_judgement_blockage"


class EndogenousDriveEngine:
    """Supervisor drive loop — deterministic core + optional LLM intelligence.

    The drive engine does not execute work. It turns system facts, core values,
    and (when available) LLM-analyzed memory context into auditable
    API-B judgement projections that still pass through supervisor review.

    Without LLM: uses deterministic text extraction (first 80 chars).
    With LLM: reads compressed memory context to generate intelligent,
    context-aware learning topics.
    """

    def __init__(
        self,
        config: Any | None = None,
        *,
        generation_state: LmGenerationStateOwner | None = None,
    ) -> None:
        self.config = config
        self._generation_state = generation_state or LmGenerationStateOwner()

    def get_latest_lm_task_generation_state(self) -> Dict[str, Any]:
        return self._generation_state.snapshot()

    def resolve_cognitive_posture_state(
        self,
        *,
        drive_input: Optional[Dict[str, Any]] = None,
        deliberation_dict: Dict[str, Any],
    ) -> Dict[str, Any]:
        drive_input = normalize_drive_input(drive_input)
        drive_context = build_drive_context(drive_input)
        runtime_config = getattr(self.config, "service_runtime", None)
        charter_model = getattr(runtime_config, "endogenous_drive_cognition_charter", None)
        policy_model = getattr(charter_model, "cognitive_control_policy", None)
        if hasattr(policy_model, "model_dump"):
            policy = policy_model.model_dump(mode="json")
        else:
            policy = dict(policy_model or {})

        recent_reference_alignment = build_recent_reference_alignment(drive_context)
        proposal_drift_memory = build_proposal_drift_memory(drive_context)
        recent_learning_evidence = normalize_recent_learning_evidence(
            list(drive_context.get("completed_learning_tasks") or [])
        )
        service_runtime = getattr(self.config, "service_runtime", None)
        execution_config = getattr(self.config, "execution", None)
        external_research_evidence = build_external_research_evidence(
            enabled=bool(
                getattr(service_runtime, "endogenous_drive_external_research_enabled", False)
            ),
            entries=list(
                getattr(service_runtime, "endogenous_drive_external_research_entries", [])
                or []
            ),
            file_entries=list(
                getattr(service_runtime, "endogenous_drive_external_research_files", [])
                or []
            ),
            repo_root=getattr(execution_config, "git_repo_path", "./") or "./",
        )
        shell_slot = get_shell_slot_meta(drive_input)
        shell_body_profile = build_shell_body_profile(shell_slot)
        posture_context = build_cognitive_posture_context(
            policy=policy,
            deliberation_dict=deliberation_dict,
            drive_history=dict(drive_context.get("drive_history") or {}),
            recent_learning_evidence=recent_learning_evidence,
            external_research_evidence=external_research_evidence,
            shell_body_profile=shell_body_profile,
            recent_reference_alignment=recent_reference_alignment,
            proposal_drift_memory=proposal_drift_memory,
        )
        return dict(posture_context.get("cognitive_posture") or {})

    def generate_candidates(
        self,
        *,
        drive_input: Optional[Dict[str, Any]] = None,
        existing_drive_keys: Iterable[str],
        max_candidates: int = 3,
        deliberation_report: DriveDeliberationReport | None = None,
        lm_proposals_override: Optional[List[Dict[str, Any]]] = None,
    ) -> List[EndogenousTaskCandidate]:
        drive_input = normalize_drive_input(drive_input)
        existing_keys = set(existing_drive_keys)
        candidates = self._candidate_stream(
            drive_input,
            existing_keys=existing_keys,
            deliberation_report=deliberation_report,
            lm_proposals_override=lm_proposals_override,
        )
        candidates.sort(key=lambda candidate: candidate.utility, reverse=True)
        return candidates[:max(max_candidates, 0)]

    def build_deliberation_report(
        self,
        *,
        drive_input: Optional[Dict[str, Any]] = None,
    ) -> DriveDeliberationReport:
        return build_deliberation_report_projection(
            drive_input=normalize_drive_input(drive_input),
        )

    def _candidate_stream(
        self,
        drive_input: Dict[str, Any],
        *,
        existing_keys: set[str] = None,
        deliberation_report: DriveDeliberationReport | None = None,
        lm_proposals_override: Optional[List[Dict[str, Any]]] = None,
    ) -> List[EndogenousTaskCandidate]:
        if existing_keys is None:
            existing_keys = set()
        preparation = prepare_candidate_stream(
            drive_input=drive_input,
            existing_keys=existing_keys,
            deliberation_report=deliberation_report,
        )
        lm_candidates = self._llm_task_proposals(
            drive_input=drive_input,
            existing_keys=existing_keys,
            deliberation=preparation["deliberation"],
            drive_context=preparation["drive_context"],
            memory_plan=preparation["memory_plan"],
            self_learning_plan=preparation["self_learning_plan"],
            autonomous_improvement_plan=preparation[
                "autonomous_improvement_plan"
            ],
            proposals_override=lm_proposals_override,
        )
        return assemble_prepared_candidate_stream(
            preparation=preparation,
            lm_candidates=lm_candidates,
        )

    def _llm_task_proposals(
        self,
        *,
        drive_input: Optional[Dict[str, Any]] = None,
        existing_keys: set[str],
        deliberation: DriveDeliberationReport,
        drive_context: Dict[str, Any],
        memory_plan: Dict[str, Any],
        self_learning_plan: Dict[str, Any],
        autonomous_improvement_plan: Dict[str, Any],
        proposals_override: Optional[List[Dict[str, Any]]] = None,
    ) -> List[EndogenousTaskCandidate]:
        drive_input = normalize_drive_input(drive_input)
        service_runtime = getattr(self.config, "service_runtime", None)
        if not is_lm_task_generation_enabled(service_runtime):
            return []

        evidence_packet = build_lm_evidence_packet_from_runtime_config(
            runtime_config=service_runtime,
            execution_config=getattr(self.config, "execution", None),
            drive_input=drive_input,
            deliberation=deliberation,
            drive_context=drive_context,
            memory_plan=memory_plan,
            self_learning_plan=self_learning_plan,
            autonomous_improvement_plan=autonomous_improvement_plan,
        )
        if proposals_override is None:
            execution = execute_lm_task_generation_from_runtime_config(
                evidence_packet=evidence_packet,
                runtime_config=service_runtime,
            )
            self._generation_state.record(
                context_snapshot=execution.context_snapshot,
                proposals=execution.proposals,
            )
            proposals = execution.proposals
        else:
            proposals = [dict(item) for item in proposals_override if isinstance(item, dict)]
        if not proposals:
            return []
        generation_state = self._generation_state.snapshot()
        return materialize_lm_proposals_for_deliberation(
            proposals=proposals,
            existing_keys=existing_keys,
            deliberation=deliberation,
            drive_context=drive_context,
            evidence_packet=evidence_packet,
            cognitive_assessment=dict(generation_state["context"]).get(
                "cognitive_assessment"
            ),
        )

