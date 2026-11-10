"""Regenerate the Angular client's input contract from FastAPI."""

import json
from pathlib import Path

from ph_web.app import create_app


def main():
    target = Path(__file__).resolve().parents[1] / "web" / "openapi.json"
    contract = create_app().openapi()
    target.write_bytes((json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
