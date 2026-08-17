import json
import os
from datetime import datetime

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
@register_strategy
class RegimeSwitchingStrategy(bt.Strategy):
    # Defaults here are just a fallback; real values normally come from
    # strategies.json ("params") and are passed in via cerebro.addstrategy(**params).
    params = (
        ('trend_period', 200),
        ('rsi_period', 14),
        ('cash_apy', 0.03),        # simulate idle-cash APY
        ('overbought', 70),
        ('oversold', 30),
        ('boll_oversold', 0.1),
        ('boll_overbought', 0.9),
    )

    def __init__(self):
        self.qqq = self.datas[0]
        self.sqqq = self.datas[1]
        self.tqqq = self.datas[2]
        self.psq = self.datas[3]

        self.sma = bt.indicators.SimpleMovingAverage(self.qqq.close, period=self.p.trend_period)
        self.rsi = bt.indicators.RSI_Safe(self.qqq.close, period=self.p.rsi_period)
        self.boll = bt.indicators.BollingerBandsPct(self.qqq.close)

        self.current_regime = None

        # --- Data Trackers ---
        self.track_dates = []
        self.track_nav = []
        self.track_regime = []
        self.track_qqq = []
        self.track_tqqq = []

    def next(self):
        # simulate cash_apy on idle cash
        daily_rate = self.p.cash_apy / 252.0
        idle_cash = self.broker.getcash()
        if idle_cash > 0:
            self.broker.add_cash(idle_cash * daily_rate)

        self.track_dates.append(self.data.datetime.date(0))
        self.track_nav.append(self.broker.getvalue())
        self.track_regime.append(self.current_regime)
        self.track_qqq.append(self.qqq.close[0])
        self.track_tqqq.append(self.tqqq.close[0])

        if len(self) < self.p.trend_period:
            return

        # ==========================================
        # 2. Features
        # ==========================================
        is_uptrend = self.qqq.close[0] > self.sma[0]
        is_overbought = self.rsi[0] >= self.p.overbought
        is_oversold = self.rsi[0] <= self.p.oversold
        is_boll_oversold = self.boll[0] <= self.p.boll_oversold
        is_boll_overbought = self.boll[0] >= self.p.boll_overbought

        # ==========================================
        # 3. Regime Switching
        # ==========================================
        new_regime = None

        if is_uptrend and not is_overbought:
            new_regime = 'A'
        elif is_uptrend and is_overbought:
            new_regime = 'B'
        elif not is_uptrend and not is_oversold:
            new_regime = 'C'
        elif not is_uptrend and (is_oversold or is_boll_oversold):
            new_regime = 'D'

        if new_regime != self.current_regime:
            self.current_regime = new_regime
            self.log(f"进入状态: {new_regime}")

            if new_regime == 'A':
                self.order_target_percent(self.qqq, target=0.20)
                self.order_target_percent(self.tqqq, target=0.80)
                self.order_target_percent(self.sqqq, target=0.0)
                self.order_target_percent(self.psq, target=0.0)

            elif new_regime == 'B':
                self.order_target_percent(self.qqq, target=0.8)
                self.order_target_percent(self.tqqq, target=0.2)
                self.order_target_percent(self.sqqq, target=0.0)
                self.order_target_percent(self.psq, target=0.0)

            elif new_regime == 'C':
                self.order_target_percent(self.qqq, target=0.0)
                self.order_target_percent(self.tqqq, target=0.0)
                self.order_target_percent(self.psq, target=0.20)
                self.order_target_percent(self.sqqq, target=0.80)

            elif new_regime == 'D':
                self.order_target_percent(self.psq, target=0.0)
                self.order_target_percent(self.sqqq, target=0.0)
                self.order_target_percent(self.qqq, target=0.50)
                self.order_target_percent(self.tqqq, target=0.50)

    def log(self, txt, dt=None):
        dt = dt or self.datas[0].datetime.date(0)
        print(f'{dt.isoformat()} - {txt}')


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
    Run a single backtest from a strategy config dict, e.g. one entry of strategies.json:

    {
      "id": "regime_v1",
      "name": "Regime Switching (Trend + RSI)",
      "strategy_class": "RegimeSwitchingStrategy",
      "tickers": ["QQQ", "SQQQ", "TQQQ", "PSQ"],
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
    params = config.get("params", {})

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