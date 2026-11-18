"""What a model call costs, from its usage. US dollars, Gemini API list prices."""

from decimal import Decimal

# model -> (input, output) per million tokens. Cached input costs 0.25x input.
PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "gemini-2.5-pro": (Decimal("1.25"), Decimal("10.00")),
    "gemini-2.5-flash": (Decimal("0.30"), Decimal("2.50")),
    "gemini-2.5-flash-lite": (Decimal("0.10"), Decimal("0.40")),
    "gemini-3-pro-preview": (Decimal("2.00"), Decimal("12.00")),
    "gemini-3-flash-preview": (Decimal("0.50"), Decimal("3.00")),
}
WEB_SEARCH = Decimal("0.035")  # per grounded Google Search query ($35 per 1,000)
MILLION = Decimal(1_000_000)


def price(model: str) -> tuple[Decimal, Decimal]:
    if model not in PRICES:
        raise ValueError(f"No price for {model}: add it to costs.PRICES")
    return PRICES[model]


def search_count(response) -> int:
    """Number of Google Search queries billed in a Gemini response's grounding metadata."""
    candidates = getattr(response, "candidates", None) or []
    total = 0
    for cand in candidates:
        gm = getattr(cand, "grounding_metadata", None)
        if gm:
            queries = getattr(gm, "web_search_queries", None) or []
            total += len(queries)
    return total


def cost(model: str, usage, searches: int = 0) -> Decimal:
    """The cost of one response, from its `usage_metadata` (and grounded search count)."""
    inp, out = price(model)
    if usage is None:
        return (searches * WEB_SEARCH).quantize(Decimal("0.0001"))
    prompt_tokens = getattr(usage, "prompt_token_count", None) or 0
    cached_tokens = getattr(usage, "cached_content_token_count", None) or 0
    uncached = max(0, prompt_tokens - cached_tokens)
    output_tokens = (getattr(usage, "candidates_token_count", None) or 0) + (
        getattr(usage, "thoughts_token_count", None) or 0
    )
    tokens = (
        uncached * inp + cached_tokens * inp * Decimal("0.25") + output_tokens * out
    ) / MILLION
    return (tokens + searches * WEB_SEARCH).quantize(Decimal("0.0001"))


def response_cost(requested: str, response) -> Decimal:
    """A response's cost at the prices of the model that served it."""
    served = getattr(response, "model_version", None) or getattr(response, "model", None)
    model = served if served in PRICES else requested
    usage = getattr(response, "usage_metadata", None)
    return cost(model, usage, searches=search_count(response))
