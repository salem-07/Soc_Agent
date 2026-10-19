"""
gemini_client.py
-----------------
Minimal wrapper around the Gemini API (google-generativeai SDK).

The agent only ever asks Gemini to PROPOSE structured JSON -- plan steps or
a result synthesis -- never to execute anything itself. That keeps the
"model proposes, services dispatch/authorize" rule (contract rule #1)
intact: this module is the only place `google.generativeai` is imported.
"""

from __future__ import annotations

import json
import os
from typing import Any

import google.generativeai as genai


class GeminiClient:
    def __init__(self, api_key: str | None = None, model: str = "gemini-2.5-pro"):
        api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Export it or pass api_key= explicitly "
                "(see .env.example)."
            )
        genai.configure(api_key=api_key)
        self._model_name = model
        self._model = genai.GenerativeModel(model)

    def generate_json(
        self,
        system_instruction: str,
        prompt: str,
        response_schema: dict[str, Any],
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """Call Gemini and force a JSON response matching response_schema
        (Gemini structured output / controlled generation)."""
        model = genai.GenerativeModel(
            self._model_name,
            system_instruction=system_instruction,
        )
        resp = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                temperature=temperature,
                response_mime_type="application/json",
                response_schema=response_schema,
            ),
        )
        text = resp.text
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Gemini did not return valid JSON: {exc}\nRaw: {text[:2000]}") from exc
