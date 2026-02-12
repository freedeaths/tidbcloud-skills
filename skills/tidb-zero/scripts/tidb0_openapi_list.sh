#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  tidb0_openapi_list.sh [--query TEXT] [--limit N] [--format tsv|json]

Notes:
  - Lists operations from ../configs/openapi.json.
  - Output fields: operationId, method, path, summary.
USAGE
}

query=""
limit=50
format="tsv"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --query) query="${2-}"; shift 2 ;;
    --limit) limit="${2-}"; shift 2 ;;
    --format) format="${2-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq is required" >&2
  exit 127
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
spec="${script_dir}/../configs/openapi.json"
if [[ ! -f "${spec}" ]]; then
  echo "error: openapi spec not found: ${spec}" >&2
  exit 2
fi

jq_filter='[
  .paths
  | to_entries[]
  | .key as $path
  | .value
  | to_entries[]
  | select(.key | test("^(get|post|put|delete|patch|head|options)$"))
  | .key as $method
  | .value as $op
  | {
      operationId: ($op.operationId // ""),
      method: ($method | ascii_upcase),
      path: $path,
      summary: ($op.summary // $op.description // "")
    }
  | select(.operationId != "")
] | sort_by(.operationId)'

if [[ "${format}" == "json" ]]; then
  out="$(jq -c "${jq_filter}" "${spec}")"
  if [[ -n "${query}" ]]; then
    out="$(echo "${out}" | jq -c --arg q "${query}" '[.[] | select((.operationId + " " + .method + " " + .path + " " + .summary) | test($q; "i"))]')"
  fi
  if [[ "${limit}" != "0" ]]; then
    out="$(echo "${out}" | jq -c --argjson n "${limit}" '.[0:$n]')"
  fi
  echo "${out}" | jq .
  exit 0
fi

lines="$(jq -r "${jq_filter} | .[] | [.operationId, .method, .path, .summary] | @tsv" "${spec}")"

if [[ -n "${query}" ]]; then
  if command -v rg >/dev/null 2>&1; then
    lines="$(echo "${lines}" | rg -i -- "${query}" || true)"
  else
    lines="$(echo "${lines}" | grep -i -- "${query}" || true)"
  fi
fi

if [[ "${limit}" != "0" ]]; then
  lines="$(echo "${lines}" | head -n "${limit}")"
fi

echo -e "operationId\tmethod\tpath\tsummary"
echo "${lines}"
