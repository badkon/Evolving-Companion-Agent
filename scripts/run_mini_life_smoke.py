"""Offline routine projection; no DB initialization or API calls."""

from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.mini_life import MiniLifeService


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    seed = load_character_seed_data(root / "data/characters/si_001.yaml")
    service = MiniLifeService(seed)
    for hour, minute in ((8, 0), (10, 0), (15, 0), (20, 0), (23, 30)):
        now = datetime(2026, 10, 5, hour, minute, tzinfo=ZoneInfo(seed.timezone))
        print("time:", now.isoformat())
        print(json.dumps(asdict(service.build(now)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
