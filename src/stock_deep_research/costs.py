"""What a model call costs, from its usage. US dollars, Anthropic's list prices."""

from decimal import Decimal

# model -> (input, output) per million tokens. Cache writes cost 1.25x input, reads 0.1x.
PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-5-5": (Decimal("4"), Decimal("20")),
    "claude-sonnet-5-5": (Decimal("2"), Decimal("10")),
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),
}
WEB_SEARCH = Decimal("0.01")  # per search ($10 per 1,000); web fetch costs only its tokens
MILLION = Decimal(1_000_000)


def price(model: str) -> tuple[Decimal, Decimal]:
    if model not in PRICES:
        raise ValueError(f"No price for {model}: add it to costs.PRICES")
    return PRICES[model]


def cost(model: str, usage) -> Decimal:
    """The cost of one response, from its `usage`."""
    inp, out = price(model)
    write = getattr(usage, "cache_creation_input_tokens", None) or 0
    read = getattr(usage, "cache_read_input_tokens", None) or 0
    tokens = (
        usage.input_tokens * inp
        + write * inp * Decimal("1.25")
        + read * inp * Decimal("0.1")
        + usage.output_tokens * out
    ) / MILLION
    server = getattr(usage, "server_tool_use", None)
    searches = (getattr(server, "web_search_requests", 0) or 0) if server else 0
    return (tokens + searches * WEB_SEARCH).quantize(Decimal("0.0001"))
