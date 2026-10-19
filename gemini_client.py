"""
gemini_client.py
-----------------
Thin wrapper around the Gemini API (google-generativeai SDK) used only by this
agent. Keeps the networking/SDK concerns out of agent.py.

Reads the API key from the GEMINI_API_KEY environment variable - never pass
it in code or logs. The orchestrator / deployment environment is responsible
for injecting it as a secret, same way other credentials stay outside model
context per the contract ("Secret material stays outside model context").
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict

import google.generativeai as genai


class GeminiCallError(RuntimeError):
    """Raised when Gemini cannot be called or its reply isn't usable JSON."""


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _strip_fences(text: str) -> str:
    """Gemini sometimes wraps JSON in ```json ... ``` even when told not to."""
    return _JSON_FENCE_RE.sub("", text).strip()


class GeminiClient:
    def __init__(
        self,
        model_name: str = "gemini-3.7-flash",
        api_key: str | None = None,
        temperature: float = 0.1,
        max_output_tokens: int = 8192,
    ) -> None:
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise GeminiCallError(
                "GEMINI_API_KEY is not set. Provide it via the environment "
                "(never hard-code it in source)."
            )
        genai.configure(api_key=key)
        self._model = genai.GenerativeModel(
            model_name=model_name,
            generation_config={
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
                "response_mime_type": "application/json",
            },
        )

    def generate_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """Call Gemini and return the parsed JSON object it produced."""
        try:
            response = self._model.generate_content(
                [
                    {"role": "user", "parts": [system_prompt + "\n\n" + user_prompt]},
                ]
            )
        except Exception as exc:  # network / SDK errors
            raise GeminiCallError(f"Gemini request failed: {exc}") from exc

        text = getattr(response, "text", None)
        if not text:
            raise GeminiCallError("Gemini returned an empty response.")

        cleaned = _strip_fences(text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise GeminiCallError(
                f"Gemini response was not valid JSON: {exc}\nRaw text:\n{text[:2000]}"
            ) from exc
