#!/usr/bin/env python3
"""
Session Manager - Manages E2E exploration sessions with attempt tracking and YAML generation.

This module provides:
1. Session state management (variables, attempts, draft_yaml)
2. Variable substitution ({cluster_1} → real value before execution)
3. Value extraction from responses (save: cluster_1 = body.clusterId)
4. Dependency tracking (which step depends on which saved variable)
5. Commands:
   - execute(operation, request, save_config) → record attempt + update variables
   - status() → show current session state
   - summary() → review all attempts, remove failures/redundant, generate final YAML
   - rerun(yaml_file) → execute YAML to verify repeatability
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml

from .runtime import canonical_sut_name, expand_env_vars, load_dotenv_from, resolve_skill_root
from .secure_executor import ExecutionResult, SecureExecutor


_SENSITIVE_KEY_RE = re.compile(r"(password|private[_-]?key|token|secret|pwd)", re.IGNORECASE)
_PLACEHOLDER_VALUE_RE = re.compile(r"^\{[A-Za-z0-9_]+\}$")


def _to_placeholder_name(key: str) -> str:
    key = re.sub(r"[^A-Za-z0-9]+", "_", key)
    key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    key = key.strip("_").lower()
    return key or "redacted"


def redact_sensitive_values(obj: Any) -> Any:
    """
    Redact sensitive values for persistence (session files / YAML).

    Rules:
    - For keys that look like secrets (password/private_key/token/secret), replace the value with a placeholder
      like "{root_password}" so outputs stay reusable without leaking real values.
    - If the value already looks like a placeholder (e.g. "{root_password}"), keep it.
    """
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SENSITIVE_KEY_RE.search(k):
                if isinstance(v, str) and _PLACEHOLDER_VALUE_RE.match(v):
                    out[k] = v
                else:
                    out[k] = "{" + _to_placeholder_name(k) + "}"
                continue
            out[k] = redact_sensitive_values(v)
        return out
    if isinstance(obj, list):
        return [redact_sensitive_values(v) for v in obj]
    return obj


@dataclass
class Attempt:
    """A single execution attempt (success or failure)."""

    index: int
    timestamp: str
    operation_id: str
    request_type: str  # http, cli
    request: dict  # Original request with placeholders
    resolved_request: dict  # Request with variables substituted
    response: dict
    success: bool
    error: Optional[str]
    duration_ms: int
    saved_variables: dict = field(default_factory=dict)  # Variables extracted from this attempt (values)
    save_config: Optional[dict] = None  # Original save config (eval paths), for YAML generation

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Step:
    """A step in the final YAML (success attempts only, cleaned up)."""

    name: str
    operation_id: str
    request_type: str
    request: dict
    expect: dict
    save: Optional[dict] = None
    max_retries: int = 0
    delay_after: int = 0

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "operation_id": self.operation_id,
            "request_type": self.request_type,
            "request": self.request,
            "expect": self.expect,
        }
        if self.save:
            d["save"] = self.save
        if self.max_retries > 0:
            d["max_retries"] = self.max_retries
        if self.delay_after > 0:
            d["delay_after"] = self.delay_after
        return d


class SessionManager:
    """Manages an E2E exploration session."""

    def __init__(
        self,
        sut_name: str,
        scenario_name: str,
        session_id: Optional[str] = None,
        *,
        skill_root: Path | None = None,
    ):
        self.session_id = session_id or f"ses_{uuid.uuid4().hex[:12]}"
        self.sut_name = canonical_sut_name(sut_name)
        self.scenario_name = scenario_name
        self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at

        self.skill_root = skill_root or resolve_skill_root()
        load_dotenv_from(self.skill_root)

        self.output_dir = self.skill_root / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session_file = self.output_dir / f".session_{self.session_id}.json"
        self.draft_yaml_file = self.output_dir / f".draft_{self.scenario_name}.yaml"
        self.final_yaml_file = self.output_dir / f"{self.scenario_name}.yaml"

        self.config_dir = self.skill_root / "configs" / self.sut_name
        self.sut_config = self._load_sut_config()

        self.variables: dict[str, Any] = {}
        self.attempts: list[Attempt] = []

        preset_vars = self.sut_config.get("preset_variables", {})
        self.variables.update(preset_vars)

        self.executor = SecureExecutor(self.sut_name, skill_root=self.skill_root)

    def _load_sut_config(self) -> dict:
        sut_file = self.config_dir / "sut.yaml"
        if sut_file.exists():
            with open(sut_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return expand_env_vars(data)
        return {}

    # =========================================================================
    # Variable Substitution
    # =========================================================================

    def substitute_variables(self, obj: Any) -> Any:
        """Replace {placeholder} with actual values from self.variables."""
        if isinstance(obj, str):
            pattern = r"\{([^}]+)\}"
            matches = re.findall(pattern, obj)
            result = obj
            for var_name in matches:
                if var_name in self.variables:
                    result = result.replace(f"{{{var_name}}}", str(self.variables[var_name]))
            return result
        if isinstance(obj, dict):
            return {k: self.substitute_variables(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.substitute_variables(item) for item in obj]
        return obj

    def find_required_variables(self, obj: Any) -> set[str]:
        """Find all {placeholder} references in a request."""
        required: set[str] = set()
        if isinstance(obj, str):
            pattern = r"\{([^}]+)\}"
            required.update(re.findall(pattern, obj))
        elif isinstance(obj, dict):
            for v in obj.values():
                required.update(self.find_required_variables(v))
        elif isinstance(obj, list):
            for item in obj:
                required.update(self.find_required_variables(item))
        return required

    # =========================================================================
    # Value Extraction
    # =========================================================================

    def extract_value(self, response: dict, eval_path: str) -> Optional[Any]:
        """
        Extract a value from response using eval path like 'body.clusterId' or
        'body.tidbNodeSetting.tidbNodeGroups[0].tidbNodeGroupId'.
        """
        if eval_path.startswith("body."):
            eval_path = eval_path[5:]

        current: Any = response
        for part in self._parse_path(eval_path):
            if isinstance(part, int):
                if isinstance(current, list) and len(current) > part:
                    current = current[part]
                else:
                    return None
            else:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    return None

        return current

    def _parse_path(self, path: str) -> list[Any]:
        parts: list[Any] = []
        for segment in path.split("."):
            if "[" in segment:
                base = segment[: segment.index("[")]
                if base:
                    parts.append(base)
                idx_str = segment[segment.index("[") + 1 : segment.index("]")]
                parts.append(int(idx_str))
            else:
                parts.append(segment)
        return parts

    def extract_and_save(self, response: dict, save_config: Optional[dict]) -> dict:
        """
        Extract values from response and save to variables.

        save_config format:
          {"placeholder":[{"key":"cluster_1","eval":"body.clusterId"}]}
        """
        saved: dict[str, Any] = {}
        if not save_config:
            return saved

        placeholders = save_config.get("placeholder", [])
        for p in placeholders:
            key = p.get("key")
            eval_path = p.get("eval")
            if key and eval_path:
                value = self.extract_value(response, eval_path)
                if value is not None:
                    self.variables[key] = value
                    saved[key] = value

        return saved

    # =========================================================================
    # Execution
    # =========================================================================

    def execute(
        self,
        operation_id: str,
        request: dict,
        save_config: Optional[dict] = None,
        request_type: str = "http",
    ) -> Attempt:
        required = self.find_required_variables(request)
        missing = required - set(self.variables.keys())
        if missing:
            print(f"Warning: Missing variables: {missing}", file=sys.stderr)

        resolved_request = self.substitute_variables(request)
        resolved_request = expand_env_vars(resolved_request)

        if request_type == "http":
            result = self.executor.execute_http(resolved_request)
        elif request_type == "cli":
            result = self.executor.execute_cli(resolved_request)
        else:
            result = ExecutionResult(
                success=False,
                status_code=None,
                body={},
                error=f"Unknown request_type: {request_type}",
                duration_ms=0,
            )

        saved_vars = self.extract_and_save(result.body, save_config) if result.success else {}

        attempt = Attempt(
            index=len(self.attempts),
            timestamp=datetime.now().isoformat(),
            operation_id=operation_id,
            request_type=request_type,
            request=request,
            resolved_request=resolved_request,
            response={"status_code": result.status_code, "body": result.body},
            success=result.success,
            error=result.error,
            duration_ms=result.duration_ms,
            saved_variables=saved_vars,
            save_config=save_config,
        )
        self.attempts.append(attempt)

        self._update_draft_yaml()
        self.save()

        return attempt

    # =========================================================================
    # Status
    # =========================================================================

    def status(self) -> dict:
        success_count = sum(1 for a in self.attempts if a.success)
        failure_count = len(self.attempts) - success_count

        return {
            "session_id": self.session_id,
            "sut": self.sut_name,
            "scenario": self.scenario_name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "variables": redact_sensitive_values(self.variables),
            "attempts": {"total": len(self.attempts), "success": success_count, "failure": failure_count},
            "files": {
                "session": str(self.session_file),
                "draft_yaml": str(self.draft_yaml_file),
                "final_yaml": str(self.final_yaml_file) if self.final_yaml_file.exists() else None,
            },
            "recent_attempts": [
                {
                    "index": a.index,
                    "operation_id": a.operation_id,
                    "success": a.success,
                    "saved": list(a.saved_variables.keys()) if a.saved_variables else [],
                }
                for a in self.attempts[-5:]
            ],
        }

    # =========================================================================
    # Summary - Review and Generate Final YAML
    # =========================================================================

    def summary(self, remove_indices: Optional[list[int]] = None) -> dict:
        remove_set = set(remove_indices or [])

        final_steps: list[Step] = []
        saved_vars_chain: dict[str, int] = {}
        required_vars_chain: dict[str, list[int]] = {}

        step_number = 0
        for attempt in self.attempts:
            if attempt.index in remove_set:
                continue
            if not attempt.success:
                continue

            step_number += 1

            for var_name in attempt.saved_variables:
                saved_vars_chain[var_name] = step_number

            required = self.find_required_variables(attempt.request)
            for var_name in required:
                required_vars_chain.setdefault(var_name, []).append(step_number)

            step = Step(
                name=f"step_{step_number}_{self._operation_to_name(attempt.operation_id)}",
                operation_id=attempt.operation_id,
                request_type=attempt.request_type,
                request=redact_sensitive_values(attempt.request),
                expect={"status_code": attempt.response.get("status_code", 200)},
                save=attempt.save_config if attempt.save_config else None,
            )
            final_steps.append(step)

        validation_errors = []
        preset = set((self.sut_config.get("preset_variables", {}) or {}).keys())
        for var_name, step_indices in required_vars_chain.items():
            if var_name in preset:
                continue
            if var_name not in saved_vars_chain:
                validation_errors.append(f"Variable '{var_name}' used in step(s) {step_indices} but never saved")
                continue
            save_step = saved_vars_chain[var_name]
            for use_step in step_indices:
                if use_step <= save_step:
                    validation_errors.append(
                        f"Variable '{var_name}' used in step {use_step} before being saved in step {save_step}"
                    )

        self._generate_final_yaml(final_steps)

        return {
            "total_attempts": len(self.attempts),
            "success_attempts": sum(1 for a in self.attempts if a.success),
            "removed_attempts": len(remove_set),
            "final_steps": len(final_steps),
            "saved_variables": list(saved_vars_chain.keys()),
            "validation_errors": validation_errors,
            "final_yaml": str(self.final_yaml_file),
        }

    def _operation_to_name(self, operation_id: str) -> str:
        if "_" in operation_id:
            parts = operation_id.split("_")
            return "_".join(p.lower() for p in parts[1:])
        return operation_id.lower()

    def _generate_final_yaml(self, steps: list[Step]) -> None:
        conn = self.sut_config.get("connection", {})
        output = {
            "scenario": {"name": self.scenario_name, "description": f"Auto-generated from session {self.session_id}"},
            "connection": {
                "host": conn.get("host", ""),
                "base_path": conn.get("base_path", ""),
                "auth": {"type": conn.get("auth", {}).get("type", "digest")},
            },
            "steps": [s.to_dict() for s in steps],
        }
        with open(self.final_yaml_file, "w", encoding="utf-8") as f:
            yaml.dump(output, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    # =========================================================================
    # Rerun - Execute Final YAML
    # =========================================================================

    def rerun(self) -> dict:
        if not self.final_yaml_file.exists():
            return {"success": False, "error": "No final YAML file. Run summary first."}

        with open(self.final_yaml_file, "r", encoding="utf-8") as f:
            yaml_data = yaml.safe_load(f) or {}

        steps = yaml_data.get("steps", [])

        rerun_session = SessionManager(
            self.sut_name,
            f"{self.scenario_name}_rerun_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            skill_root=self.skill_root,
        )

        results = []
        all_success = True
        for step in steps:
            operation_id = step.get("operation_id", "")
            request_type = step.get("request_type", "http")
            request = step.get("request", {})
            save_config = step.get("save")
            max_retries = step.get("max_retries", 0)
            delay_after = step.get("delay_after", 0)

            if max_retries > 0:
                expect = step.get("expect", {})
                cel_condition = expect.get("cel", "")
                if cel_condition and request_type == "http":
                    resolved_request = rerun_session.substitute_variables(request)
                    result = rerun_session.executor.poll_until_ready(
                        resolved_request,
                        cel_condition,
                        max_retries=max_retries,
                        delay_seconds=delay_after or 30,
                    )
                    attempt = Attempt(
                        index=len(rerun_session.attempts),
                        timestamp=datetime.now().isoformat(),
                        operation_id=operation_id,
                        request_type=request_type,
                        request=request,
                        resolved_request=resolved_request,
                        response={"status_code": result.status_code, "body": result.body},
                        success=result.success,
                        error=result.error,
                        duration_ms=result.duration_ms,
                        saved_variables=rerun_session.extract_and_save(result.body, save_config)
                        if result.success
                        else {},
                        save_config=save_config,
                    )
                    rerun_session.attempts.append(attempt)
                else:
                    attempt = rerun_session.execute(operation_id, request, save_config, request_type)
            else:
                attempt = rerun_session.execute(operation_id, request, save_config, request_type)

            results.append(
                {
                    "step": step.get("name", operation_id),
                    "success": attempt.success,
                    "error": attempt.error,
                    "saved": list(attempt.saved_variables.keys()),
                }
            )

            if not attempt.success:
                all_success = False

        rerun_session.save()

        return {"success": all_success, "rerun_session_id": rerun_session.session_id, "steps": results, "variables": rerun_session.variables}

    # =========================================================================
    # Draft YAML (all attempts, including failures)
    # =========================================================================

    def _update_draft_yaml(self) -> None:
        conn = self.sut_config.get("connection", {})

        steps = []
        for attempt in self.attempts:
            safe_attempt = redact_sensitive_values(
                {
                    "index": attempt.index,
                    "timestamp": attempt.timestamp,
                    "operation_id": attempt.operation_id,
                    "request_type": attempt.request_type,
                    "request": attempt.request,
                    "resolved_request": attempt.resolved_request,
                    "response": attempt.response,
                    "success": attempt.success,
                    "error": attempt.error,
                    "duration_ms": attempt.duration_ms,
                    "saved_variables": attempt.saved_variables,
                    "save_config": attempt.save_config,
                }
            )
            steps.append(
                {
                    "index": safe_attempt["index"],
                    "timestamp": safe_attempt["timestamp"],
                    "operation_id": safe_attempt["operation_id"],
                    "request_type": safe_attempt["request_type"],
                    "request": safe_attempt["request"],
                    "resolved_request": safe_attempt["resolved_request"],
                    "response": safe_attempt["response"],
                    "success": safe_attempt["success"],
                    "error": safe_attempt["error"],
                    "duration_ms": safe_attempt["duration_ms"],
                    "saved_variables": safe_attempt["saved_variables"],
                    "save_config": safe_attempt["save_config"],
                }
            )

        output = {
            "session": {"id": self.session_id, "sut": self.sut_name, "scenario": self.scenario_name, "created_at": self.created_at},
            "connection": {"host": conn.get("host", ""), "base_path": conn.get("base_path", "")},
            "variables": redact_sensitive_values(self.variables),
            "attempts": steps,
        }

        with open(self.draft_yaml_file, "w", encoding="utf-8") as f:
            yaml.dump(output, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    # =========================================================================
    # Save/Load Session
    # =========================================================================

    def save(self) -> str:
        self.updated_at = datetime.now().isoformat()
        data = redact_sensitive_values(
            {
                "session_id": self.session_id,
                "sut_name": self.sut_name,
                "scenario_name": self.scenario_name,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "variables": self.variables,
                "attempts": [a.to_dict() for a in self.attempts],
            }
        )
        with open(self.session_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return str(self.session_file)

    @classmethod
    def load(cls, session_file: str, *, skill_root: Path | None = None) -> "SessionManager":
        with open(session_file, "r", encoding="utf-8") as f:
            data = json.load(f) or {}

        session = cls(
            sut_name=data["sut_name"],
            scenario_name=data["scenario_name"],
            session_id=data["session_id"],
            skill_root=skill_root,
        )
        session.created_at = data.get("created_at", session.created_at)
        session.updated_at = data.get("updated_at", session.updated_at)
        session.variables = data.get("variables", {}) or {}
        session.attempts = [Attempt(**a) for a in (data.get("attempts", []) or [])]
        return session

    @classmethod
    def find_session(cls, session_id: str, *, skill_root: Path | None = None) -> Optional["SessionManager"]:
        root = skill_root or resolve_skill_root()
        load_dotenv_from(root)
        output_dir = root / "output"
        session_file = output_dir / f".session_{session_id}.json"
        if session_file.exists():
            return cls.load(str(session_file), skill_root=root)
        return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 1:
        print(
            json.dumps(
                {
                    "success": False,
                    "error": "Usage: tidbcloud-manager session <command> [args]",
                    "commands": {
                        "new": "new <sut_name> <scenario_name>",
                        "execute": "execute <session_id> <operation_id> '<request_json>' ['<save_json>'] [request_type]",
                        "status": "status <session_id>",
                        "summary": "summary <session_id> [--remove <index>]...",
                        "rerun": "rerun <session_id>",
                    },
                },
                indent=2,
            )
        )
        return 1

    command = argv[0]
    try:
        if command == "new":
            if len(argv) < 3:
                print(json.dumps({"success": False, "error": "Usage: new <sut_name> <scenario_name>"}))
                return 1
            sut_name = argv[1]
            scenario_name = argv[2]
            session = SessionManager(sut_name, scenario_name)
            session.save()
            print(
                json.dumps(
                    {"success": True, "session_id": session.session_id, "session_file": str(session.session_file), "variables": session.variables},
                    indent=2,
                )
            )
            return 0

        if command == "execute":
            if len(argv) < 4:
                print(
                    json.dumps(
                        {
                            "success": False,
                            "error": "Usage: execute <session_id> <operation_id> '<request_json>' ['<save_json>'] [request_type]",
                        }
                    )
                )
                return 1
            session_id = argv[1]
            operation_id = argv[2]
            request = json.loads(argv[3])
            save_config = json.loads(argv[4]) if len(argv) > 4 else None
            request_type = argv[5] if len(argv) > 5 else "http"

            session = SessionManager.find_session(session_id)
            if not session:
                print(json.dumps({"success": False, "error": f"Session not found: {session_id}"}))
                return 1

            attempt = session.execute(operation_id, request, save_config, request_type)
            print(
                json.dumps(
                    {
                        "success": attempt.success,
                        "attempt_index": attempt.index,
                        "operation_id": attempt.operation_id,
                        "status_code": attempt.response.get("status_code"),
                        "body": attempt.response.get("body", {}),
                        "error": attempt.error,
                        "duration_ms": attempt.duration_ms,
                        "saved_variables": attempt.saved_variables,
                        "all_variables": session.variables,
                    },
                    indent=2,
                )
            )
            return 0 if attempt.success else 1

        if command == "status":
            if len(argv) < 2:
                print(json.dumps({"success": False, "error": "Usage: status <session_id>"}))
                return 1
            session_id = argv[1]
            session = SessionManager.find_session(session_id)
            if not session:
                print(json.dumps({"success": False, "error": f"Session not found: {session_id}"}))
                return 1
            print(json.dumps(session.status(), indent=2))
            return 0

        if command == "summary":
            if len(argv) < 2:
                print(json.dumps({"success": False, "error": "Usage: summary <session_id> [--remove <index>]..."}))
                return 1
            session_id = argv[1]

            remove_indices = []
            i = 2
            while i < len(argv):
                if argv[i] == "--remove" and i + 1 < len(argv):
                    remove_indices.append(int(argv[i + 1]))
                    i += 2
                else:
                    i += 1

            session = SessionManager.find_session(session_id)
            if not session:
                print(json.dumps({"success": False, "error": f"Session not found: {session_id}"}))
                return 1

            result = session.summary(remove_indices if remove_indices else None)
            print(json.dumps(result, indent=2))
            return 0

        if command == "rerun":
            if len(argv) < 2:
                print(json.dumps({"success": False, "error": "Usage: rerun <session_id>"}))
                return 1
            session_id = argv[1]
            session = SessionManager.find_session(session_id)
            if not session:
                print(json.dumps({"success": False, "error": f"Session not found: {session_id}"}))
                return 1
            result = session.rerun()
            print(json.dumps(result, indent=2))
            return 0 if result.get("success") else 1

        print(json.dumps({"success": False, "error": f"Unknown command: {command}"}))
        return 1

    except json.JSONDecodeError as e:
        print(json.dumps({"success": False, "error": f"Invalid JSON: {e}"}))
        return 1
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
