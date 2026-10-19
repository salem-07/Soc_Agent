"""
agent.py
--------
The "05 / Retest" agent itself.

Pipeline for one task:
  1. Validate the incoming task_input against RetestInput (fail fast, loudly,
     on malformed/missing fields - never silently guess).
  2. Build the role prompt and call Gemini for a draft VerificationResult.
  3. Validate Gemini's draft against the strict output schema.
  4. Enforce the handoff/contract rules in code (not just in the prompt),
     because per the spec "the model proposes; deterministic services
     authorize" - a model that violates a hard rule gets corrected or
     escalated here, not trusted blindly.
  5. Attach runtime-assigned fields (schema_version, ids, model_call_id) -
     these are never produced by the model.
  6. Return the final RetestAgentResult, ready to hand back to the
     Orchestrator / Reporting & Memory roles exactly as artifact_refs /
     evidence_refs per the contract.

This module has no knowledge of HOW task_input arrives (file, queue, HTTP) -
see main.py / server.py for that. That keeps it a pure, testable function the
rest of the SentinelX project can import directly.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from pydantic import ValidationError

from gemini_client import GeminiClient, GeminiCallError
from prompts import SYSTEM_PROMPT, build_user_prompt
from schemas import (
    CommonOutput,
    GateType,
    RetestAgentResult,
    RetestInput,
    RuntimeBinding,
    TaskStatus,
    Verdict,
    VerificationResult,
)

AGENT_ID = "retest-agent"
SCHEMA_VERSION = "1.0"


class RetestAgentError(RuntimeError):
    """Raised when a task cannot be processed at all (bad input, no API key, etc.)."""


class RetestAgent:
    def __init__(self, gemini_client: GeminiClient | None = None) -> None:
        self._gemini = gemini_client or GeminiClient()

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #

    def handle_task(self, raw_task_input: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main entry point. Takes the raw dict handed over by the Orchestrator
        (common input + Retest role-specific input, per the contract) and
        returns the final result dict ready to persist / hand off.

        Never requires anything extra from a human - every decision needed
        to produce a result is either in raw_task_input or is reported back
        as a BLOCKED/NEEDS_REVIEW status with an explicit required_gate,
        exactly as the contract specifies for missing facts (rule #5).
        """
        task_input = self._validate_input(raw_task_input)

        try:
            draft = self._gemini.generate_json(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_user_prompt(task_input),
            )
        except GeminiCallError as exc:
            # The model couldn't be reached / didn't answer usably. This is a
            # tool/runtime failure, not a security verdict - report FAILED,
            # never silently invent a PASS/FAIL.
            return self._failure_envelope(task_input, reason=str(exc))

        common_output, verification_result = self._validate_output(draft)
        common_output, verification_result = self._enforce_contract_rules(
            task_input, common_output, verification_result
        )

        result = RetestAgentResult(
            runtime=self._runtime_binding(task_input),
            common_output=common_output,
            verification_result=verification_result,
        )
        return result.model_dump(mode="json")

    # ------------------------------------------------------------------ #
    # Input validation
    # ------------------------------------------------------------------ #

    @staticmethod
    def _validate_input(raw: Dict[str, Any]) -> RetestInput:
        try:
            return RetestInput.model_validate(raw)
        except ValidationError as exc:
            raise RetestAgentError(
                f"Invalid task input for Retest agent - refusing to guess "
                f"missing/malformed fields:\n{exc}"
            ) from exc

    # ------------------------------------------------------------------ #
    # Output validation
    # ------------------------------------------------------------------ #

    @staticmethod
    def _validate_output(draft: Dict[str, Any]):
        try:
            common_output = CommonOutput.model_validate(draft["common_output"])
            verification_result = VerificationResult.model_validate(
                draft["verification_result"]
            )
        except (KeyError, ValidationError) as exc:
            raise RetestAgentError(
                f"Gemini draft did not match the required VerificationResult "
                f"contract shape:\n{exc}"
            ) from exc
        return common_output, verification_result

    # ------------------------------------------------------------------ #
    # Contract / handoff rule enforcement (deterministic, not model-trusted)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _enforce_contract_rules(
        task_input: RetestInput,
        common_output: CommonOutput,
        vr: VerificationResult,
    ):
        notes: list[str] = []

        # Rule: a missing required check prevents PASS.
        if vr.verdict == Verdict.PASS_ and not vr.security_check_results:
            vr.verdict = Verdict.INCONCLUSIVE
            vr.verdict_reason = (
                "Overridden from PASS: no security_check_results were produced. "
                f"Original model reason: {vr.verdict_reason}"
            )
            notes.append("verdict downgraded to INCONCLUSIVE: missing required security checks")

        if vr.verdict == Verdict.PASS_ and task_input.role_input.related_variations and not vr.variation_check_results:
            vr.verdict = Verdict.INCONCLUSIVE
            vr.verdict_reason = (
                "Overridden from PASS: related_variations were specified but no "
                f"variation_check_results were produced. Original reason: {vr.verdict_reason}"
            )
            notes.append("verdict downgraded to INCONCLUSIVE: required variation checks missing")

        # Rule: a blocked/forbidden-source result alone is not proof; functional
        # health of the permitted path must also be demonstrated.
        if vr.verdict == Verdict.PASS_ and not vr.functional_check_results:
            vr.verdict = Verdict.INCONCLUSIVE
            vr.verdict_reason = (
                "Overridden from PASS: no functional_check_results confirming "
                f"legitimate access still works. Original reason: {vr.verdict_reason}"
            )
            notes.append("verdict downgraded to INCONCLUSIVE: legitimate-path health unconfirmed")

        # Rule: Twin verification cannot close a Real finding.
        env = task_input.common.environment_kind.value
        if env in ("TWIN", "BOTH") and "real" not in vr.closure_recommendation.lower() \
                and vr.verdict == Verdict.PASS_:
            # Only block automatically when we can't tell the recommendation already
            # accounted for this; force explicit non-closure language and a review gate.
            vr.closure_recommendation = (
                "Closure NOT recommended from this result: verification ran in a "
                f"{env} environment and cannot close a Real-environment finding on its "
                f"own. Original model recommendation: {vr.closure_recommendation}"
            )
            common_output.required_gate = GateType.REVIEW
            if common_output.status == TaskStatus.DONE:
                common_output.status = TaskStatus.NEEDS_REVIEW
            notes.append("closure blocked: TWIN/BOTH result cannot close a REAL finding by itself")

        # Rule: failed retest returns evidence for replanning, not auto-reapplication.
        if vr.verdict == Verdict.FAIL:
            if common_output.proposed_actions:
                notes.append(
                    "note: verdict is FAIL - proposed_actions should describe replanning "
                    "(e.g. routing back to Patch), not a reapplication of the same change"
                )
            if not common_output.next_objective:
                common_output.next_objective = (
                    "Return evidence to Patch/Orchestrator for replanning; original "
                    "weakness still reproducible or functionality regressed."
                )
            if common_output.status == TaskStatus.DONE:
                common_output.status = TaskStatus.NEEDS_REVIEW

        if notes:
            common_output.assumptions = list(common_output.assumptions) + notes

        return common_output, vr

    # ------------------------------------------------------------------ #
    # Runtime binding + failure envelope
    # ------------------------------------------------------------------ #

    @staticmethod
    def _runtime_binding(task_input: RetestInput) -> RuntimeBinding:
        return RuntimeBinding(
            schema_version=SCHEMA_VERSION,
            workspace_id=task_input.common.workspace_id,
            run_id=task_input.common.run_id,
            environment_id=task_input.common.environment_id,
            agent_id=AGENT_ID,
            model_call_id=str(uuid.uuid4()),
        )

    def _failure_envelope(self, task_input: RetestInput, reason: str) -> Dict[str, Any]:
        result = RetestAgentResult(
            runtime=self._runtime_binding(task_input),
            common_output=CommonOutput(
                status=TaskStatus.FAILED,
                artifact_refs=[],
                evidence_refs=[],
                proposed_actions=[],
                assumptions=[],
                coverage_gaps=["Gemini call failed; no verification could be performed."],
                required_gate=None,
                concise_rationale=f"Retest agent could not complete: {reason}",
                next_objective="Retry this Retest task once the underlying failure is resolved.",
            ),
            verification_result=VerificationResult(
                verification_plan=[],
                security_check_results=[],
                variation_check_results=[],
                functional_check_results=[],
                control_and_health_results=[],
                tested_state_ref=task_input.role_input.current_target_state_ref,
                verdict=Verdict.INCONCLUSIVE,
                verdict_reason=f"Tool failure, not a security outcome: {reason}",
                closure_recommendation="Do not close; retry verification.",
            ),
        )
        return result.model_dump(mode="json")
