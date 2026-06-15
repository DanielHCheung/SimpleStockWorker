import json
import os

import yfinance as yf
import backtrader as bt
import pandas as pd
import datetime
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# ==========================================
# 1. State Machine Strategy
# ==========================================
class RegimeSwitchingStrategy(bt.Strategy):
    params = (
        ('trend_period', 200), 
        ('rsi_period', 14),   
        ('cash_apy', 0.03),  # stimulate 3% APY
    )

    def __init__(self):
        self.qqq = self.datas[0]
        self.sqqq = self.datas[1]
        self.tqqq = self.datas[2]
        self.psq = self.datas[3]

        # 
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
        # stimulate 3% APY
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
        is_overbought = self.rsi[0] >= 70
        is_oversold = self.rsi[0] <= 30
        is_boll_oversold = self.boll[0] <= 0.1
        is_boll_overbought = self.boll[0] >= 0.9

        # ==========================================
        # 3. Regime Switching
        # ==========================================
        new_regime = None

        if is_uptrend and not is_overbought:
            new_regime = 'A' # 
        elif is_uptrend and is_overbought:
            new_regime = 'B' # 
        elif not is_uptrend and not is_oversold:
            new_regime = 'C' # 
        elif not is_uptrend and (is_oversold or is_boll_oversold):
            new_regime = 'D' # 

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
# 4. Custom Plotting Tool
# ==========================================
def plot_strategy_results(strategy_instance):
    print("Plotting Equity Curve & Regime Map...")
    dates = strategy_instance.track_dates
    nav = strategy_instance.track_nav
    regimes = strategy_instance.track_regime
    qqq_prices = strategy_instance.track_qqq 
    tqqq_prices = strategy_instance.track_tqqq 

    fig, ax = plt.subplots(figsize=(14, 7))
    
    # Equity Curve
    ax.plot(dates, nav, color='black', linewidth=2.0, label='Portfolio NAV')
    
    # QQQ Benchmark Curve
    if qqq_prices and nav:
        initial_capital = nav[0]
        initial_qqq_price = qqq_prices[0]
        benchmark_nav_qqq = [(price / initial_qqq_price) * initial_capital for price in qqq_prices]
        ax.plot(dates, benchmark_nav_qqq, color='blue', linewidth=1.5, linestyle='--', label='Benchmark (Buy & Hold QQQ)')
    # if tqqq_prices and nav:
    #     initial_capital = nav[0]
    #     initial_tqqq_price = tqqq_prices[0]
    #     benchmark_nav_tqqq = [(price / initial_tqqq_price) * initial_capital for price in tqqq_prices]
    #     ax.plot(dates, benchmark_nav_tqqq, color='red', linewidth=1.5, linestyle='--', label='Benchmark (Buy & Hold TQQQ)')

    color_map = {
        'A': 'lightgreen',
        'B': 'khaki',
        'C': 'lightcoral',
        'D': 'lightblue'
    }
    
    start_idx = 0
    for i in range(1, len(regimes)):
        if regimes[i] != regimes[i-1] or i == len(regimes) - 1:
            current_r = regimes[i-1]
            if current_r in color_map:
                ax.axvspan(dates[start_idx], dates[i], color=color_map[current_r], alpha=0.3)
            start_idx = i

    ax.set_title('Regime Switching Strategy vs QQQ Benchmark', fontsize=16, fontweight='bold')
    ax.set_ylabel('Net Asset Value ($)', fontsize=12)
    ax.set_xlabel('Date', fontsize=12)
    ax.grid(True, linestyle='--', alpha=0.6)
    
    import matplotlib.patches as mpatches
    legend_patches = [mpatches.Patch(color=color_map[k], alpha=0.5, label=f'Regime {k}') for k in color_map]
    legend_patches.insert(0, plt.Line2D([0], [0], color='black', lw=2, label='Portfolio NAV'))
    legend_patches.insert(1, plt.Line2D([0], [0], color='blue', lw=1.5, linestyle='--', label='Benchmark QQQ'))
    # legend_patches.insert(2, plt.Line2D([0], [0], color='red', lw=1.5, linestyle='--', label='Benchmark TQQQ'))
    ax.legend(handles=legend_patches, loc='upper left')

    plt.tight_layout()
    plt.show()

# ==========================================
# 5. Run Strategy
# ==========================================
def run_backtest():
    print("Downloading data from Yahoo Finance...")
    tickers = ['QQQ', 'SQQQ', 'TQQQ', 'PSQ']
    data = yf.download(tickers, start="2019-01-01", end=datetime.now().strftime("%Y-%m-%d"), group_by='ticker', auto_adjust=True)

    cerebro = bt.Cerebro()

    for ticker in tickers:
        df = data[ticker].dropna()
        data_feed = bt.feeds.PandasData(dataname=df)
        cerebro.adddata(data_feed, name=ticker)

    cerebro.addstrategy(RegimeSwitchingStrategy)

    cerebro.broker.setcash(100000.0)
    cerebro.broker.setcommission(commission=0.0005)

    print(f"Initial Fund: {cerebro.broker.getvalue():.2f}")
    results = cerebro.run()
    strategy_instance = results[0] 
    print(f"Final Fund: {cerebro.broker.getvalue():.2f}")


    return strategy_instance

# if __name__ == '__main__':
#     cerebro, results = run_backtest()
#     plot_strategy_results(results[0])

