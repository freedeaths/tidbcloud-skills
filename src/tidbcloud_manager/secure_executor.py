#!/usr/bin/env python3
"""
Secure Executor - Execute HTTP requests and CLI commands without exposing credentials to LLM.

All authentication is handled internally by reading from environment variables or credential files.
The LLM never sees the actual tokens.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from requests.auth import HTTPDigestAuth
import yaml

from .runtime import expand_env_vars, load_dotenv_from, resolve_skill_root


@dataclass
class ExecutionResult:
    """Result of an execution, safe to return to LLM (no credentials)."""

    success: bool
    status_code: Optional[int]
    body: dict
    error: Optional[str]
    duration_ms: int

    def to_json(self) -> str:
        return json.dumps(
            {
                "success": self.success,
                "status_code": self.status_code,
                "body": self.body,
                "error": self.error,
                "duration_ms": self.duration_ms,
            },
            indent=2,
            ensure_ascii=False,
        )


class SecureExecutor:
    """Execute requests without exposing credentials."""

    def __init__(self, sut_name: str = "tidbcloud_serverless", *, skill_root: Path | None = None):
        self.sut_name = sut_name
        self.skill_root = skill_root or resolve_skill_root()
        load_dotenv_from(self.skill_root)

        self.config_dir = self.skill_root / "configs" / sut_name
        self.sut_config = self._load_sut_config()
        self.credentials = self._load_credentials()

    def _load_sut_config(self) -> dict:
        sut_file = self.config_dir / "sut.yaml"
        if sut_file.exists():
            with open(sut_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return expand_env_vars(data)
        return {}

    def _load_credentials(self) -> dict:
        """
        Load credentials from environment variables or credential file.
        NEVER return these to the LLM.
        """
        creds: dict[str, str] = {}

        auth_config = self.sut_config.get("connection", {}).get("auth", {})
        env_vars = auth_config.get("env_vars", {})

        for key, env_name in env_vars.items():
            value = os.environ.get(env_name)
            if value:
                creds[key] = value

        if creds:
            return creds

        # Fallback to credential file (configurable)
        cred_path = auth_config.get("credential_file")
        if cred_path:
            cred_file = Path(os.path.expandvars(str(cred_path))).expanduser()
        else:
            cred_file = Path.home() / ".tidb-credentials.json"

        if cred_file.exists():
            with open(cred_file, "r", encoding="utf-8") as f:
                loaded = json.load(f) or {}
            for k, v in loaded.items():
                if isinstance(v, str) and v:
                    creds[k] = v

        return creds

    def _get_auth(self) -> Optional[HTTPDigestAuth]:
        auth_type = self.sut_config.get("connection", {}).get("auth", {}).get("type", "digest")

        if auth_type == "digest":
            public_key = self.credentials.get("public_key")
            private_key = self.credentials.get("private_key")
            if public_key and private_key:
                return HTTPDigestAuth(public_key, private_key)

        return None

    def _build_url(self, path: str) -> str:
        conn = self.sut_config.get("connection", {})
        host = conn.get("host", "")
        base_path = conn.get("base_path", "").rstrip("/")

        if not path.startswith("/"):
            path = "/" + path

        return f"https://{host}{base_path}{path}"

    def execute_http(self, request: dict) -> ExecutionResult:
        request = expand_env_vars(request)
        method = request.get("method", "GET").upper()
        path = request.get("path", "")
        headers = request.get("headers", {})
        query_params = request.get("query_params", {})
        body = request.get("body")

        url = self._build_url(path)
        auth = self._get_auth()

        if "Content-Type" not in headers and body:
            headers["Content-Type"] = "application/json"

        start_time = time.time()

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=query_params,
                json=body if body else None,
                auth=auth,
                timeout=60,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            try:
                response_body = response.json() if response.content else {}
            except json.JSONDecodeError:
                response_body = {"raw": response.text[:1000]}

            success = 200 <= response.status_code < 300
            error = None if success else f"HTTP {response.status_code}"

            return ExecutionResult(
                success=success,
                status_code=response.status_code,
                body=response_body,
                error=error,
                duration_ms=duration_ms,
            )

        except requests.RequestException as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ExecutionResult(
                success=False,
                status_code=None,
                body={},
                error=str(e),
                duration_ms=duration_ms,
            )

    def execute_cli(self, request: dict) -> ExecutionResult:
        request = expand_env_vars(request)
        tool = request.get("tool", "")
        args = request.get("args", [])
        if not tool:
            return ExecutionResult(
                success=False,
                status_code=None,
                body={},
                error="Missing 'tool' for CLI request",
                duration_ms=0,
            )

        if not isinstance(args, list):
            return ExecutionResult(
                success=False,
                status_code=None,
                body={},
                error="'args' must be a list",
                duration_ms=0,
            )

        cmd = [tool] + [str(a) for a in args]
        start_time = time.time()

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            duration_ms = int((time.time() - start_time) * 1000)

            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            success = result.returncode == 0

            body: dict
            if stdout:
                try:
                    body = json.loads(stdout)
                except json.JSONDecodeError:
                    body = {"stdout": stdout[:2000]}
            else:
                body = {}

            if stderr:
                body["stderr"] = stderr[:2000]

            return ExecutionResult(
                success=success,
                status_code=result.returncode,
                body=body,
                error=None if success else f"Exit {result.returncode}",
                duration_ms=duration_ms,
            )

        except subprocess.SubprocessError as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ExecutionResult(
                success=False,
                status_code=None,
                body={},
                error=str(e),
                duration_ms=duration_ms,
            )

    def poll_until_ready(
        self, request: dict, expect_cel: str, max_retries: int = 60, delay_seconds: int = 30
    ) -> ExecutionResult:
        last: Optional[ExecutionResult] = None
        for _ in range(max_retries):
            last = self.execute_http(request)
            if last.success and self._evaluate_expect(last.body, expect_cel):
                return last
            time.sleep(delay_seconds)
        return last or ExecutionResult(
            success=False, status_code=None, body={}, error="No attempts executed", duration_ms=0
        )

    def _evaluate_expect(self, body: dict, expect: str) -> bool:
        """
        Minimal CEL-like evaluator, intentionally tiny:
        - Supports "body.foo == 'BAR'" or "body.foo == BAR"
        """
        if not expect:
            return True

        expr = expect.strip()
        if "==" not in expr:
            return False

        left, right = [p.strip() for p in expr.split("==", 1)]
        if left.startswith("body."):
            path = left[5:]
        else:
            return False

        expected = right.strip().strip("'").strip('"')

        current: object = body
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return False

        return str(current) == expected


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if len(argv) < 2:
        print(
            json.dumps(
                {
                    "success": False,
                    "error": "Usage: tidbcloud-manager secure-exec <http|cli|poll> '<json_request>' [--sut <sut_name>]",
                    "examples": [
                        'tidbcloud-manager secure-exec http \'{"method":"GET","path":"/clusters"}\' --sut tidbcloud_serverless',
                        'tidbcloud-manager secure-exec cli \'{"tool":"aws","args":["ec2","describe-instances"]}\' --sut tidbcloud_dedicated',
                        'tidbcloud-manager secure-exec poll \'{"method":"GET","path":"/clusters/123","expect":"body.state == ACTIVE","max_retries":60,"delay":30}\' --sut tidbcloud_serverless',
                    ],
                }
            )
        )
        return 1

    command = argv[0]
    request_json = argv[1]

    sut_name = os.environ.get("TIDBCLOUD_SUT", "tidbcloud_serverless")
    # Compatibility: allow either `--sut <name>` or a 3rd positional arg (legacy script style).
    if "--sut" in argv:
        i = argv.index("--sut")
        if i + 1 < len(argv):
            sut_name = argv[i + 1]
    elif len(argv) >= 3 and not argv[2].startswith("-"):
        sut_name = argv[2]

    try:
        request = json.loads(request_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"success": False, "error": f"Invalid JSON: {e}"}))
        return 1

    executor = SecureExecutor(sut_name)

    if command == "http":
        result = executor.execute_http(request)
    elif command == "cli":
        result = executor.execute_cli(request)
    elif command == "poll":
        expect = request.pop("expect", "")
        max_retries = int(request.pop("max_retries", 60))
        delay = int(request.pop("delay", 30))
        result = executor.poll_until_ready(request, expect, max_retries, delay)
    else:
        print(json.dumps({"success": False, "error": f"Unknown command: {command}. Use http, cli, or poll."}))
        return 1

    print(result.to_json())
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
