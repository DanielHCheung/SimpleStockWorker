import json
import os
from datetime import datetime

import backtrader as bt
import pandas as pd
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from engine import register_strategy


@register_strategy
class RidgeRegressionStrategy(bt.Strategy):
    # Parameters can be dynamically injected via cerebro.addstrategy(**params)
    params = (
        ('train_window', 252),      # Rolling training window: past 252 trading days
        ('ridge_alpha', 1.0),       # L2 Regularization Penalty for the Ridge model
        ('cash_apy', 0.03),         # Idle-cash Annual Percentage Yield
        ('rebalance_days', 20),     # Rebalance frequency: retrain model and adjust positions every 20 days
        ('signal_ticker', 'AAPL'),
    )

    def __init__(self):
        # Generalized asset reference, driven by config (engine.run_backtest
        # injects 'signal_ticker' from the strategy's benchmark_ticker).
        # Falls back to the first data feed if the name isn't found, so this
        # still works if someone runs it standalone with a single feed.
        try:
            self.asset = self.getdatabyname(self.p.signal_ticker)
        except Exception:
            self.asset = self.datas[0]

        # ==========================================
        # 1. Feature Engineering / Predictors
        # ==========================================
        self.ret_1d = bt.indicators.PercentChange(self.asset.close, period=1)
        self.mom_5d = bt.indicators.PercentChange(self.asset.close, period=5)
        self.mom_20d = bt.indicators.PercentChange(self.asset.close, period=20)
        self.sma_20d = bt.indicators.SimpleMovingAverage(self.asset.close, period=20)

        # Backtrader's built-in StdDev is price-based.
        # We need the standard deviation of daily returns to represent volatility.
        self.vol_20d = bt.indicators.StdDev(self.ret_1d, period=20)

        # State control variables
        self.days_since_rebalance = 0
        self.current_position = 0    # 0: Cash, 1: Long
        self.current_regime = 'Cash' # Initial regime state

        # --- Data Trackers ---
        # These four must stay the same length, appended exactly once per
        # `next()` call, so main.py's export (which zips dates/nav/regime/
        # benchmark together) doesn't desync.
        self.track_dates = []
        self.track_nav = []
        self.track_regime = []      # 'Cash' / 'Long' per bar, for the regime timeline
        self.track_benchmark = []   # asset's own price series, for the "vs benchmark" chart

    def next(self):
        # Simulate risk-free interest on idle cash
        daily_rate = self.p.cash_apy / 252.0
        idle_cash = self.broker.getcash()
        if idle_cash > 0:
            self.broker.add_cash(idle_cash * daily_rate)

        # Record trajectory data for post-backtest analysis.
        # Exactly one append per list, per bar — keeps everything aligned.
        self.track_dates.append(self.data.datetime.date(0))
        self.track_nav.append(self.broker.getvalue())
        self.track_regime.append(self.current_regime)
        self.track_benchmark.append(self.asset.close[0])

        # Ensure sufficient historical data to fill the training window and future target shift
        if len(self) < self.p.train_window + 5:
            return

        self.days_since_rebalance += 1

        # ==========================================
        # 2. Machine Learning Model Trigger
        # ==========================================
        # Trigger periodic retraining to avoid the performance bottleneck of fitting the model on every single bar
        if self.days_since_rebalance >= self.p.rebalance_days:
            self.days_since_rebalance = 0
            self.rebalance_model()

    def rebalance_model(self):
        """
        Core logic: Extract historical features -> Train Ridge model -> Predict current signal -> Execute orders
        """
        hist_len = self.p.train_window

        # Extract historical arrays for scikit-learn compatibility
        ret_1d_arr = np.array(self.ret_1d.get(size=hist_len))
        mom_5d_arr = np.array(self.mom_5d.get(size=hist_len))
        mom_20d_arr = np.array(self.mom_20d.get(size=hist_len))
        vol_20d_arr = np.array(self.vol_20d.get(size=hist_len))
        sma_20d_arr = np.array(self.sma_20d.get(size=hist_len))
        close_arr = np.array(self.asset.close.get(size=hist_len))

        # Calculate price-to-moving-average (bias) factor
        price_to_ma_arr = (close_arr / sma_20d_arr) - 1

        # Construct DataFrame for training
        df = pd.DataFrame({
            'ret_1d': ret_1d_arr,
            'mom_5d': mom_5d_arr,
            'mom_20d': mom_20d_arr,
            'vol_20d': vol_20d_arr,
            'price_to_ma': price_to_ma_arr,
            'close': close_arr
        })

        # Target label: Predict forward 5-day return
        df['target'] = df['close'].pct_change(periods=5).shift(-5)

        # Drop rows with NaN values caused by shifting to keep a clean training set
        df = df.dropna()
        if len(df) < 50:
            return  # Skip training if valid samples are insufficient

        # Define feature columns explicitly
        feature_cols = ['ret_1d', 'mom_5d', 'mom_20d', 'vol_20d', 'price_to_ma']
        X = df[feature_cols]
        y = df['target']

        # Feature Standardization - A mandatory step for linear models with regularization
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        # Fit the Ridge Regression Model
        model = Ridge(alpha=self.p.ridge_alpha)
        model.fit(X_scaled, y)

        # ==========================================
        # 3. Prediction & Position Sizing
        # ==========================================
        # Wrap the current features in a pandas DataFrame with matching column names
        # to prevent the scikit-learn "valid feature names" UserWarning
        current_features = pd.DataFrame([[
            self.ret_1d[0],
            self.mom_5d[0],
            self.mom_20d[0],
            self.vol_20d[0],
            (self.asset.close[0] / self.sma_20d[0]) - 1
        ]], columns=feature_cols)

        current_features_scaled = scaler.transform(current_features)
        pred_return = model.predict(current_features_scaled)[0]

        # Trading logic: Go long if expected return > 0, otherwise hold cash
        new_position = 1 if pred_return > 0 else 0

        if new_position != self.current_position:
            self.current_position = new_position

            if new_position == 1:
                self.current_regime = 'Long'
                self.log(f"Bullish signal (Predicted return: {pred_return:.4f}) -> Going long 100%")
                self.order_target_percent(self.asset, target=1.0)
            else:
                self.current_regime = 'Cash'
                self.log(f"Bearish signal (Predicted return: {pred_return:.4f}) -> Liquidating to cash")
                self.order_target_percent(self.asset, target=0.0)
            # current_regime only takes effect on the *next* bar's next() call,
            # which is where track_regime is actually appended — do not append
            # here, that was double-writing track_benchmark with a stale value
            # on rebalance bars and desyncing it from track_dates/track_nav.

    def log(self, txt, dt=None):
        dt = dt or self.datas[0].datetime.date(0)
        print(f'{dt.isoformat()} - {txt}')