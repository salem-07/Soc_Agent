"""
schemas.py
----------
Typed contracts for the SentinelX "05 / Retest" agent, mirrored field-for-field
from "SentinelX AI | Agent input/output reference v1.0" (Network First spec).

Design notes
~~~~~~~~~~~~
* The spec explicitly says: "Role-specific output records are separate
  artifacts... the team must encode and validate the detailed types before
  implementation." Several fields are therefore *references* to records that
  live in other services (scope service, evidence store, capability
  registry, etc.). We model those as `Ref` = a free-form dict that must at
  least carry a `ref_id`, so this agent stays forward-compatible with
  whatever exact backing schema the Orchestrator/runtime team finalizes.

* Per the "Contract and runtime rules" section:
    1. The model PROPOSES. Deterministic services AUTHORIZE, PERSIST,
       DISPATCH, assign record IDs and decide authoritative states.
    2. "Runtime binding: schema_version, workspace_id, run_id,
       environment_id, agent_id and model_call_id accompany every stored
       result. The trusted runtime assigns or verifies these identifiers;
       the model cannot change them."

  So `RetestResult` produced by this agent is a **draft** proposal only.
  It intentionally does NOT set the runtime-binding envelope fields -- the
  Orchestrator/runtime attaches and verifies those after persistence.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Generic building blocks
# ---------------------------------------------------------------------------

class Ref(BaseModel):
    """A reference to a record owned by another service (scope, evidence,
    inventory, capability, etc). Kept intentionally loose -- this agent
    never resolves these itself, it only passes them through / cites them.
    """

    ref_id: str
    kind: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


EnvironmentKind = Literal["REAL", "TWIN", "BOTH"]
Status = Literal["DONE", "MORE_WORK", "BLOCKED", "NEEDS_REVIEW", "FAILED"]
Gate = Literal["INPUT", "AUTHORIZATION", "APPROVAL", "REVIEW"]
Verdict = Literal["PASS", "FAIL", "INCONCLUSIVE"]
VerificationPurpose = Literal["PRE_APPLICATION", "POST_CHANGE_RETEST"]


# ---------------------------------------------------------------------------
# Common input / output (shared by every role, per the spec's first page)
# ---------------------------------------------------------------------------

class RemainingBudget(BaseModel):
    time_seconds: Optional[float] = None
    operation_count: Optional[int] = None
    model_tokens: Optional[int] = None
    model_cost: Optional[float] = None


class CommonInput(BaseModel):
    workspace_id: str
    run_id: str
    task_id: str

    environment_id: str
    environment_kind: EnvironmentKind

    objective: str
    selected_stages: list[str] = Field(default_factory=list)

    scope_ref: Ref
    context_refs: list[Ref] = Field(default_factory=list)
    evidence_refs: list[Ref] = Field(default_factory=list)

    available_capabilities: list[dict[str, Any]] = Field(default_factory=list)
    remaining_budget: RemainingBudget = Field(default_factory=RemainingBudget)


class ProposedAction(BaseModel):
    capability_id: str
    version: str
    stage: Optional[str] = None
    asset_ids: list[str] = Field(default_factory=list)
    environment: Optional[str] = None
    parameter_ref: Optional[Ref] = None
    evidence_refs: list[Ref] = Field(default_factory=list)
    dependency_ids: list[str] = Field(default_factory=list)
    cleanup_plan_ref: Optional[Ref] = None


class CommonOutput(BaseModel):
    status: Status
    artifact_refs: list[Ref] = Field(default_factory=list)
    evidence_refs: list[Ref] = Field(default_factory=list)
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
    required_gate: Optional[Gate] = None
    concise_rationale: Optional[str] = None
    next_objective: Optional[str] = None


# ---------------------------------------------------------------------------
# Role-specific input: "05 / Retest" (owner: Salem)
# ---------------------------------------------------------------------------

class RetestRoleInput(BaseModel):
    original_finding_ref: Ref
    change_ref: Ref
    current_target_state_ref: Ref
    independent_expectations: dict[str, Any]
    related_variations: list[dict[str, Any]] = Field(default_factory=list)
    access_and_health_context: dict[str, Any]
    verification_purpose: VerificationPurpose


class RetestTaskInput(CommonInput, RetestRoleInput):
    """What the Orchestrator / Patch role hands to this agent for one
    Retest task (common envelope + role-specific fields, per the
    "Patch -> Verification / Apply" and "Applied change -> Retest"
    handoff rules).
    """


# ---------------------------------------------------------------------------
# Role-specific output: "VerificationResult"
# ---------------------------------------------------------------------------

class CheckResult(BaseModel):
    check_id: str
    capability_id: Optional[str] = None
    target_ref: Optional[Ref] = None
    outcome: Literal["REPRODUCIBLE", "NOT_REPRODUCIBLE", "PASS", "FAIL", "ERROR", "SKIPPED"]
    detail: str
    evidence_refs: list[Ref] = Field(default_factory=list)
    observed_at: Optional[str] = None


class VerificationPlanStep(BaseModel):
    step_id: str
    capability_id: str
    version: str
    category: Literal["security", "variation", "functional", "control_and_health"]
    target_ref: Optional[Ref] = None
    params: dict[str, Any] = Field(default_factory=dict)
    prerequisites: list[str] = Field(default_factory=list)
    expected_pass_condition: str


class VerdictBlock(BaseModel):
    result: Verdict
    reason: str


class ClosureRecommendation(BaseModel):
    recommend_closure: bool
    reasoning: str
    follow_up: Optional[str] = None


class RetestRoleOutput(BaseModel):
    verification_plan: list[VerificationPlanStep] = Field(default_factory=list)
    security_check_results: list[CheckResult] = Field(default_factory=list)
    variation_check_results: list[CheckResult] = Field(default_factory=list)
    functional_check_results: list[CheckResult] = Field(default_factory=list)
    control_and_health_results: list[CheckResult] = Field(default_factory=list)
    tested_state_ref: Ref
    verdict: VerdictBlock
    closure_recommendation: ClosureRecommendation


class RetestResult(CommonOutput, RetestRoleOutput):
    """Draft agent-result for the Retest role. The Orchestrator/runtime
    wraps this with the binding envelope (schema_version, workspace_id,
    run_id, environment_id, agent_id, model_call_id) before persisting it
    as the authoritative stored result.
    """
