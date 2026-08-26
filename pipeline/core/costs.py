from config import settings

class BudgetExceeded(Exception):
    pass

def cost_usd(tok_in: int, tok_out: int) -> float:
    return tok_in * settings.price_in_per_mtok / 1e6 + tok_out * settings.price_out_per_mtok / 1e6

class BudgetGuard:
    def __init__(self, limit_usd: float):
        self.limit = limit_usd
        self.spent = 0.0

    def add(self, usd: float) -> None:
        if self.spent + usd > self.limit:
            raise BudgetExceeded(f"budget {self.limit} would be exceeded (spent {self.spent:.2f} + {usd:.2f})")
        self.spent += usd
