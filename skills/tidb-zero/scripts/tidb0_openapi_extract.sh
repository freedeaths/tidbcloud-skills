#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  tidb0_openapi_extract.sh --operation-id OPERATION_ID

Output:
  Prints a focused JSON blob including:
  - meta: info/host/basePath/schemes/consumes/produces
  - method/path
  - request/response schema refs
  - the operation (parameters/responses)
  - related definitions (top-level + nested refs)
USAGE
}

operation_id=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --operation-id) operation_id="${2-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "${operation_id}" ]]; then
  echo "error: --operation-id is required" >&2
  usage >&2
  exit 2
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq is required" >&2
  exit 127
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}" )" && pwd)"
spec="${script_dir}/../configs/openapi.json"
if [[ ! -f "${spec}" ]]; then
  echo "error: openapi spec not found: ${spec}" >&2
  exit 2
fi

jq -n --arg opid "${operation_id}" --slurpfile spec "${spec}" '
  def ref_name:
    if (type == "string") then sub("^#/definitions/";"") else empty end;

  def collect_refs:
    [.. | objects | select(has("$ref")) | .["$ref"]] | unique;

  ($spec[0]) as $s
  | (
      $s.paths
      | to_entries[]
      | .key as $path
      | .value
      | to_entries[]
      | select(.key | test("^(get|post|put|delete|patch|head|options)$"))
      | select(.value.operationId == $opid)
      | {method:(.key|ascii_upcase), path:$path, operation:.value}
    ) as $hit
  | ($hit.operation.parameters // []) as $params
  | ($params | map(select(.in=="body")) | .[0].schema["$ref"]?) as $req_ref
  | ($hit.operation.responses["200"].schema["$ref"]? // null) as $resp200_ref
  | (
      ([ $req_ref, $resp200_ref ] | map(select(. != null)))
      | map(ref_name)
    ) as $top_defs
  | (
      $top_defs
      | map({key: ., value: ($s.definitions[.] // null)})
      | from_entries
    ) as $top_def_schemas
  | (
      ($top_def_schemas | collect_refs)
      | map(ref_name)
      | map(select(. != ""))
      | unique
    ) as $nested_defs
  | (
      ($nested_defs - $top_defs)
      | map({key: ., value: ($s.definitions[.] // null)})
      | from_entries
    ) as $nested_def_schemas
  | {
      operationId: $opid,
      meta: {
        info: ($s.info // {}),
        host: ($s.host // null),
        basePath: ($s.basePath // ""),
        schemes: ($s.schemes // []),
        consumes: ($s.consumes // []),
        produces: ($s.produces // [])
      },
      method: $hit.method,
      path: $hit.path,
      requestBodyRef: $req_ref,
      response200Ref: $resp200_ref,
      operation: {
        summary: ($hit.operation.summary // null),
        description: ($hit.operation.description // null),
        parameters: ($hit.operation.parameters // []),
        responses: ($hit.operation.responses // {})
      },
      definitions: ($top_def_schemas + $nested_def_schemas)
    }
' | jq .
