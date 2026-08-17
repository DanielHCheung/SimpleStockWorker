import os
import json
import shutil
import requests
from engine import run_backtest
from strategies_config import get_enabled_strategies

DATA_DIR = "data/strategies"
PUBLIC_DATA_DIR = "public/data/strategies"


def export_strategy_data(config, strategy_instance):
    """Write one strategy's timeseries to data/strategies/<id>.json and mirror to public/."""
    benchmark_ticker = config.get("benchmark_ticker", config["tickers"][0])
    data = {
        "id": config["id"],
        "name": config["name"],
        "description": config.get("description", ""),
        "params": config.get("params", {}),
        "dates": [d.isoformat() for d in strategy_instance.track_dates],
        "nav": strategy_instance.track_nav,
        "regimes": strategy_instance.track_regime,
        # Generic reference series + which ticker it actually is — not
        # assumed to be QQQ. strategy_instance.track_benchmark is populated
        # by whichever ticker the strategy config set as benchmark_ticker.
        "benchmark_ticker": benchmark_ticker,
        "benchmark": strategy_instance.track_benchmark,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    local_path = os.path.join(DATA_DIR, f"{config['id']}.json")
    with open(local_path, "w") as f:
        json.dump(data, f)

    os.makedirs(PUBLIC_DATA_DIR, exist_ok=True)
    public_path = os.path.join(PUBLIC_DATA_DIR, f"{config['id']}.json")
    shutil.copy(local_path, public_path)

    print(f"✅ {config['id']} exported to {public_path}")
    return data


def export_manifest(entries):
    """Write a manifest.json listing every strategy so the frontend knows what's available."""
    manifest = {
        "strategies": [
            {
                "id": e["id"],
                "name": e["name"],
                "description": e.get("description", ""),
                "final_nav": e["nav"][-1] if e["nav"] else None,
                "start_date": e["dates"][0] if e["dates"] else None,
                "end_date": e["dates"][-1] if e["dates"] else None,
            }
            for e in entries
        ]
    }

    os.makedirs("data", exist_ok=True)
    with open("data/manifest.json", "w") as f:
        json.dump(manifest, f)

    os.makedirs("public/data", exist_ok=True)
    shutil.copy("data/manifest.json", "public/data/manifest.json")
    print("✅ manifest.json exported to public/data/manifest.json")


def send_tg_msg(message):
    token = os.getenv("TG_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    if not token or not chat_id:
        print("⚠️  TG_TOKEN or TG_CHAT_ID not set, skipping Telegram message.")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, data={"chat_id": chat_id, "text": message})


if __name__ == "__main__":
    configs = get_enabled_strategies()
    if not configs:
        raise SystemExit("No enabled strategies found in strategies_config.py")

    exported = []
    summary_lines = []

    for config in configs:
        try:
            strategy_instance = run_backtest(config)
        except Exception as e:
            print(f"❌ Strategy '{config['id']}' failed: {e}")
            summary_lines.append(f"❌ {config['name']}: FAILED ({e})")
            continue

        data = export_strategy_data(config, strategy_instance)
        exported.append(data)

        final_nav = strategy_instance.broker.getvalue()
        summary_lines.append(f"• {config['name']}: ${final_nav:,.2f}")

    export_manifest(exported)

    msg = "✅ Dashboard updated.\n" + "\n".join(summary_lines)
    send_tg_msg(msg)
    print(msg)