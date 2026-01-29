## tidbcloud-skills

This repo contains a skill for **TiDB Cloud Serverless** API exploration + YAML scenario generation, plus a small local runner (`tidbcloud-manager`) used by the skill.

Skill source lives in `skills/tidbcloud-manager/`.

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

## Install the skill

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

Example (global):

```bash
mkdir -p ~/.config/opencode/skill
ln -s "$(pwd)/skills/tidbcloud-manager" ~/.config/opencode/skill/tidbcloud-manager
```

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
tidbcloud-manager secure-exec http '{"method":"GET","path":"/clusters"}' --sut tidbcloud_serverless
```

Or run from repo root (auto-detects `./skills/tidbcloud-manager/`):
```bash
tidbcloud-manager secure-exec http '{"method":"GET","path":"/clusters"}' --sut tidbcloud_serverless
```

Session workflow:

```bash
tidbcloud-manager session new tidbcloud_serverless demo
tidbcloud-manager session status <session_id>
```

## OpenAPI helpers (optional)

If `openapi.json` is large, use these helpers instead of opening the whole file:

```bash
tidbcloud-manager openapi list --sut tidbcloud_serverless --query cluster
tidbcloud-manager openapi extract --sut tidbcloud_serverless --operation-id ClusterService_CreateCluster
```

## Export knowledge (optional)

After you run successful/failed operations multiple times locally, you can export curated knowledge back into the repo:

```bash
tidbcloud-manager knowledge export --sut tidbcloud_serverless
```

## Dedicated

The initial open-source release is **serverless-only**. Dedicated support is intentionally not published in the first iteration.

## Prompt examples

Use the skill trigger:

```
# Codex CLI / OpenCode (SKILL.md trigger)
tidb serverless req: create a cluster named 'cluster-from-agent' with root password '...'
tidb serverless req: delete the cluster
```
