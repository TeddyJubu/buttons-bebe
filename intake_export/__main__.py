import argparse
import json
import os

from .mcp_read import ExportError, ReadClient
from .snapshot import capture, destination


def main():
    parser = argparse.ArgumentParser(description="Explicit read-only Gorgias test snapshot; never runs inside intake.")
    parser.add_argument("--tickets", type=int, default=50)
    args = parser.parse_args()
    if not 1 <= args.tickets <= 100:
        parser.error("--tickets must be between 1 and 100")
    os.umask(0o077)
    directory = destination()
    try:
        with ReadClient() as client:
            result = capture(client, directory, args.tickets,
                             progress=lambda row: print(json.dumps(row), flush=True))
        print(json.dumps({"directory": str(directory), "counts": result["counts"],
                          "failed": len(result["failed"]), "sample_capture_complete": result["sample_capture_complete"]}), flush=True)
        return 0 if result["sample_capture_complete"] else 1
    except (ExportError, OSError):
        print("Export stopped. Private partial files remain for inspection; no provider writes were attempted.", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
