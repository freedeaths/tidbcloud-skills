from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def resolve_skill_root(start: Path | None = None) -> Path:
    explicit = os.environ.get("TIDBCLOUD_MANAGER_SKILL_DIR") or os.environ.get("SKILL_DIR")
    if explicit:
        root = Path(explicit).expanduser().resolve()
        return root

    start_path = (start or Path.cwd()).resolve()
    # Common repo-root layout: run from repo root and keep the skill under .codex/skills/.
    repo_skill = start_path / ".codex" / "skills" / "tidbcloud-manager"
    if (repo_skill / "configs").is_dir():
        return repo_skill

    # Common repo-root layout (recommended): run from repo root and keep the skill under skills/.
    repo_skill2 = start_path / "skills" / "tidbcloud-manager"
    if (repo_skill2 / "configs").is_dir():
        return repo_skill2

    for base in (start_path, *start_path.parents):
        if (base / "configs").is_dir():
            return base
        # Don't walk up forever; keep it local and predictable.
        if base.parent == base:
            break

    raise RuntimeError(
        "Cannot locate skill root (missing ./configs). "
        "Run from the skill directory or set TIDBCLOUD_MANAGER_SKILL_DIR."
    )


def load_dotenv_from(root: Path) -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return

    env_file = root / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)


def expand_env_vars(obj: Any) -> Any:
    if isinstance(obj, str):
        def _repl(m: re.Match[str]) -> str:
            key = m.group(1)
            default = m.group(2)
            value = os.environ.get(key)
            if value is None or value == "":
                return default or ""
            return value

        return _ENV_PATTERN.sub(_repl, obj)
    if isinstance(obj, dict):
        return {k: expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [expand_env_vars(v) for v in obj]
    return obj
