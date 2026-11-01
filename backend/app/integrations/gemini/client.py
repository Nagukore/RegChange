"""Google Gemini client with enforced structured output.

Implemented against the Generative Language REST API:
    POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
    header: x-goog-api-key: <GEMINI_API_KEY>

Why REST rather than the SDK: this project needs exact control over
`responseMimeType` + `responseSchema` and over retry/timeout behaviour, and
httpx is already a dependency. One less package to pin.

Two invariants enforced here:
  1. Every model response is parsed and validated against a caller-supplied
     Pydantic model. Unvalidated JSON never reaches the database.
  2. Regulatory text is passed as DATA, never as instructions. See
     `app/prompts/base.py` for the delimiting and injection-resistance rules.
"""
from __future__ import annotations

import json
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

T = TypeVar("T", bound=BaseModel)


class GeminiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class GeminiNotConfigured(GeminiError):
    """No GEMINI_API_KEY set; callers must degrade visibly."""


class GeminiTransient(GeminiError):
    """Retryable: 429 or 5xx."""


class GeminiOutputInvalid(GeminiError):
    """The model returned JSON that failed Pydantic validation."""

    def __init__(self, message: str, *, raw: str | None = None):
        super().__init__(message)
        self.raw = raw


def _strip_unsupported(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert a Pydantic JSON schema into the subset Gemini accepts.

    Gemini's `responseSchema` is OpenAPI-flavoured and rejects `$defs`/`$ref`,
    `additionalProperties`, `anyOf` unions containing null, and several
    validation keywords. Pydantic emits all of these, so they are resolved or
    removed here.
    """
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(n) for n in node]
        if not isinstance(node, dict):
            return node

        if "$ref" in node:
            ref_name = node["$ref"].split("/")[-1]
            target = defs.get(ref_name, {})
            merged = {k: v for k, v in node.items() if k != "$ref"}
            return resolve({**target, **merged})

        # `str | None` becomes anyOf[str, null]; collapse to the non-null branch
        # because Gemini has no nullable-union support.
        for union_key in ("anyOf", "oneOf"):
            if union_key in node:
                branches = [b for b in node[union_key] if b.get("type") != "null"]
                chosen = branches[0] if branches else {"type": "string"}
                rest = {k: v for k, v in node.items() if k != union_key}
                return resolve({**chosen, **rest})

        drop = {
            "$defs", "$schema", "additionalProperties", "title", "default",
            "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "const",
            "pattern", "patternProperties", "allOf", "discriminator",
            "minLength", "maxLength", "minimum", "maximum", "examples",
        }
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in drop:
                continue
            out[key] = resolve(value)

        # An enum must declare its type for Gemini to accept it.
        if "enum" in out and "type" not in out:
            out["type"] = "string"
        return out

    cleaned = resolve({k: v for k, v in schema.items() if k != "$defs"})
    return cleaned if isinstance(cleaned, dict) else {"type": "object"}


class GeminiClient:
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key if api_key is not None else settings.gemini_api_key

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise GeminiNotConfigured(
                "GEMINI_API_KEY is not set. Create a key at "
                "https://aistudio.google.com/apikey and add it to backend/.env"
            )
        return {"x-goog-api-key": self._api_key, "Content-Type": "application/json"}

    @retry(
        retry=retry_if_exception_type((GeminiTransient, httpx.TransportError)),
        stop=stop_after_attempt(settings.gemini_max_retries),
        wait=wait_exponential(multiplier=2, min=3, max=30),
        reraise=True,
    )
    async def _generate(
        self,
        *,
        model: str,
        system_instruction: str,
        user_content: str,
        response_schema: dict[str, Any] | None,
        temperature: float,
        max_output_tokens: int,
    ) -> str:
        generation_config: dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            "candidateCount": 1,
        }
        if response_schema is not None:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseSchema"] = response_schema

        body = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"role": "user", "parts": [{"text": user_content}]}],
            "generationConfig": generation_config,
        }
        url = f"{GEMINI_BASE_URL}/models/{model}:generateContent"

        async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
            response = await client.post(url, json=body, headers=self._headers())

        if response.status_code >= 400:
            try:
                detail = json.dumps(response.json().get("error", {}))[:500]
            except Exception:
                detail = response.text[:500]
            msg = f"Gemini returned HTTP {response.status_code}: {detail}"
            if response.status_code == 429 or response.status_code >= 500:
                raise GeminiTransient(msg, status_code=response.status_code)
            raise GeminiError(msg, status_code=response.status_code)

        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            # Usually a safety block or an empty prompt.
            feedback = data.get("promptFeedback", {})
            raise GeminiError(f"Gemini returned no candidates (promptFeedback={feedback})")

        candidate = candidates[0]
        finish_reason = candidate.get("finishReason")
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)

        if finish_reason == "MAX_TOKENS" and not text.rstrip().endswith(("}", "]")):
            raise GeminiError(
                "Gemini hit MAX_TOKENS and returned truncated JSON. "
                "Reduce the input size or raise max_output_tokens."
            )
        if not text.strip():
            raise GeminiError(f"Gemini returned an empty response (finishReason={finish_reason})")
        return text

    async def generate_structured(
        self,
        *,
        system_instruction: str,
        user_content: str,
        output_model: type[T],
        model: str | None = None,
        temperature: float = 0.1,
        max_output_tokens: int = 32768,
    ) -> T:
        """Generate and validate a response against `output_model`.

        Raises GeminiOutputInvalid if the model returns JSON that does not
        satisfy the schema -- the caller must then fail visibly rather than
        persist a partial interpretation.
        """
        schema = _strip_unsupported(output_model.model_json_schema())
        raw = await self._generate(
            model=model or settings.gemini_model,
            system_instruction=system_instruction,
            user_content=user_content,
            response_schema=schema,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GeminiOutputInvalid(
                f"Gemini response was not valid JSON: {exc}", raw=raw[:2000]
            ) from exc
        try:
            return output_model.model_validate(payload)
        except ValidationError as exc:
            raise GeminiOutputInvalid(
                f"Gemini JSON failed {output_model.__name__} validation: {exc}",
                raw=raw[:2000],
            ) from exc

    async def generate_text(
        self,
        *,
        system_instruction: str,
        user_content: str,
        model: str | None = None,
        temperature: float = 0.2,
        max_output_tokens: int = 4096,
    ) -> str:
        return await self._generate(
            model=model or settings.gemini_model,
            system_instruction=system_instruction,
            user_content=user_content,
            response_schema=None,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

    async def healthcheck(self) -> dict[str, Any]:
        if not self.configured:
            return {"configured": False, "reachable": False, "detail": "GEMINI_API_KEY not set"}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{GEMINI_BASE_URL}/models/{settings.gemini_model}",
                    headers=self._headers(),
                )
            if response.status_code >= 400:
                return {
                    "configured": True,
                    "reachable": False,
                    "detail": f"HTTP {response.status_code} for model {settings.gemini_model}",
                }
            return {
                "configured": True,
                "reachable": True,
                "detail": f"Model {settings.gemini_model} available",
            }
        except Exception as exc:
            return {"configured": True, "reachable": False, "detail": str(exc)[:300]}


def get_gemini_client() -> GeminiClient:
    return GeminiClient()
