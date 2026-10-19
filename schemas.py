"""
schemas.py
----------
Strict data contracts for the "05 / Retest" agent, encoded from:
SentinelX AI / Network First - Shared Agent Contracts v1.0 (13 Oct 2026)

Two layers, exactly as the spec separates them:
  1. Common input/output  -> shared by every role in the pipeline.
  2. Role-specific input/output -> Retest's own fields (RetestInput / VerificationResult).

Nothing here invents fields that the contract doesn't define (rule #2 in the
spec: "Do not add arbitrary fields to the existing strict agent-result
envelope"). Runtime-assigned fields (schema_version, workspace_id, run_id,
environment_id, agent_id, model_call_id) are modeled separately in
RuntimeBinding and are never set by the model.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Shared enums
# --------------------------------------------------------------------------- #

class EnvironmentKind(str, Enum):
    REAL = "REAL"
    TWIN = "TWIN"
    BOTH = "BOTH"


class TaskStatus(str, Enum):
    DONE = "DONE"
    MORE_WORK = "MORE_WORK"
    BLOCKED = "BLOCKED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


class GateType(str, Enum):
    INPUT = "INPUT"
    AUTHORIZATION = "AUTHORIZATION"
    APPROVAL = "APPROVAL"
    REVIEW = "REVIEW"


class VerificationPurpose(str, Enum):
    PRE_APPLICATION = "PRE_APPLICATION"
    POST_CHANGE_RETEST = "POST_CHANGE_RETEST"


class Verdict(str, Enum):
    PASS_ = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


# --------------------------------------------------------------------------- #
# Common input (identical across all six roles)
# --------------------------------------------------------------------------- #

class CommonInput(BaseModel):
    workspace_id: str
    run_id: str
    task_id: str
    environment_id: str
    environment_kind: EnvironmentKind
    objective: str
    selected_stages: List[str] = Field(default_factory=list)
    scope_ref: str
    context_refs: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    available_capabilities: List[Dict[str, Any]] = Field(default_factory=list)
    remaining_budget: Dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# 05 / Retest - role-specific input
# --------------------------------------------------------------------------- #

class RelatedVariation(BaseModel):
    description: str
    source_or_identity: Optional[str] = None
    condition: Optional[str] = None


class AccessAndHealthContext(BaseModel):
    valid_identities: List[str] = Field(default_factory=list)
    assessment_sources: List[str] = Field(default_factory=list)
    tool_controls: List[str] = Field(default_factory=list)
    target_service_health: Optional[str] = None


class RetestRoleInput(BaseModel):
    original_finding_ref: str = Field(
        ..., description="Original accepted finding, proof method and evidence, "
                          "or a reviewed equivalent for an external finding."
    )
    change_ref: str = Field(
        ..., description="Exact proposed patch or observed applied change, "
                          "including customer-applied fixes."
    )
    current_target_state_ref: str = Field(
        ..., description="Current state of the environment to be tested."
    )
    independent_expectations: str = Field(
        ..., description="Reviewed expected security and functional behavior; "
                          "not solely tests authored by the Patch Agent."
    )
    related_variations: List[RelatedVariation] = Field(default_factory=list)
    access_and_health_context: AccessAndHealthContext
    verification_purpose: VerificationPurpose


class RetestInput(BaseModel):
    """Full input object the agent receives for one Retest task."""
    common: CommonInput
    role_input: RetestRoleInput


# --------------------------------------------------------------------------- #
# Common output (identical shape across all roles)
# --------------------------------------------------------------------------- #

class ProposedAction(BaseModel):
    capability_id: str
    capability_version: str
    stage: str
    asset_ids: List[str] = Field(default_factory=list)
    environment: EnvironmentKind
    parameter_ref: Optional[str] = None
    evidence_refs: List[str] = Field(default_factory=list)
    dependency_ids: List[str] = Field(default_factory=list)
    cleanup_plan_ref: Optional[str] = None


class CommonOutput(BaseModel):
    status: TaskStatus
    artifact_refs: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    proposed_actions: List[ProposedAction] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    coverage_gaps: List[str] = Field(default_factory=list)
    required_gate: Optional[GateType] = None
    concise_rationale: Optional[str] = None
    next_objective: Optional[str] = None


# --------------------------------------------------------------------------- #
# 05 / Retest - role-specific output: VerificationResult
# --------------------------------------------------------------------------- #

class CheckResult(BaseModel):
    check_id: str
    description: str
    outcome: str  # e.g. "REPRODUCIBLE" / "NOT_REPRODUCIBLE" / "PASS" / "FAIL" / "ERROR"
    evidence_refs: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class VerificationPlanItem(BaseModel):
    check_id: str
    registered_capability_id: str
    prerequisites: List[str] = Field(default_factory=list)
    independently_grounded_expectation: str


class ControlAndHealthResult(BaseModel):
    item: str
    status: str
    evidence_ref: Optional[str] = None


class VerificationResult(BaseModel):
    verification_plan: List[VerificationPlanItem] = Field(default_factory=list)
    security_check_results: List[CheckResult] = Field(default_factory=list)
    variation_check_results: List[CheckResult] = Field(default_factory=list)
    functional_check_results: List[CheckResult] = Field(default_factory=list)
    control_and_health_results: List[ControlAndHealthResult] = Field(default_factory=list)
    tested_state_ref: str
    verdict: Verdict
    verdict_reason: str
    closure_recommendation: str

    @field_validator("verdict_reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("verdict_reason must not be empty (contract: 'PASS / FAIL / "
                              "INCONCLUSIVE with reason').")
        return v


# --------------------------------------------------------------------------- #
# Runtime binding (assigned by the trusted runtime, never by the model)
# --------------------------------------------------------------------------- #

class RuntimeBinding(BaseModel):
    schema_version: str
    workspace_id: str
    run_id: str
    environment_id: str
    agent_id: str
    model_call_id: str


# --------------------------------------------------------------------------- #
# Final stored agent result envelope
# --------------------------------------------------------------------------- #

class RetestAgentResult(BaseModel):
    runtime: RuntimeBinding
    common_output: CommonOutput
    verification_result: VerificationResult
