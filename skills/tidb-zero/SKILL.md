---
name: tidb-zero
description: "Create and use TiDB Zero temporary/playground TiDB databases via unauthenticated REST API using curl (no tidbcloud-manager / no pip). TRIGGER: tidb0, ti0, tidb-zero, instant tidb cluster,temporary tidb database, playground, temp, 快速创建临时集群, 临时集群, 申请激活码. Supports only 2 operations: (1) request activation code, (2) quickly create a temporary TiDB database/cluster with required activationCode and optional namePrefix/password. After creation, always print id, name, connection(host, port, username, password), expiresAt, remainingDatabaseQuota. For follow-up DB tasks, run SQL using mysqlsh or mysql CLI with the returned connection."
---

# TiDB Zero (Temporary / Playground Database)

## Setup

Required env:
- `TIDBZERO_HOST`: API host (no auth)

Optional env:
- `TIDBZERO_BASE_PATH` (default `/v1alpha1`)
- `TIDB0_ACTIVATION_CODE` (reuse existing activation code)
- `TIDB0_NAME_PREFIX` (default `codex-`)
- `TIDB0_DB_PASSWORD` (optional)

Timeout env:
- `TIDB0_CURL_CONNECT_TIMEOUT` (default `10`)
- `TIDB0_CURL_MAX_TIME` (default `60`)

Prereqs:
- HTTP: `curl`, `jq`
- SQL: `mysqlsh` (preferred) or `mysql`

OpenAPI helpers (no Python):
- `skills/tidb-zero/scripts/tidb0_openapi_list.sh`
- `skills/tidb-zero/scripts/tidb0_openapi_extract.sh` (keeps meta: info/host/basePath/schemes/consumes/produces)

## Fallbacks (permissions / OS / missing tools)

If the helper scripts fail:

1) Permission denied:
```bash
chmod +x skills/tidb-zero/scripts/*.sh
# or bypass executable bit:
bash skills/tidb-zero/scripts/tidb0_openapi_list.sh --limit 20
```

2) Shell/OS mismatch (Windows/macOS/Linux):
```bash
uname -a || ver
printf 'SHELL=%s\n' "${SHELL:-}"
bash --version || true
```
(Prefer WSL or Git Bash on Windows.)

3) Dependencies:
```bash
command -v curl
command -v jq
```

4) No-script fallback (pure jq):

List operations:
```bash
jq -r '[
  .paths
  | to_entries[]
  | .key as $path
  | .value
  | to_entries[]
  | select(.key | test("^(get|post|put|delete|patch|head|options)$"))
  | .key as $method
  | .value as $op
  | select(($op.operationId // "") != "")
  | {operationId:$op.operationId, method:($method|ascii_upcase), path:$path, summary:($op.summary // $op.description // "")}
] | sort_by(.operationId)
  | .[] | [.operationId,.method,.path,.summary] | @tsv' \
  skills/tidb-zero/configs/openapi.json
```

Extract method/path/basePath for an operationId:
```bash
OPID='PublicShadowPoolService_RequireInstance'
jq -n --arg opid "$OPID" --slurpfile s skills/tidb-zero/configs/openapi.json '
  ($s[0]) as $spec
  | {
      operationId:$opid,
      meta:{info:($spec.info//{}), basePath:($spec.basePath//""), schemes:($spec.schemes//[]), consumes:($spec.consumes//[]), produces:($spec.produces//[])},
      hit:(
        $spec.paths
        | to_entries[]
        | .key as $path
        | .value
        | to_entries[]
        | select(.value.operationId == $opid)
        | {method:(.key|ascii_upcase), path:$path}
      )
    }
  | .method = .hit.method
  | .path = .hit.path
  | del(.hit)

## Intent routing

- HTTP intent (activation code / create temporary cluster / create instant database/ etc): use `curl` + `jq`.
- SQL intent (create table/ query data/ modify schema/ verify data): use `mysqlsh` or `mysql` with the `connection` returned by create-db.

## Autonomy rule (minimize human involvement)

- Try to execute without asking the user.
- If a request fails, adjust and retry based on the response.
- If there are **3 consecutive failures**, stop and ask the user for:
  - `TIDBZERO_HOST` (and whether it needs `http://` instead of `https://`)
  - `TIDBZERO_BASE_PATH`
  - activation code status (new/expired/quota)
  - the last `HTTP <status>` + response JSON

## OpenAPI usage rule (when to list/extract)

- First attempt should list operations.
- Extract the exact operation in each http request unless you **really** already know the exact operation.
- If you see `HTTP 404/405/4XX` (path/method mismatch, etc.) or response shape is unexpected:
  1) list operations
  2) extract the exact operation
  3) retry `curl` using extracted `.method`, `.path`, and `.meta.basePath`

List:
```bash
skills/tidb-zero/scripts/tidb0_openapi_list.sh
skills/tidb-zero/scripts/tidb0_openapi_list.sh --query activation
skills/tidb-zero/scripts/tidb0_openapi_list.sh --query require
```

Extract:
```bash
skills/tidb-zero/scripts/tidb0_openapi_extract.sh --operation-id PublicShadowPoolService_RequireActivationCode
skills/tidb-zero/scripts/tidb0_openapi_extract.sh --operation-id PublicShadowPoolService_RequireDatabase
```

## Curl execution template (recommended)

Always capture status + print body:
```bash
tmp="$(mktemp)"
status="$(
  curl -sS -o "$tmp" -w '%{http_code}' \
    --connect-timeout "${TIDB0_CURL_CONNECT_TIMEOUT:-10}" \
    --max-time "${TIDB0_CURL_MAX_TIME:-60}" \
    ...
)"
echo "HTTP $status" >&2
cat "$tmp" | jq .
```

Failure body is typically `google.rpc.Status`-like: `code` (int), `message` (string), `details` (array).

## API 1) Request activation code

OperationId: `PublicShadowPoolService_RequireActivationCode`

Default base URL (can be overridden by OpenAPI extract meta):
```bash
BASE="https://${TIDBZERO_HOST}${TIDBZERO_BASE_PATH:-/v1alpha1}"
```

First attempt (POST `/activation-codes`):
```bash
tmp="$(mktemp)"
status="$(
  curl -sS -X POST \
    -H 'content-type: application/json' \
    --data-binary '{}' \
    -o "$tmp" \
    -w '%{http_code}' \
    "${BASE}/activation-codes"
)"
echo "HTTP $status" >&2
cat "$tmp" | jq .
```

Save activation code for create-db:
```bash
export TIDB0_ACTIVATION_CODE="<.code from response>"
```

If it fails with 404/405, use extract and retry:
```bash
op_json="$(skills/tidb-zero/scripts/tidb0_openapi_extract.sh --operation-id PublicShadowPoolService_RequireActivationCode)"
method="$(echo "$op_json" | jq -r '.method')"
path="$(echo "$op_json" | jq -r '.path')"
base_path="$(echo "$op_json" | jq -r '.meta.basePath')"
BASE="https://${TIDBZERO_HOST}${base_path}"
# then curl -X "$method" "$BASE$path" ...
```

## API 2) Create a temporary database (playground-like)

OperationId: `PublicShadowPoolService_RequireDatabase`

Default base URL:
```bash
BASE="https://${TIDBZERO_HOST}${TIDBZERO_BASE_PATH:-/v1alpha1}"
```

Request body (jq; required activationCode, optional namePrefix/password):
```bash
body="$(jq -n \
  --arg activationCode "${TIDB0_ACTIVATION_CODE}" \
  --arg namePrefix "${TIDB0_NAME_PREFIX:-codex-}" \
  --arg password "${TIDB0_DB_PASSWORD:-}" \
  '{activationCode:$activationCode, namePrefix:$namePrefix, password:$password}
   | with_entries(select(.value != ""))'
)"
```

First attempt (current spec uses POST `/instances`):
```bash
tmp="$(mktemp)"
status="$(
  curl -sS -X POST \
    -H 'content-type: application/json' \
    --data-binary "$body" \
    -o "$tmp" \
    -w '%{http_code}' \
    "${BASE}/instances"
)"
echo "HTTP $status" >&2
cat "$tmp" | tee /tmp/tidb0-create-db.json | jq .
```

If it fails with 404/405, use extract and retry:
```bash
op_json="$(skills/tidb-zero/scripts/tidb0_openapi_extract.sh --operation-id PublicShadowPoolService_RequireDatabase)"
method="$(echo "$op_json" | jq -r '.method')"
path="$(echo "$op_json" | jq -r '.path')"
base_path="$(echo "$op_json" | jq -r '.meta.basePath')"
BASE="https://${TIDBZERO_HOST}${base_path}"
# then curl -X "$method" "$BASE$path" ...
```

On success, ALWAYS explicitly print these fields to the user (in addition to the full JSON response):
- `id`, `name`, `expiresAt`, `remainingDatabaseQuota`
- `connection.host`, `connection.port`, `connection.username`, `connection.password`

Quick extraction from saved response:
```bash
jq -r '
  "id: \(.database.id)",
  "name: \(.database.name)",
  "expiresAt: \(.database.expiresAt)",
  "remainingDatabaseQuota: \(.remainingDatabaseQuota)",
  "connection.host: \(.database.connection.host)",
  "connection.port: \(.database.connection.port)",
  "connection.username: \(.database.connection.username)",
  "connection.password: \(.database.connection.password)"' \
  /tmp/tidb0-create-db.json
```

## Follow-up: Run SQL on the created database

Use returned `connection`.

mysqlsh (preferred; password via stdin). In sandboxed environments, set `MYSQLSH_USER_CONFIG_HOME` to a writable directory:
```bash
mkdir -p /tmp/mysqlsh-codex
printf '%s' '<connection.password>' | MYSQLSH_USER_CONFIG_HOME=/tmp/mysqlsh-codex mysqlsh --sql \
  --host '<connection.host>' --port '<connection.port>' --user '<connection.username>' \
  --passwords-from-stdin \
  --execute "SELECT 1;"
```

mysql fallback:
```bash
MYSQL_PWD='<connection.password>' mysql \
  -h '<connection.host>' -P '<connection.port>' -u '<connection.username>' \
  -e "SELECT 1;"
```
