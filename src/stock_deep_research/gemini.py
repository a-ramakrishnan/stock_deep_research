"""Gemini API client and structured-output helpers."""

from decimal import Decimal

from google import genai
from google.genai import types
from pydantic import BaseModel

from stock_deep_research import costs
from stock_deep_research.config import get_settings


def make_client(api_key: str | None = None) -> genai.Client:
    """Build a Gemini API client using `GEMINI_API_KEY` from settings unless provided."""
    key = api_key if api_key is not None else get_settings().gemini_api_key
    return genai.Client(api_key=key or None)


def finish_reason(response) -> str:
    """Normalized stop/finish reason from a Gemini response."""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return "empty"
    reason = getattr(candidates[0], "finish_reason", None)
    return str(reason).lower() if reason is not None else "stop"


def parse_structured[T: BaseModel](
    client: genai.Client,
    *,
    model: str,
    system: str,
    prompt: str,
    schema: type[T],
    max_tokens: int,
) -> tuple[T | None, Decimal, str]:
    """Call Gemini with a JSON Pydantic schema and return `(parsed, cost_usd, finish_reason)`."""
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    cost = costs.response_cost(model, response)
    reason = finish_reason(response)
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, schema):
        return parsed, cost, reason
    if isinstance(parsed, dict):
        return schema.model_validate(parsed), cost, reason
    raw = getattr(response, "text", None)
    if raw:
        try:
            return schema.model_validate_json(raw), cost, reason
        except Exception:
            return None, cost, f"parse_error:{reason}"
    return None, cost, reason
