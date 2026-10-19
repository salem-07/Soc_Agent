"""
server.py
---------
Optional HTTP wrapper around RetestAgent, for deployments where the
Orchestrator talks to each role as a service rather than importing it in-process.

Run:
    uvicorn server:app --host 0.0.0.0 --port 8005

Endpoint:
    POST /v1/retest/run
    body: the raw task input dict (common + role_input) exactly as agent.py expects
    returns: the RetestAgentResult JSON, or HTTP 422 with details if the
             input itself was invalid (never a silent guess).

This file is intentionally thin - all real logic stays in agent.py so the
pipeline can also call RetestAgent directly in-process (see main.py) without
going through HTTP at all.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from agent import RetestAgent, RetestAgentError

app = FastAPI(title="SentinelX Retest Agent", version="1.0")
_agent = RetestAgent()


@app.post("/v1/retest/run")
async def run_retest(task_input: dict) -> JSONResponse:
    try:
        result = _agent.handle_task(task_input)
    except RetestAgentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(content=result)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "agent": "retest-agent"}
