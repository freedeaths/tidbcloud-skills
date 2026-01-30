---
name: tidbcloud-manager
description: "TRIGGER: When user says 'tidbx req:' followed by a task. Operate TiDB Cloud TiDBX (Starter/Essential/Premium) and Dedicated using tidbcloud-manager (fast paths + OpenAPI selector)."
---

# TiDB Cloud Manager

Use this skill to operate TiDB Cloud **TiDBX** (and related tiers) via `tidbcloud-manager`.

## Execution strategy

Default flow:
1) Try a **fast path** if the user request matches a common operation.
2) Otherwise use the **OpenAPI selector** (list → select → extract → execute).
3) Only if you still cannot execute after **3 iterations**, inspect `configs/tidbx/openapi.json` directly as a last resort.

Safety:
- Do **not** modify repository code unless the user explicitly asks.
- Do **not** print/echo secrets (API keys, passwords). Prefer `${ENV_VAR}` placeholders when possible.
- Prefer autonomy: only ask the user when required.
  - If the user explicitly requested **create**/**delete** with enough identifiers (e.g. cluster name/password for create; clusterId or an unambiguous selection for delete), treat that as approval and execute.
  - Ask the user only when the request is ambiguous (multiple matches) or missing required inputs (e.g. project/region not provided anywhere).

## Product / OpenAPI selection

SUTs (configs under `./configs/<sut>/`):
- `tidbx`: Starter + Essential (shared OpenAPI). **Default**.
- `tidbx_premium`: Premium (separate OpenAPI file).
- `dedicated`: Dedicated (separate OpenAPI file).

Rule of thumb:
- If the user does not specify a tier, operate on **TiDBX Starter** (`--sut tidbx`).
- If the user explicitly says **Premium**, switch to `--sut tidbx_premium`.
- If the user explicitly says **Dedicated**, switch to `--sut dedicated`.

## Quick prompts (what the user will type)

Examples:
- `tidbx req: create a cluster named 'cluster-from-agent' with root password '4u32hfjfkdlsa'`
- `tidbx req: delete the cluster` (agent should list clusters and ask which one, unless a previous `cluster_1` exists in this conversation)

## Fast paths

### Create cluster

1) Show a **redacted** summary (name / region / project) and proceed immediately if the user explicitly asked to create.
2) Create cluster with `POST /clusters` (operationId: `ClusterService_CreateCluster`).
3) Save `clusterId` as `cluster_1`.
4) Poll `GET /clusters/{cluster_1}` until `ACTIVE`.

#### TiDBX tier rules (Starter vs Essential)

- If the user says just “create a cluster” (no tier mentioned), create **Starter** by default:
  - Omit `"autoScaling"` in the request body.
- If the user explicitly says **Essential**, create Essential:
  - Include `"autoScaling": {"minRcu": ..., "maxRcu": ...}` in the request body.
  - If the user did not mention **both** `minRcu` and `maxRcu`, default to `minRcu=2000`, `maxRcu=4000` (keep any user-provided value).

Default request shape (fill from user prompt and `.env`):
```bash
tidbcloud-manager secure-exec http '{
  "method":"POST",
  "path":"/clusters",
  "body":{
    "displayName":"<name>",
    "labels":{"tidb.cloud/project":"${TIDBCLOUD_PROJECT_ID}"},
    "region":{"name":"${TIDBCLOUD_REGION_NAME:-regions/aws-us-east-1}"},
    "rootPassword":"${TIDBCLOUD_ROOT_PASSWORD:-}"
  }
}' --sut tidbx
```

Essential example (only difference is `autoScaling`):
```bash
tidbcloud-manager secure-exec http '{
  "method":"POST",
  "path":"/clusters",
  "body":{
    "displayName":"<name>",
    "labels":{"tidb.cloud/project":"${TIDBCLOUD_PROJECT_ID}"},
    "region":{"name":"${TIDBCLOUD_REGION_NAME:-regions/aws-us-east-1}"},
    "rootPassword":"${TIDBCLOUD_ROOT_PASSWORD:-}",
    "autoScaling":{"minRcu":2000,"maxRcu":4000}
  }
}' --sut tidbx
```

Then poll:
```bash
tidbcloud-manager secure-exec poll '{"method":"GET","path":"/clusters/<clusterId>","expect":"body.state == ACTIVE","max_retries":60,"delay":10}' --sut tidbx
```

### Delete cluster

- If there is a previously created `cluster_1` in this conversation, delete that.
- Otherwise:
  1) List clusters (`GET /clusters`).
  2) Show a short numbered list (displayName, clusterId, state, region if available).
  3) If there is exactly one obvious target, delete it; otherwise ask the user to pick by **number** or provide a **clusterId**.
  4) Delete by ID (operationId: `ClusterService_DeleteCluster`).

## OpenAPI selector (general operations)

Use this when the request is not covered by fast paths.

### 1) List candidate operations
```bash
tidbcloud-manager openapi list --sut tidbx --query "<keywords>" --limit 50 --format yaml
```

Pick the most relevant `operationId` based on method/path/summary. If multiple are plausible, ask the user to choose.

### 2) Extract one operation (small, focused spec)
```bash
tidbcloud-manager openapi extract --sut tidbx --operation-id <OPERATION_ID> --format yaml
```

### 3) Execute

Generate the HTTP request JSON based on the extracted schema, then execute via `tidbcloud-manager secure-exec ...`.

Retry rules:
- Up to **3 iterations** (adjust request based on errors / missing fields).
- If still failing, inspect `configs/tidbx/openapi.json` directly as a last resort (prefer searching relevant sections first; only `cat` if you really must):
  ```bash
  rg -n '"operationId": "<OPERATION_ID>"' configs/tidbx/openapi.json -n
  # last resort:
  cat configs/tidbx/openapi.json
  ```

## Setup expectations

- Skill directory contains `./configs/` and may contain `./.env`.
- Required env vars: `TIDB_PUBLIC_KEY`, `TIDB_PRIVATE_KEY`, `TIDBCLOUD_PROJECT_ID`.
- Optional env vars: `TIDBCLOUD_REGION_NAME`, `TIDBCLOUD_ROOT_PASSWORD`, `TIDBCLOUD_HOST`, `TIDBCLOUD_BASE_PATH`.

## SQL execution (prefer mysqlsh)

If the user asks to **run SQL** / **write SQL** / **verify by querying**, do not run `mysql` directly in a shell.
Prefer running DB commands via `tidbcloud-manager secure-exec cli` so secrets stay out of the prompt and we can apply fallbacks.

Recommended env vars (set in `.env` or your shell):
- `TIDBCLOUD_DB_HOST`, `TIDBCLOUD_DB_PORT` (default 4000), `TIDBCLOUD_DB_USER` (default root)
- `TIDBCLOUD_ROOT_PASSWORD` (or provide a placeholder in the request body and let the user set it in env)

Run one SQL (mysqlsh first; if mysqlsh missing, fallback to mysql):
```bash
tidbcloud-manager secure-exec cli '{
  "tool":"mysqlsh",
  "args":[
    "--sql",
    "--host","${TIDBCLOUD_DB_HOST:-}",
    "--port","${TIDBCLOUD_DB_PORT:-4000}",
    "--user","${TIDBCLOUD_DB_USER:-root}",
    "--passwords-from-stdin",
    "--execute","SELECT 1;"
  ],
  "stdin":"${TIDBCLOUD_ROOT_PASSWORD:-}",
  "fallback":[
    {
      "tool":"mysql",
      "args":[
        "-h","${TIDBCLOUD_DB_HOST:-}",
        "-P","${TIDBCLOUD_DB_PORT:-4000}",
        "-u","${TIDBCLOUD_DB_USER:-root}",
        "-e","SELECT 1;"
      ],
      "env":{"MYSQL_PWD":"${TIDBCLOUD_ROOT_PASSWORD:-}"}
    }
  ]
}' --sut tidbx
```

## References
- Credentials: `references/credentials.md`
- YAML format: `references/yaml-format.md`
- OpenAPI helpers: `tidbcloud-manager openapi list --sut tidbx` and `tidbcloud-manager openapi extract --sut tidbx --operation-id <ID>`
