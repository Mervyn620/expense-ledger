import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.collector_backends import (  # noqa: E402
    CollectorBackendError,
    CollectorBackendManager,
    MODES,
)


def parser():
    command_parser = argparse.ArgumentParser(
        description="Inspect and select the expense collection execution backend."
    )
    command_parser.add_argument("--root", type=Path, default=ROOT)
    subparsers = command_parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status")
    status.add_argument("--refresh", action="store_true")
    select = subparsers.add_parser("select")
    select.add_argument("--mode", choices=sorted(MODES), required=True)
    resolve = subparsers.add_parser("resolve")
    resolve.add_argument("--mode", choices=sorted(MODES))
    return command_parser


def main(argv=None):
    args = parser().parse_args(argv)
    manager = CollectorBackendManager(root=args.root)
    try:
        if args.command == "status":
            payload = manager.status(force=args.refresh)
        elif args.command == "select":
            payload = manager.set_mode(args.mode)
        else:
            payload = manager.resolve(args.mode, force=True)
    except CollectorBackendError as exc:
        print(json.dumps({
            "status": "failed",
            "message": str(exc),
        }, ensure_ascii=False))
        return 2
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
