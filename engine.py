import json
import os
from datetime import datetime

import importlib
import pkgutil

import yfinance as yf
import backtrader as bt
import pandas as pd



# ==========================================
# Strategy Registry
# ==========================================
# Every bt.Strategy subclass that should be runnable from strategies.json
# must be added here, keyed by the "strategy_class" string used in the config.
STRATEGY_REGISTRY = {}


def register_strategy(cls):
    """Class decorator: adds a strategy to the registry under its own name."""
    STRATEGY_REGISTRY[cls.__name__] = cls
    return cls


# ==========================================
# 1. State Machine Strategy
# ==========================================

# moved to folder

def load_all_strategies():
    """scan strategies folder for all strategies """
    import strategies  
    
    # iterate all files under folder strategies 
    for _, module_name, _ in pkgutil.iter_modules(strategies.__path__):
        # eq: import strategies.regime_switching
        importlib.import_module(f"strategies.{module_name}")

load_all_strategies()


# ==========================================
# 2. (Add more strategies here)
# ==========================================
# @register_strategy
# class MyOtherStrategy(bt.Strategy):
#     params = (('some_param', 10),)
#     def __init__(self): ...
#     def next(self): ...
#
# Then reference it from strategies.json via:
#   "strategy_class": "MyOtherStrategy"


# ==========================================
# 3. Config-driven Backtest Runner
# ==========================================
_DATA_CACHE = {}


 
def _download_data(tickers, start, end):
    """Cache yfinance downloads across strategies that share the same tickers/date range."""
    key = (tuple(sorted(tickers)), start, end)
    if key not in _DATA_CACHE:
        print(f"Downloading data for {tickers} ({start} → {end})...")
        _DATA_CACHE[key] = yf.download(
            tickers, start=start, end=end, group_by='ticker', auto_adjust=True
        )
    return _DATA_CACHE[key]
 
 
def run_backtest(config: dict):
    """
    Run a single backtest from a strategy config dict, e.g. one entry of
    strategies_config.STRATEGIES:
 
    {
      "id": "regime_v1",
      "name": "Regime Switching (Trend + RSI)",
      "strategy_class": "RegimeSwitchingStrategy",
      "tickers": ["QQQ", "SQQQ", "TQQQ", "PSQ"],
      "benchmark_ticker": "QQQ",   # <-- reference/benchmark line, not hardcoded
      "start": "2019-01-01",
      "initial_cash": 100000.0,
      "commission": 0.0005,
      "params": { "trend_period": 200, "rsi_period": 14, ... }
    }
    """
    strategy_cls_name = config["strategy_class"]
    if strategy_cls_name not in STRATEGY_REGISTRY:
        raise ValueError(
            f"Unknown strategy_class '{strategy_cls_name}' in config '{config.get('id')}'. "
            f"Available: {list(STRATEGY_REGISTRY.keys())}"
        )
    strategy_cls = STRATEGY_REGISTRY[strategy_cls_name]
 
    tickers = config["tickers"]
    start = config.get("start", "2019-01-01")
    end = config.get("end") or datetime.now().strftime("%Y-%m-%d")
    params = dict(config.get("params", {}))
 
    # The reference/benchmark ticker is config-driven, not hardcoded to QQQ.
    # Default to the first ticker in the list if the config doesn't set one.
    benchmark_ticker = config.get("benchmark_ticker", tickers[0])
    if benchmark_ticker not in tickers:
        raise ValueError(
            f"benchmark_ticker '{benchmark_ticker}' in config '{config.get('id')}' "
            f"is not in tickers {tickers}"
        )
    # RegimeSwitchingStrategy (and any other strategy that wants this) reads
    # the signal/benchmark feed via the 'signal_ticker' param.
    params.setdefault('signal_ticker', benchmark_ticker)
 
    data = _download_data(tickers, start, end)
 
    cerebro = bt.Cerebro()
    for ticker in tickers:
        df = data[ticker].dropna()
        data_feed = bt.feeds.PandasData(dataname=df)
        cerebro.adddata(data_feed, name=ticker)
 
    cerebro.addstrategy(strategy_cls, **params)
 
    cerebro.broker.setcash(config.get("initial_cash", 100000.0))
    cerebro.broker.setcommission(commission=config.get("commission", 0.0005))
 
    print(f"[{config['id']}] Initial Fund: {cerebro.broker.getvalue():.2f}")
    results = cerebro.run()
    strategy_instance = results[0]
    print(f"[{config['id']}] Final Fund: {cerebro.broker.getvalue():.2f}")
 
    return strategy_instance