import os
import json
import shutil
import requests
from engine import run_backtest


def export_data(strategy_instance):
    data = {
        "dates": [d.isoformat() for d in strategy_instance.track_dates],
        "nav": strategy_instance.track_nav,
        "regimes": strategy_instance.track_regime,
        "qqq": strategy_instance.track_qqq,
    }

    # Write to data/ (local backup)
    os.makedirs("data", exist_ok=True)
    with open("data/report_data.json", "w") as f:
        json.dump(data, f)

    # Copy into public/data/ so Astro serves it as a static asset
    os.makedirs("public/data", exist_ok=True)
    shutil.copy("data/report_data.json", "public/data/report_data.json")

    print("✅ Data exported to public/data/report_data.json")


def send_tg_msg(message):
    token = os.getenv("TG_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    if not token or not chat_id:
        print("⚠️  TG_TOKEN or TG_CHAT_ID not set, skipping Telegram message.")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, data={"chat_id": chat_id, "text": message})


if __name__ == "__main__":
    strategy_instance = run_backtest()
    export_data(strategy_instance)
    final_nav = strategy_instance.broker.getvalue()
    send_tg_msg(f"✅ Dashboard updated. NAV: ${final_nav:,.2f}")
    print(f"Final NAV: {final_nav:,.2f}")
