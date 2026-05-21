"""Raphael stock analysis CLI runner."""

import argparse
import asyncio
import json
from pathlib import Path

from ai_client import run_raphael_stock_analysis


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Run Raphael stock analysis via Anthropic.")
    parser.add_argument("input_json", help="Path to request JSON file")
    parser.add_argument("--model", default=None, help="Anthropic model override")
    parser.add_argument("--max-tokens", type=int, default=4000, help="Response max tokens")
    args = parser.parse_args()

    input_path = Path(args.input_json).expanduser().resolve()
    request_data = json.loads(input_path.read_text(encoding="utf-8"))

    result = await run_raphael_stock_analysis(
        request_data=request_data,
        model=args.model,
        max_tokens=args.max_tokens,
    )
    print(result)


if __name__ == "__main__":
    asyncio.run(_main())
