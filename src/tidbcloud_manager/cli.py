from __future__ import annotations

import argparse
import sys

from .secure_executor import main as secure_executor_main
from .session_manager import main as session_manager_main
from .knowledge_export import main as knowledge_main
from .openapi_tools import main as openapi_main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tidbcloud-manager")
    sub = parser.add_subparsers(dest="cmd", required=True)

    se = sub.add_parser("secure-exec", help="Execute one request without exposing credentials")
    se.add_argument("mode", choices=["http", "cli", "poll"])
    se.add_argument("request_json")
    se.add_argument("--sut", dest="sut_name", default=None, help="SUT name under ./configs (e.g. tidbx)")

    sm = sub.add_parser("session", help="Manage exploration sessions and generate YAML")
    sm.add_argument("args", nargs=argparse.REMAINDER)

    kn = sub.add_parser("knowledge", help="Export local knowledge to repo YAML")
    kn.add_argument("args", nargs=argparse.REMAINDER)

    oa = sub.add_parser("openapi", help="List/extract operations from openapi.json")
    oa.add_argument("args", nargs=argparse.REMAINDER)

    ns = parser.parse_args(argv)

    if ns.cmd == "secure-exec":
        extra = []
        if ns.sut_name:
            extra = ["--sut", ns.sut_name]
        return secure_executor_main([ns.mode, ns.request_json, *extra])

    if ns.cmd == "session":
        if not ns.args:
            return session_manager_main([])
        # Drop a leading "--" if present (common when forwarding remainder args).
        args = ns.args[1:] if ns.args and ns.args[0] == "--" else ns.args
        return session_manager_main(args)

    if ns.cmd == "knowledge":
        if not ns.args:
            return knowledge_main([])
        args = ns.args[1:] if ns.args and ns.args[0] == "--" else ns.args
        return knowledge_main(args)

    if ns.cmd == "openapi":
        if not ns.args:
            return openapi_main([])
        args = ns.args[1:] if ns.args and ns.args[0] == "--" else ns.args
        return openapi_main(args)

    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
