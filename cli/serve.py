"""
kugua serve — start the kugua API and dashboard server.

Usage:
  kugua-serve --dashboard              # API + HTML dashboard on port 3847
  kugua-serve --port 5000              # API only on custom port
  kugua-serve --dashboard --port 8080  # Dashboard on custom port
"""

import argparse
import sys


def main():
    p = argparse.ArgumentParser(
        description="kugua serve — start the kugua API and dashboard server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  kugua-serve --dashboard                # Dashboard at http://localhost:3847
  kugua-serve --port 5000                # API only
  kugua-serve --dashboard --port 8080    # Dashboard on custom port
        """,
    )
    p.add_argument(
        "--port", "-p",
        type=int,
        default=3847,
        help="Port to listen on (default: 3847)",
    )
    p.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)",
    )
    p.add_argument(
        "--dashboard", "-d",
        action="store_true",
        default=True,
        help="Enable the HTML dashboard at /dashboard (default: on)",
    )
    p.add_argument(
        "--api-only",
        action="store_true",
        help="API only, no dashboard endpoints",
    )
    args = p.parse_args()

    if args.api_only:
        args.dashboard = False

    try:
        from kugua.api_server import start_server
    except ImportError as e:
        print(f"Error: cannot import kugua.api_server — {e}", file=sys.stderr)
        print("Ensure kugua is installed: pip install -e .", file=sys.stderr)
        return 1

    print(f"\n  kugua serve v0.3")
    print(f"  Host: {args.host}  Port: {args.port}  Dashboard: {'on' if args.dashboard else 'off'}\n")

    try:
        start_server(
            host=args.host,
            port=args.port,
            dashboard=args.dashboard,
        )
    except OSError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
