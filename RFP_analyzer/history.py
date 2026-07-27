import json
from datetime import datetime
from pathlib import Path

HISTORY_DIR = Path(__file__).parent / "history_data"
HISTORY_DIR.mkdir(exist_ok=True)


def save_analysis(fname: str, result: dict) -> str:
    """Save an analysis result to disk and return its history id."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c for c in fname if c.isalnum() or c in (" ", "_", "-")).strip()
    history_id = f"{timestamp}_{safe_name}"

    record = {
        "id": history_id,
        "fname": fname,
        "timestamp": datetime.now().isoformat(),
        "verdict": result.get("verdict"),
        "fit_score": result.get("fit_score"),
        "result": result,
    }
    with open(HISTORY_DIR / f"{history_id}.json", "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return history_id


def list_history() -> list:
    """Metadata only (no full result) for all saved analyses, newest first."""
    records = []
    for path in HISTORY_DIR.glob("*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            records.append({
                "id": data.get("id", path.stem),
                "fname": data.get("fname", "Unknown"),
                "timestamp": data.get("timestamp", ""),
                "verdict": data.get("verdict"),
                "fit_score": data.get("fit_score"),
            })
        except Exception:
            continue
    records.sort(key=lambda r: r["timestamp"], reverse=True)
    return records


def load_analysis(history_id: str) -> dict:
    with open(HISTORY_DIR / f"{history_id}.json", "r", encoding="utf-8") as f:
        return json.load(f)


def delete_analysis(history_id: str) -> None:
    path = HISTORY_DIR / f"{history_id}.json"
    if path.exists():
        path.unlink()