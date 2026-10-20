import json
import os
from datetime import datetime

import backtrader as bt
import pandas as pd
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from engine import register_strategy


@register_strategy
class QQQTacticalGBStrategy(bt.Strategy):
    """
    QQQ tactical timing with dynamic position sizing.

    Design (three layers, ML never touches the order book directly):
      1. Features  - trend / momentum / mean-reversion / volatility / volume
                     (+ optional interest-rate feed, see below)
      2. ML        - HistGradientBoostingRegressor predicts forward
                     `forecast_horizon`-day return. Non-linear, so it can
                     learn interactions a Ridge cannot (e.g. "oversold RSI
                     only bounces inside an uptrend").
      3. Sizing    - prediction -> raw position via a linear ramp, then
                     volatility targeting + a hard trend cap + a turnover
                     limiter. This layer is rules, not ML, on purpose:
                     it is what keeps a bad prediction from becoming a
                     bad portfolio.

    Optional rate feed: if the engine adds a second data feed named
    'TNX' (Yahoo ^TNX, close = 10Y yield x 10) or 'DGS10', its level and
    20d change become features. If absent, those features are 0.0 and the
    strategy runs unchanged on price/volume alone.
    """
    params = (
        ('train_window', 504),        # ~2 years of bars for each fit
        ('rebalance_days', 10),       # retrain + resize every 10 bars
        ('forecast_horizon', 10),     # predict forward 10-day return
        ('cash_apy', 0.03),           # idle-cash yield (same as template)
        ('target_vol', 0.18),         # 18% annualised volatility target
        ('pred_zero', -0.005),        # pred <= -0.5% (10d) -> 0% position
        ('pred_full', 0.015),         # pred >= +1.5% (10d) -> full raw position
        ('max_step', 0.50),           # max position change per rebalance
        ('signal_ticker', 'QQQ'),
    )

    def __init__(self):
        try:
            self.asset = self.getdatabyname(self.p.signal_ticker)
        except Exception:
            self.asset = self.datas[0]

        # Optional interest-rate feed (graceful if the engine has none)
        self.rate = None
        for name in ('TNX', 'DGS10', 'RATE_10Y'):
            try:
                self.rate = self.getdatabyname(name)
                break
            except Exception:
                continue

        # ==========================================
        # 1. Feature indicators
        # ==========================================
        # Trend - EXPMA family + long MAs
        self.ema12 = bt.indicators.ExponentialMovingAverage(self.asset.close, period=12)
        self.ema26 = bt.indicators.ExponentialMovingAverage(self.asset.close, period=26)
        self.ema50 = bt.indicators.ExponentialMovingAverage(self.asset.close, period=50)
        self.ema200 = bt.indicators.ExponentialMovingAverage(self.asset.close, period=200)
        self.sma20 = bt.indicators.SimpleMovingAverage(self.asset.close, period=20)

        # MACD (histogram is the usable part)
        self.macd = bt.indicators.MACD(self.asset.close,
                                       period_me1=12, period_me2=26, period_signal=9)

        # Momentum
        self.ret_1d = bt.indicators.PercentChange(self.asset.close, period=1)
        self.mom_5d = bt.indicators.PercentChange(self.asset.close, period=5)
        self.mom_20d = bt.indicators.PercentChange(self.asset.close, period=20)
        self.mom_60d = bt.indicators.PercentChange(self.asset.close, period=60)
        self.rsi = bt.indicators.RSI(self.asset.close, period=14)

        # Mean reversion / volatility - Bollinger + ATR + realised vol
        self.boll = bt.indicators.BollingerBands(self.asset.close, period=20, devfactor=2.0)
        self.atr = bt.indicators.ATR(self.asset, period=14)
        self.vol_20d = bt.indicators.StdDev(self.ret_1d, period=20)

        # Volume (QQQ volume spikes confirm / contradict price moves)
        self.vol_sma20 = bt.indicators.SimpleMovingAverage(self.asset.volume, period=20)

        # State
        self.days_since_rebalance = 0
        self.current_position = 0.0   # float now: 0.0 - 1.0, not binary
        self.current_regime = 'Cash'

        # --- Data Trackers (same contract as the template) ---
        self.track_dates = []
        self.track_nav = []
        self.track_regime = []
        self.track_benchmark = []

    # ------------------------------------------------------------------
    def _feature_row(self, i=None):
        """One feature dict. i=None -> current bar; else offset into .get() arrays
        is handled by the caller passing pre-extracted arrays (see rebalance)."""
        raise NotImplementedError  # features are built vectorised in rebalance_model

    def next(self):
        # Idle cash earns the risk-free proxy, proportional to what's idle
        daily_rate = self.p.cash_apy / 252.0
        idle_cash = self.broker.getcash()
        if idle_cash > 0:
            self.broker.add_cash(idle_cash * daily_rate)

        self.track_dates.append(self.data.datetime.date(0))
        self.track_nav.append(self.broker.getvalue())
        self.track_regime.append(self.current_regime)
        self.track_benchmark.append(self.asset.close[0])

        # Need EMA200 + a full training window before the first fit
        if len(self) < self.p.train_window + self.p.forecast_horizon + 5:
            return

        self.days_since_rebalance += 1
        if self.days_since_rebalance >= self.p.rebalance_days:
            self.days_since_rebalance = 0
            self.rebalance_model()

    # ------------------------------------------------------------------
    def rebalance_model(self):
        n = self.p.train_window
        h = self.p.forecast_horizon

        def arr(ind):
            return np.array(ind.get(size=n), dtype=float)

        close = arr(self.asset.close)
        ema12, ema26 = arr(self.ema12), arr(self.ema26)
        ema50, ema200 = arr(self.ema50), arr(self.ema200)
        macd_hist = arr(self.macd.macdhisto)
        boll_top, boll_bot, boll_mid = arr(self.boll.top), arr(self.boll.bot), arr(self.boll.mid)

        with np.errstate(divide='ignore', invalid='ignore'):
            vol_ratio = np.array(self.asset.volume.get(size=n), dtype=float) / arr(self.vol_sma20)
            rate_level = np.zeros(n)
            rate_chg20 = np.zeros(n)
            if self.rate is not None:
                r = np.array(self.rate.close.get(size=n), dtype=float)
                rate_level = r / 10.0 if np.nanmax(r) > 20 else r  # ^TNX is yield x10
                rate_chg20 = np.concatenate([np.full(20, np.nan), rate_level[20:] - rate_level[:-20]])

            df = pd.DataFrame({
                # trend
                'p_to_ema12': close / ema12 - 1,
                'p_to_ema50': close / ema50 - 1,
                'p_to_ema200': close / ema200 - 1,
                'ema50_to_200': ema50 / ema200 - 1,
                # momentum
                'ret_1d': arr(self.ret_1d),
                'mom_5d': arr(self.mom_5d),
                'mom_20d': arr(self.mom_20d),
                'mom_60d': arr(self.mom_60d),
                'rsi_norm': arr(self.rsi) / 100.0,
                'macd_hist_norm': macd_hist / close,        # scale-free MACD
                # mean reversion (Bollinger)
                'boll_pctb': (close - boll_bot) / (boll_top - boll_bot),  # 0=lower, 1=upper
                'boll_bw': (boll_top - boll_bot) / boll_mid,              # bandwidth = vol regime
                # volatility
                'vol_20d': arr(self.vol_20d),
                'atr_pct': arr(self.atr) / close,
                'dd_20d': close / pd.Series(close).rolling(20).max().values - 1,
                # volume + rates
                'vol_ratio': vol_ratio,
                'rate_10y': rate_level,
                'rate_chg20': rate_chg20,
                'close': close,
            })

        feature_cols = [c for c in df.columns if c != 'close']

        # Label: forward h-day return.
        df['target'] = df['close'].pct_change(periods=h).shift(-h)
        # PURGE the last h rows: their labels peek into the future relative
        # to "today". dropna() already removes them via the shift, but the
        # explicit slice documents intent and guards refactors.
        df = df.iloc[:-h].dropna()
        if len(df) < 200:
            return

        X, y = df[feature_cols], df['target']

        # ==========================================
        # 2. Model - gradient boosting, not linear
        # ==========================================
        # No scaling needed (tree splits are scale-invariant). Shallow trees
        # + strong min_samples_leaf: with ~480 training rows, capacity
        # control matters more than fit.
        model = HistGradientBoostingRegressor(
            max_iter=200, learning_rate=0.05, max_depth=3,
            min_samples_leaf=20, l2_regularization=1.0, random_state=42,
        )
        model.fit(X, y)

        # Current-bar features: df's last row is h bars old because of the
        # purge, so rebuild the live row from the raw arrays' final values.
        last = -1
        cur = pd.DataFrame([{
            'p_to_ema12': close[last] / ema12[last] - 1,
            'p_to_ema50': close[last] / ema50[last] - 1,
            'p_to_ema200': close[last] / ema200[last] - 1,
            'ema50_to_200': ema50[last] / ema200[last] - 1,
            'ret_1d': float(self.ret_1d[0]), 'mom_5d': float(self.mom_5d[0]),
            'mom_20d': float(self.mom_20d[0]), 'mom_60d': float(self.mom_60d[0]),
            'rsi_norm': float(self.rsi[0]) / 100.0,
            'macd_hist_norm': float(self.macd.macdhisto[0]) / close[last],
            'boll_pctb': (close[last] - boll_bot[last]) / max(boll_top[last] - boll_bot[last], 1e-9),
            'boll_bw': (boll_top[last] - boll_bot[last]) / boll_mid[last],
            'vol_20d': float(self.vol_20d[0]),
            'atr_pct': float(self.atr[0]) / close[last],
            'dd_20d': close[last] / np.max(close[-20:]) - 1,
            'vol_ratio': float(self.asset.volume[0]) / max(float(self.vol_sma20[0]), 1.0),
            'rate_10y': float(rate_level[last]),
            'rate_chg20': float(rate_chg20[last]) if not np.isnan(rate_chg20[last]) else 0.0,
        }], columns=feature_cols)

        pred = float(model.predict(cur)[0])

        # ==========================================
        # 3. Prediction -> dynamic position (rules layer)
        # ==========================================
        # (a) Signal ramp: linear between pred_zero and pred_full, clipped
        raw = np.clip((pred - self.p.pred_zero) / (self.p.pred_full - self.p.pred_zero), 0.0, 1.0)

        # (b) Volatility targeting: size down when realised vol > target
        ann_vol = max(float(self.vol_20d[0]) * np.sqrt(252), 1e-6)
        vol_scalar = float(np.clip(self.p.target_vol / ann_vol, 0.4, 1.1))
        target = raw * vol_scalar

        # (c) Trend cap: below EMA200 the model only gets half a portfolio,
        #     no matter how bullish the short-term features look
        if close[last] < ema200[last]:
            target = min(target, 0.5)

        # (d) Quantise to 25% steps + limit turnover per rebalance
        target = round(target / 0.25) * 0.25
        delta = np.clip(target - self.current_position, -self.p.max_step, self.p.max_step)
        target = float(np.clip(self.current_position + delta, 0.0, 1.0))

        if abs(target - self.current_position) >= 0.05:
            self.log(f"pred10d={pred:+.3%} raw={raw:.2f} vol_scalar={vol_scalar:.2f} "
                     f"-> position {self.current_position:.0%} => {target:.0%}")
            self.current_position = target
            self.current_regime = ('Cash' if target == 0 else
                                   'Defensive' if target <= 0.35 else
                                   'Partial' if target <= 0.7 else 'Long')
            self.order_target_percent(self.asset, target=target)
            # regime takes effect on the next bar's append (same as template)

    def log(self, txt, dt=None):
        dt = dt or self.datas[0].datetime.date(0)
        print(f'{dt.isoformat()} - {txt}')
