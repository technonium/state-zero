#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path

from caption_builder import build_caption


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the canonical State Zero Instagram caption.")
    parser.add_argument("--data", required=True, help="Path to daily_data.json")
    parser.add_argument("--meta", required=True, help="Path to card_metadata.json")
    parser.add_argument("--output", required=True, help="Path to write caption.txt")
    parser.add_argument("--run-date", help="Run date fallback in YYYY-MM-DD format")
    args = parser.parse_args()

    data_path = Path(args.data)
    meta_path = Path(args.meta)
    output_path = Path(args.output)

    daily_data = json.loads(data_path.read_text(encoding="utf-8"))
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    caption = build_caption(metadata, daily_data, run_date=args.run_date)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(caption + "\n", encoding="utf-8")
    print(f"Caption written: {output_path}")


if __name__ == "__main__":
    main()
