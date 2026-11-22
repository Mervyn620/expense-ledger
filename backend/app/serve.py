import argparse
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".codex"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    stdout_path = STATE_DIR / "expense-app.stdout.log"
    stderr_path = STATE_DIR / "expense-app.stderr.log"
    with (
        stdout_path.open("a", encoding="utf-8", buffering=1) as stdout,
        stderr_path.open("a", encoding="utf-8", buffering=1) as stderr,
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        import uvicorn

        uvicorn.run(
            "app.main:app",
            host="127.0.0.1",
            port=args.port,
            log_level="info",
        )


if __name__ == "__main__":
    raise SystemExit(main())
