"""Entry point: `solarapp` or `python -m solarapp`.

Binds to localhost only — SolarQuick has no authentication and is meant to run
on the machine you are sitting at.
"""
from __future__ import annotations

import argparse
import errno
import os
import socket
import sys
import threading
import webbrowser

DEFAULT_PORT = 8000
DEFAULT_HOST = "127.0.0.1"


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError as e:
            if e.errno in (errno.EADDRINUSE, errno.EACCES):
                return False
            raise


def find_port(host: str, port: int, tries: int = 20) -> int | None:
    """First free port at or above `port`.

    Port 8000 is a popular default, so a first run often collides with
    something else the user already has running. Moving up quietly beats
    dying with "address already in use".
    """
    for candidate in range(port, port + tries):
        if _port_is_free(host, candidate):
            return candidate
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        # no prog=: argparse then reports whichever entry point was actually
        # invoked, rather than printing "solarapp" to someone who ran solarquick
        description="SolarQuick — a quicklook browser for solar physics data.",
    )
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("SOLARQUICK_PORT", DEFAULT_PORT)),
                        help=f"port to serve on (default {DEFAULT_PORT}, "
                             "or $SOLARQUICK_PORT)")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help="interface to bind (default localhost only)")
    parser.add_argument("--no-browser", action="store_true",
                        help="don't open a browser window on startup")
    parser.add_argument("--exact-port", action="store_true",
                        help="fail if --port is taken instead of trying the next one")
    args = parser.parse_args(argv)

    port = args.port
    if not _port_is_free(args.host, port):
        if args.exact_port:
            print(f"Port {port} is already in use. Free it, or pass a different "
                  f"--port.", file=sys.stderr)
            return 1
        found = find_port(args.host, port)
        if found is None:
            print(f"Ports {port}–{port + 19} are all in use. Pass --port with a "
                  f"free one.", file=sys.stderr)
            return 1
        print(f"Port {port} is in use; using {found} instead.", flush=True)
        port = found

    url = f"http://{args.host}:{port}"
    print(f"SolarQuick is starting at {url}  (Ctrl-C to stop)", flush=True)
    if not args.no_browser:
        # after a short delay so the server is accepting connections
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    import uvicorn
    uvicorn.run("solarapp.server.app:app", host=args.host, port=port,
                log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
