"""
main.py
-------
CLI entry point for the Retest agent.

Usage:
    python main.py path/to/task_input.json
    cat task_input.json | python main.py

The input is exactly what the Orchestrator / context builder hands this role
per the contract: common input fields + the Retest role-specific input
fields. The agent runs fully automatically from there - it does not prompt
for anything else. Output is the final VerificationResult envelope as JSON
on stdout (safe to pipe straight back into the orchestrator / Reporting &
Memory role).

Exit codes:
    0  - task handled (status may still be FAILED/BLOCKED - check the JSON)
    2  - task input itself was invalid / could not be processed at all
"""

from __future__ import annotations

import json
import sys

from agent import RetestAgent, RetestAgentError


def _load_input() -> dict:
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as fh:
            return json.load(fh)
    data = sys.stdin.read()
    if not data.strip():
        print("No input provided (pass a file path or pipe JSON on stdin).", file=sys.stderr)
        sys.exit(2)
    return json.loads(data)


def main() -> None:
    try:
        raw_input = _load_input()
    except (json.JSONDecodeError, FileNotFoundError, OSError) as exc:
        print(f"Could not read task input: {exc}", file=sys.stderr)
        sys.exit(2)

    agent = RetestAgent()
    try:
        result = agent.handle_task(raw_input)
    except RetestAgentError as exc:
        print(f"Retest agent could not process this task: {exc}", file=sys.stderr)
        sys.exit(2)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
