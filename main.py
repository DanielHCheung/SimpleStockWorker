import os
import requests
import json
from engine import run_backtest

def export_data(strategy_instance):
    data = {
        "dates": [d.isoformat() for d in strategy_instance.track_dates],
        "nav": strategy_instance.track_nav,
        "regimes": strategy_instance.track_regime,
        "qqq": strategy_instance.track_qqq
    }

    with open('data/report_data.json', 'w') as f:
        json.dump(data, f)



def send_tg_msg(message):
    token = os.getenv('TG_TOKEN')
    chat_id = os.getenv('TG_CHAT_ID')
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, data={"chat_id": chat_id, "text": message})

if __name__ == '__main__':
    strategy_instance = run_backtest()
    export_data(strategy_instance)
    final_nav = strategy_instance.broker.getvalue()
    send_tg_msg(f"✅ Report updated. NAV: ${final_nav:.2f}。")