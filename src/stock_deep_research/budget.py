"""The run's money: a hard ceiling, reserved before each call and settled after it.

Before a call, its worst case (the prompt's tokens, estimated from its length, plus every
output token it may write, plus every search it may make) is checked against what is left; a
call that could cross the ceiling is not made (`BudgetStop`), and the run publishes what it has.
After the call, the actual cost (from its usage) is recorded on the run.
"""

from decimal import Decimal

from stock_deep_research import costs
from stock_deep_research.models import Run

CHARS_PER_TOKEN = 3  # a conservative estimate (English and numbers run nearer 4)


class BudgetStop(Exception):
    """The next call could cross the ceiling."""


class Budget:
    def __init__(self, run: Run, ceiling: Decimal):
        self.run = run
        self.ceiling = Decimal(ceiling)

    @property
    def spent(self) -> Decimal:
        return Decimal(self.run.cost_usd or 0)

    @property
    def left(self) -> Decimal:
        return self.ceiling - self.spent

    def worst_case(self, model: str, prompt_chars: int, max_tokens: int, searches: int = 0):
        inp, out = costs.price(model)
        tokens = Decimal(prompt_chars // CHARS_PER_TOKEN) * inp + Decimal(max_tokens) * out
        return tokens / costs.MILLION + searches * costs.WEB_SEARCH

    def reserve(self, model: str, prompt_chars: int, max_tokens: int, searches: int = 0):
        """Raise BudgetStop unless the call's worst case fits in what is left."""
        need = self.worst_case(model, prompt_chars, max_tokens, searches)
        if need > self.left:
            raise BudgetStop(f"next call could cost ${need:.2f}; ${self.left:.2f} left")
        return need
