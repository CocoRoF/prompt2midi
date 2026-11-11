"""Run the server: `python -m prompt2midi.api [--host H] [--port P] [--data-dir DIR]`.

Starts uvicorn with `proxy_headers=False` always. uvicorn's default (`--proxy-headers`) trusts
`X-Forwarded-For` from 127.0.0.1 and rewrites the client address, which lets one forged header per request
get around the per-IP login-failure limit. The app handles forwarded headers itself, only when
`P2M_TRUST_PROXY=1` (docs/api-and-admin.md section 9), so uvicorn's own handling is switched off here.
"""
from __future__ import annotations

import argparse
import os
from typing import Any, Sequence

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m prompt2midi.api", description="prompt2midi web server")
    ap.add_argument("--host", default=DEFAULT_HOST, help=f"bind address (default {DEFAULT_HOST}; use 0.0.0.0 in a container)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})")
    ap.add_argument("--data-dir", default=None, help="state directory (default: $P2M_DATA_DIR or ./instance)")
    ap.add_argument("--log-level", default="info", choices=["critical", "error", "warning", "info", "debug", "trace"])
    return ap.parse_args(argv)


def build_run_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """Arguments for uvicorn.run. proxy_headers is False and not configurable on purpose."""
    return {"host": args.host, "port": args.port, "log_level": args.log_level, "proxy_headers": False, "server_header": False}


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.data_dir:
        os.environ["P2M_DATA_DIR"] = args.data_dir
    import uvicorn

    from .app import create_app

    uvicorn.run(create_app(), **build_run_kwargs(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
