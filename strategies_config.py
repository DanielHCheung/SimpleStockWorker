"""
strategies_config.py

Source of truth for which strategies run and what parameters they use.
This is plain Python (not JSON) so you can:
  - add comments next to params
  - share/derive param blocks between strategies
  - use real Python values (dates, expressions, env vars, etc.)

main.py imports STRATEGIES from here, runs each one, and exports the
*results* as JSON into public/data/ — that JSON is a build artifact for
the website, not something you hand-edit.

To add a strategy:
  1. Write/import the bt.Strategy subclass in engine.py and decorate it
     with @register_strategy (this puts it in engine.STRATEGY_REGISTRY).
  2. Add an entry to STRATEGIES below pointing "strategy_class" at its
     class name.
"""

from datetime import datetime

TODAY = datetime.now().strftime("%Y-%m-%d")

# Shared defaults so variants don't have to repeat every key.
_BASE_REGIME_PARAMS = {
    "rsi_period": 14,
    "cash_apy": 0.03,
    "overbought": 70,
    "oversold": 30,
    "boll_oversold": 0.1,
    "boll_overbought": 0.9,
}

STRATEGIES = [
    {
        "id": "regime_v1",
        "name": "Regime Switching (Trend + RSI)",
        "description": (
            "200D trend filter + RSI/Bollinger overbought-oversold regime "
            "switch across QQQ/TQQQ/SQQQ/PSQ"
        ),
        "enabled": True,
        "strategy_class": "RegimeSwitchingStrategy",
        "tickers": ["QQQ", "SQQQ", "TQQQ", "PSQ"],
        "start": "2019-01-01",
        "end": TODAY,
        "initial_cash": 100_000.0,
        "commission": 0.0005,
        "params": {
            **_BASE_REGIME_PARAMS,
            "trend_period": 200,
        },
    },
    {
        "id": "regime_v1_fast",
        "name": "Regime Switching (Fast 100D)",
        "description": "Same logic as v1 but with a faster 100-day trend filter, for comparison",
        "enabled": True,
        "strategy_class": "RegimeSwitchingStrategy",
        "tickers": ["QQQ", "SQQQ", "TQQQ", "PSQ"],
        "start": "2019-01-01",
        "end": TODAY,
        "initial_cash": 100_000.0,
        "commission": 0.0005,
        "params": {
            **_BASE_REGIME_PARAMS,
            "trend_period": 100,
        },
    },
    # {
    #     "id": "my_new_strategy",
    #     "name": "My New Strategy",
    #     "enabled": False,        # flip on when ready
    #     "strategy_class": "MyOtherStrategy",
    #     "tickers": ["SPY"],
    #     "start": "2020-01-01",
    #     "end": TODAY,
    #     "initial_cash": 100_000.0,
    #     "commission": 0.0005,
    #     "params": {"some_param": 10},
    # },
]


def get_enabled_strategies():
    return [s for s in STRATEGIES if s.get("enabled", True)]