## tidbcloud-skills

This repo contains:
- `tidbcloud-manager`: a skill for **TiDB Cloud (focusing on TiDBX)** API exploration + YAML scenario generation, plus a small local runner (`tidbcloud-manager`) used by the skill.
- `tidb-zero`: a lightweight skill to create and use temporary/playground TiDB databases without authentication but activation code (no `pip install -e .` required).

`tidbcloud-manager` skill source lives in `skills/tidbcloud-manager/`.
`tidb-zero` skill source lives in `skills/tidb-zero/`.

Supported AI coding assistants:
- **Codex CLI** (OpenAI)
- **OpenCode**
- **Cursor / Windsurf / antigravity**: configure per their skill docs (no extra files needed from this repo).
- **Claude Code**: does not use `SKILL.md` directly; configure per Claude Code docs (you can still use the same `tidbcloud-manager` runner and prompts/rules).

## Setup (venv)

Use any Python environment manager you like (e.g. `uv`, `conda`, `venv`). A virtual environment is recommended but not required.

Below uses `venv` as an example (from repo root):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

`tidbcloud-manager` is a general-purpose local runner/executor. It was originally built to support automated testing workflows, and is reused here as the skill execution backend.

## Optional CLI prerequisites (recommended)

Some tasks (running SQL, managing cloud resources, etc.) require extra CLIs installed locally.

- `mysqlsh` (recommended): used for running SQL against clusters via `tidbcloud-manager secure-exec cli`
- `mysql` (optional fallback): used only if `mysqlsh` is not available
- `aws` / `az` / `gcloud` (optional): only needed when your task touches those clouds

## Install the skill

### Install `tidb-zero` (recommended)

Recommended install command:
```bash
npx skills add https://github.com/tidbcloud/skills --skill tidb-zero
```

### For Codex CLI

Copy or symlink the skill directory to your Codex skills folder:

```bash
mkdir -p ~/.codex/skills
ln -s "$(pwd)/skills/tidbcloud-manager" ~/.codex/skills/tidbcloud-manager
```

### For OpenCode

Copy or symlink the skill directory to your OpenCode skills folder (location depends on your OpenCode installation; follow its docs).

Common locations include:

- `~/.config/opencode/skill/` (global)
- `<repo>/.opencode/skill/` (project-local)

Compatibility list (some installations also check these):

- `~/.opencode/skill/`
- `~/.config/opencode/skills/`
- `~/.opencode/skills/`

Example (global):

```bash
mkdir -p ~/.config/opencode/skill
ln -s "$(pwd)/skills/tidbcloud-manager" ~/.config/opencode/skill/tidbcloud-manager
```

Ensure the `tidbcloud-manager` executable is on `PATH` for new OpenCode sessions.
If you installed into a venv, you can symlink the executable and add it to `PATH`:

```bash
mkdir -p ~/.opencode/bin
ln -s "$(pwd)/.venv/bin/tidbcloud-manager" ~/.opencode/bin/tidbcloud-manager
export PATH="$HOME/.opencode/bin:$PATH"
```

Tip: add the `export PATH=...` line to your shell rc (e.g. `~/.zshrc`) to make it persistent.

## Configure credentials (`.env`)

Copy the `.env.example` to `.env` in the skill directory you installed and fill in values:

```bash
# Example (repo copy)
cp skills/tidbcloud-manager/.env.example skills/tidbcloud-manager/.env

# Example (Codex global install)
cp ~/.codex/skills/tidbcloud-manager/.env.example ~/.codex/skills/tidbcloud-manager/.env
```

Notes:
- `./.env` is auto-loaded when running from the skill directory (or when `TIDBCLOUD_MANAGER_SKILL_DIR` points to it).
- Never commit `.env` (already ignored).

## Run (manual)

Run from the skill directory:

```bash
cd skills/tidbcloud-manager
tidbcloud-manager secure-exec http '{"method":"GET","path":"/clusters"}' --sut tidbx
```

Or run from repo root (auto-detects `./skills/tidbcloud-manager/`):
```bash
tidbcloud-manager secure-exec http '{"method":"GET","path":"/clusters"}' --sut tidbx
```

If you run from another directory and see `Cannot locate skill root (missing ./configs)`, set:

```bash
export TIDBCLOUD_MANAGER_SKILL_DIR="/path/to/skills/tidbcloud-manager"
```

Session workflow:

```bash
tidbcloud-manager session new tidbx demo
tidbcloud-manager session status <session_id>
```

## OpenAPI helpers (optional)

If `openapi.json` is large, use these helpers instead of opening the whole file:

```bash
tidbcloud-manager openapi list --sut tidbx --query cluster
tidbcloud-manager openapi extract --sut tidbx --operation-id ClusterService_CreateCluster
```

## Export knowledge (optional)

After you run successful/failed operations multiple times locally, you can export curated knowledge back into the repo:

```bash
tidbcloud-manager knowledge export --sut tidbx
```

## Dedicated / Premium

The initial open-source release is **serverless-only**. Dedicated support is intentionally not published in the first iteration.

## Prompt examples

Use the skill trigger:

```
# Codex CLI / OpenCode (SKILL.md trigger)
tidbx req: create a cluster named 'cluster-from-agent' with root password '...'
tidbx req: delete the cluster
```
