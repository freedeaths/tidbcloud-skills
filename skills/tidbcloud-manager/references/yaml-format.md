# YAML Output Format

The generated YAML follows the format compatible with existing test executors (same as log-to-reusable output).

## Schema

```yaml
scenario:
  id: string                    # UUID, auto-generated
  context:
    auth:
      type: string              # "digest" | "bearer"
    base_path: string           # e.g., "/v1beta1"
    host: string                # e.g., "serverless.tidbapi.com"
  description: string           # From user's target description
  name: string                  # Scenario name

steps:
  - operation_id: string        # e.g., "ClusterService_CreateCluster"
    request_type: string        # "http" | "aws_cli" | "azure_cli" | "gcp_cli" | "mysqlsh"
    request: string             # JSON string of request details
    expect: string | null       # JSON string of expected result
    save: string | null         # JSON string of variables to save
    description: string         # Step description
    max_retries: integer        # For polling operations
    delay_after: integer        # Seconds to wait after this step
    name: string                # Step name
    continue_on_unexpected: boolean
    stage: string               # Optional grouping
    meta: string                # JSON string of metadata
    generation_id: string       # Session ID that generated this
    order: integer              # Step order (0-indexed)
```

## HTTP Request Format

```yaml
request: |-
  {
    "method": "POST",
    "path": "https://serverless.tidbapi.com/v1beta1/clusters",
    "headers": {},
    "query_params": {},
    "body": {
      "displayName": "llm-e2e-cluster-1234",
      "regionId": "{region_id}",
      "labels": {
        "tidb.cloud/project": "{project_id}"
      },
      "tidbNodeSetting": {
        "nodeSpecKey": "2C8G",
        "tidbNodeGroups": [
          {
            "nodeCount": 1
          }
        ]
      }
    },
    "auth": {
      "auth_type": "digest",
      "content": {
        "private_key": "placeholder",
        "public_key": "placeholder"
      }
    },
    "body_params": {
      "region_id": "region_id",
      "project_id": "project_id"
    },
    "auth_params": {
      "private_key": "private_key",
      "public_key": "public_key"
    }
  }
```

### HTTP Request Fields

| Field | Type | Description |
|-------|------|-------------|
| method | string | HTTP method: GET, POST, PUT, PATCH, DELETE |
| path | string | Full URL with placeholders |
| headers | object | HTTP headers |
| query_params | object | URL query parameters |
| body | object | Request body (for POST/PUT/PATCH) |
| auth | object | Authentication config |
| path_params | object | Path parameter variable mappings |
| body_params | object | Body parameter variable mappings |
| auth_params | object | Auth parameter variable mappings |

## CLI Request Format

```yaml
request: |-
  {
    "name": "az",
    "service": "network",
    "args": [
      "network",
      "private-endpoint",
      "create",
      "--name", "{endpoint_name}",
      "--resource-group", "openapi-test",
      "--vnet-name", "openapi-test-vnet",
      "--subnet", "openapi-test-subnet",
      "--private-connection-resource-id", "{service_id}",
      "--connection-name", "tidb-connection"
    ],
    "args_params": {
      "endpoint_name": "endpoint_name",
      "service_id": "privatelinkservice_1"
    }
  }
```

### CLI Request Fields

| Field | Type | Description |
|-------|------|-------------|
| name | string | CLI tool name: "az", "aws", "gcloud", "mysqlsh" |
| service | string | Service category (e.g., "network", "s3") |
| args | array | Command arguments as array |
| args_params | object | Variable mappings for args |

## Expect Format

### HTTP Expect

```yaml
expect: |-
  {
    "status_code": 200,
    "cel": "body.state == 'ACTIVE'"
  }
```

### CLI Expect

```yaml
expect: |-
  {
    "exit_code": 0,
    "cel": "output.provisioningState == 'Succeeded'"
  }
```

### CEL Expression Examples

| Expression | Description |
|------------|-------------|
| `body.state == 'ACTIVE'` | Check resource state |
| `body.clusterId != ''` | Check field exists and not empty |
| `size(body.items) > 0` | Check array has elements |
| `body.tidbNodeSetting.tidbNodeGroups[0].nodeCount == 2` | Check nested field |

## Save Format

```yaml
save: |-
  {
    "placeholder": [
      {
        "key": "cluster_1",
        "eval": "body.clusterId"
      },
      {
        "key": "nodegroup_1",
        "eval": "body.tidbNodeSetting.tidbNodeGroups[0].tidbNodeGroupId"
      }
    ]
  }
```

### Save Rules

1. **Key naming**: `<resource_type>_<index>` (e.g., `cluster_1`, `nodegroup_2`)
2. **Eval path for HTTP**: `body.<path>` 
3. **Eval path for CLI**: `output.<path>` or `output[0].<path>`
4. Only save **resource IDs** that will be referenced in later steps
5. Do NOT save `displayName`, `name`, or non-ID fields

## Complete Example

```yaml
scenario:
  id: "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
  context:
    auth:
      type: digest
    base_path: /v1beta1
    host: serverless.tidbapi.com
  description: "Scale in and out TiDB nodes"
  name: "scale_in_and_out_tidb"

steps:
  - operation_id: ClusterService_CreateCluster
    request_type: http
    request: |-
      {
        "method": "POST",
        "path": "https://dedicated.tidbapi.com/v1beta1/clusters",
        "body": {
          "displayName": "llm-e2e-cluster-1234",
          "regionId": "{region_id}",
          "tidbNodeSetting": {
            "nodeSpecKey": "2C8G",
            "tidbNodeGroups": [{"nodeCount": 1}]
          },
          "tikvNodeSetting": {
            "nodeCount": 3,
            "nodeSpecKey": "2C8G",
            "storageSizeGi": 200
          }
        },
        "auth": {"auth_type": "digest", "content": {"private_key": "placeholder", "public_key": "placeholder"}},
        "body_params": {"region_id": "region_id"}
      }
    expect: |-
      {"status_code": 200, "cel": ""}
    save: |-
      {"placeholder": [{"key": "cluster_1", "eval": "body.clusterId"}]}
    description: "Create a new cluster"
    max_retries: 0
    delay_after: 0
    name: "create_cluster"
    continue_on_unexpected: false
    stage: "setup"
    meta: "{}"
    generation_id: "session_123"
    order: 0

  - operation_id: ClusterService_GetCluster
    request_type: http
    request: |-
      {
        "method": "GET",
        "path": "https://dedicated.tidbapi.com/v1beta1/clusters/{clusterId}",
        "path_params": {"clusterId": "cluster_1"},
        "auth": {"auth_type": "digest", "content": {"private_key": "placeholder", "public_key": "placeholder"}}
      }
    expect: |-
      {"status_code": 200, "cel": "body.state == 'ACTIVE'"}
    save: |-
      {"placeholder": [{"key": "nodegroup_1", "eval": "body.tidbNodeSetting.tidbNodeGroups[0].tidbNodeGroupId"}]}
    description: "Poll until cluster is ACTIVE"
    max_retries: 120
    delay_after: 30
    name: "poll_cluster_active"
    continue_on_unexpected: false
    stage: "setup"
    meta: "{}"
    generation_id: "session_123"
    order: 1
```
