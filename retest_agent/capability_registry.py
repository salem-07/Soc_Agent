"""
capability_registry.py
-----------------------
Executes the *registered checks* a verification_plan step refers to.

Per the shared contract: "proposed_actions use the existing registered
capability contract" and "A tool result returns task/action/attempt IDs,
target/environment, outcome, timestamps, tool version and evidence
references." The model (Gemini) never runs a check itself -- it only
*selects* a capability_id/version and params; this module is the
deterministic executor.

This file ships a small set of safe, generic network-diagnostic
capabilities (TCP reachability, HTTP health) so the agent is runnable
out of the box. Production deployments should register their real
scanning/testing tool integrations here (or load them from the
`available_capabilities` registry passed in the task input) -- each
capability function must:

  * accept `(params: dict, access_and_health_context: dict) -> CapabilityResult`
  * only ever touch targets implied by the params it was given (the
    Orchestrator/scope service is responsible for authorizing those
    targets before this task is ever dispatched)
  * never guess success -- timeouts/errors must surface as ERROR, not PASS
    (rule 7 of the contract: "Timeouts, unavailable tools and expired
    accounts never count as successful negative tests.")
"""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class CapabilityResult:
    outcome: str  # one of: REPRODUCIBLE / NOT_REPRODUCIBLE / PASS / FAIL / ERROR / SKIPPED
    detail: str
    raw: dict[str, Any] = field(default_factory=dict)
    observed_at: float = field(default_factory=time.time)


CapabilityFn = Callable[[dict[str, Any], dict[str, Any]], CapabilityResult]


def _tcp_connect(host: str, port: int, timeout: float, source_address: str | None = None):
    bind = (source_address, 0) if source_address else None
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        if bind:
            sock.bind(bind)
        start = time.time()
        sock.connect((host, port))
        latency = time.time() - start
        return True, latency, None
    except Exception as exc:  # noqa: BLE001 - we want to classify any failure
        return False, None, str(exc)
    finally:
        sock.close()


def connectivity_check(params: dict[str, Any], access_ctx: dict[str, Any]) -> CapabilityResult:
    """capability_id: connectivity_check@v1

    params: {host, port, timeout_s?, expect_open? (bool), source_address?}
    """
    host = params["host"]
    port = int(params["port"])
    timeout = float(params.get("timeout_s", 5))
    expect_open = params.get("expect_open")
    source_address = params.get("source_address") or access_ctx.get("source_address")

    open_, latency, error = _tcp_connect(host, port, timeout, source_address)

    if error and "timed out" in error.lower():
        return CapabilityResult(
            outcome="ERROR",
            detail=f"Connection to {host}:{port} timed out (treated as inconclusive, not a negative result): {error}",
            raw={"host": host, "port": port, "error": error},
        )

    if expect_open is None:
        outcome = "PASS" if open_ else "FAIL"
    else:
        outcome = "PASS" if open_ == bool(expect_open) else "FAIL"

    detail = (
        f"{host}:{port} is {'reachable' if open_ else 'not reachable'}"
        + (f" ({latency*1000:.0f} ms)" if latency is not None else "")
        + (f" from source {source_address}" if source_address else "")
    )
    return CapabilityResult(outcome=outcome, detail=detail, raw={"host": host, "port": port, "open": open_, "latency_s": latency, "error": error})


def service_health_check(params: dict[str, Any], access_ctx: dict[str, Any]) -> CapabilityResult:
    """capability_id: service_health_check@v1

    params: {url, timeout_s?, expected_status_range?: [min,max]}
    Confirms a dependent/permitted service is healthy -- used to make sure a
    FAIL on the forbidden-source check isn't just "the service is down".
    """
    url = params["url"]
    timeout = float(params.get("timeout_s", 5))
    lo, hi = params.get("expected_status_range", [200, 399])

    req = urllib.request.Request(url, method="GET")
    try:
        start = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            latency = time.time() - start
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        latency = None
    except Exception as exc:  # noqa: BLE001
        return CapabilityResult(
            outcome="ERROR",
            detail=f"Health check for {url} errored (inconclusive): {exc}",
            raw={"url": url, "error": str(exc)},
        )

    healthy = lo <= status <= hi
    return CapabilityResult(
        outcome="PASS" if healthy else "FAIL",
        detail=f"{url} responded {status}" + (f" in {latency*1000:.0f} ms" if latency else ""),
        raw={"url": url, "status": status, "latency_s": latency},
    )


def weakness_reproduction_check(params: dict[str, Any], access_ctx: dict[str, Any]) -> CapabilityResult:
    """capability_id: weakness_reproduction_check@v1

    Generic wrapper around connectivity_check whose *meaning* is "does the
    originally-reported weakness still reproduce". Kept separate from
    connectivity_check so the plan/evidence trail clearly labels which
    category (security vs functional) a given probe belongs to, even
    though the underlying mechanics can be the same TCP probe.
    """
    result = connectivity_check(params, access_ctx)
    if result.outcome == "PASS":
        # PASS here means "matched expectation" (e.g. expected_open=False and
        # it is indeed closed) -> the weakness is NOT reproducible.
        reproducible = bool(params.get("expect_open", False))
    elif result.outcome == "FAIL":
        reproducible = not bool(params.get("expect_open", False))
    else:
        return CapabilityResult(outcome="ERROR", detail=result.detail, raw=result.raw)

    outcome = "REPRODUCIBLE" if reproducible else "NOT_REPRODUCIBLE"
    return CapabilityResult(outcome=outcome, detail=result.detail, raw=result.raw)


REGISTRY: dict[str, CapabilityFn] = {
    "connectivity_check@v1": connectivity_check,
    "service_health_check@v1": service_health_check,
    "weakness_reproduction_check@v1": weakness_reproduction_check,
}


def register_capability(capability_id: str, fn: CapabilityFn) -> None:
    """Allow the host application to plug in real scanning/testing tool
    integrations (e.g. an internal vuln-scanner client) without editing
    this file."""
    REGISTRY[capability_id] = fn


def execute(capability_id: str, params: dict[str, Any], access_ctx: dict[str, Any]) -> CapabilityResult:
    fn = REGISTRY.get(capability_id)
    if fn is None:
        return CapabilityResult(
            outcome="ERROR",
            detail=f"No executor registered for capability '{capability_id}'. "
            f"This is a capability_gap -- register it via register_capability() "
            f"or report it back to Pentesting/Orchestrator.",
            raw={"capability_id": capability_id},
        )
    try:
        return fn(params, access_ctx)
    except Exception as exc:  # noqa: BLE001
        return CapabilityResult(outcome="ERROR", detail=f"Executor raised: {exc}", raw={"capability_id": capability_id})
