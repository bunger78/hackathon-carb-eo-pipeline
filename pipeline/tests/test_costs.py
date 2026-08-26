import pytest
from core.costs import cost_usd, BudgetGuard, BudgetExceeded
from config import settings

def test_cost_math():
    # Price-independent test: 1M in + 1M out should equal sum of per-token prices
    assert cost_usd(1_000_000, 1_000_000) == pytest.approx(settings.price_in_per_mtok + settings.price_out_per_mtok)

def test_budget_guard_trips():
    g = BudgetGuard(1.0)
    g.add(0.6); g.add(0.3)
    assert g.spent == pytest.approx(0.9)
    with pytest.raises(BudgetExceeded):
        g.add(0.2)
