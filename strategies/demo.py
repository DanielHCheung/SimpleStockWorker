import json
import os
from datetime import datetime

import yfinance as yf
import backtrader as bt
import pandas as pd

from engine import register_strategy


@register_strategy
class RegimeSwitchingStrategy(bt.Strategy):
    # Defaults here are just a fallback; real values normally come from
    # strategies_config.py ("params") and are passed in via cerebro.addstrategy(**params).
    params = (
        ('trend_period', 200),
        ('rsi_period', 14),
        ('cash_apy', 0.03),        # simulate idle-cash APY
        ('overbought', 70),
        ('oversold', 30),
        ('boll_oversold', 0.1),
        ('boll_overbought', 0.9),
        # Which data feed drives the trend/RSI/Bollinger signal AND is used
        # as the "vs benchmark" comparison line on the dashboard. Must match
        # one of the names passed to cerebro.adddata(..., name=...), i.e.
        # one of the tickers in the strategy's config.
        ('signal_ticker', 'QQQ'),
    )
 
    def __init__(self):
        # Signal feed: drives regime detection AND doubles as the benchmark
        # line the frontend plots the NAV against. Looked up by name so it
        # isn't hardcoded to any single ticker.
        self.signal_data = self.getdatabyname(self.p.signal_ticker)
 
        # Everything else is just "the rest of the tradable universe" for
        # this strategy; kept as a name->data map instead of fixed attributes
        # like self.sqqq/self.tqqq/self.psq so strategies aren't locked to a
        # specific 4-ticker set.
        self.instruments = {d._name: d for d in self.datas}
 
        self.sma = bt.indicators.SimpleMovingAverage(self.signal_data.close, period=self.p.trend_period)
        self.rsi = bt.indicators.RSI_Safe(self.signal_data.close, period=self.p.rsi_period)
        self.boll = bt.indicators.BollingerBandsPct(self.signal_data.close)
 
        self.current_regime = None
 
        # --- Data Trackers ---
        self.track_dates = []
        self.track_nav = []
        self.track_regime = []
        self.track_benchmark = []   # price series of self.p.signal_ticker, for "vs benchmark" chart
 
    def next(self):
        # simulate cash_apy on idle cash
        daily_rate = self.p.cash_apy / 252.0
        idle_cash = self.broker.getcash()
        if idle_cash > 0:
            self.broker.add_cash(idle_cash * daily_rate)
 
        self.track_dates.append(self.data.datetime.date(0))
        self.track_nav.append(self.broker.getvalue())
        self.track_regime.append(self.current_regime)
        self.track_benchmark.append(self.signal_data.close[0])
 
        if len(self) < self.p.trend_period:
            return
 
        # ==========================================
        # 2. Features
        # ==========================================
        is_uptrend = self.signal_data.close[0] > self.sma[0]
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
 
            qqq = self.instruments['QQQ']
            tqqq = self.instruments['TQQQ']
            sqqq = self.instruments['SQQQ']
            psq = self.instruments['PSQ']
 
            if new_regime == 'A':
                self.order_target_percent(qqq, target=0.20)
                self.order_target_percent(tqqq, target=0.80)
                self.order_target_percent(sqqq, target=0.0)
                self.order_target_percent(psq, target=0.0)
 
            elif new_regime == 'B':
                self.order_target_percent(qqq, target=0.8)
                self.order_target_percent(tqqq, target=0.2)
                self.order_target_percent(sqqq, target=0.0)
                self.order_target_percent(psq, target=0.0)
 
            elif new_regime == 'C':
                self.order_target_percent(qqq, target=0.0)
                self.order_target_percent(tqqq, target=0.0)
                self.order_target_percent(psq, target=0.20)
                self.order_target_percent(sqqq, target=0.80)
 
            elif new_regime == 'D':
                self.order_target_percent(psq, target=0.0)
                self.order_target_percent(sqqq, target=0.0)
                self.order_target_percent(qqq, target=0.50)
                self.order_target_percent(tqqq, target=0.50)
 
    def log(self, txt, dt=None):
        dt = dt or self.datas[0].datetime.date(0)
        print(f'{dt.isoformat()} - {txt}')