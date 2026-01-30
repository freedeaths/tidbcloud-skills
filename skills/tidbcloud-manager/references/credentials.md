# Credentials Configuration

This document explains how to configure credentials so that the local runner can execute API requests **without exposing your sensitive tokens in prompts**.

## Security Model

The runner (`tidbcloud-manager secure-exec`) handles all authentication internally:

```
┌─────────────┐     ┌────────────────────┐     ┌─────────────┐
│   Agent     │ --> │ tidbcloud-manager  │ --> │   API       │
│ (sees JSON) │     │ (loads creds       │     │ (receives   │
│             │     │  from env/file)    │     │  auth)      │
└─────────────┘     └────────────────────┘     └─────────────┘
        │                    │
        │                    └── Credentials loaded here
        │                        (never returned to Claude)
        │
        └── Claude only sees:
            - Request: {"method":"GET","path":"/clusters"}
            - Response: {"status_code":200,"body":{...}}
```

## Configuration Methods

### Method 0: `.env` file (Recommended for local use)

Create `./.env` in the **skill directory** (same folder as `./configs/`).

Example:
```bash
TIDB_PUBLIC_KEY="your-public-key-here"
TIDB_PRIVATE_KEY="your-private-key-here"
TIDBCLOUD_PROJECT_ID="your-project-id-here"
TIDBCLOUD_HOST="serverless.tidbapi.com"
```

`./.env` is auto-loaded if present.

### Method 1: Environment Variables (Recommended)

Set these environment variables before starting the agent (or before running commands):

```bash
# TiDB Cloud credentials
export TIDB_PUBLIC_KEY="your-public-key-here"
export TIDB_PRIVATE_KEY="your-private-key-here"

# Then start your agent / terminal session
```

Or add to your shell profile (`~/.zshrc` or `~/.bashrc`):

```bash
export TIDB_PUBLIC_KEY="your-public-key-here"
export TIDB_PRIVATE_KEY="your-private-key-here"
```

### Method 2: Credential File

Create `~/.tidb-credentials.json` (or set `connection.auth.credential_file` in `sut.yaml`):

```json
{
    "public_key": "your-public-key-here",
    "private_key": "your-private-key-here"
}
```

**Important**: Set proper permissions:
```bash
chmod 600 ~/.tidb-credentials.json
```

### Method 3: Custom Credential File Path

For different environments, specify in `configs/<sut>/sut.yaml`:

```yaml
connection:
  auth:
    type: digest
    credential_file: ~/.config/tidb/credentials.json
    env_vars:
      public_key: TIDB_PUBLIC_KEY
      private_key: TIDB_PRIVATE_KEY
```

## Usage Examples

### Example usage

```bash
tidbcloud-manager secure-exec http '{"method":"GET","path":"/clusters"}' --sut tidbx

tidbcloud-manager secure-exec http '{"method":"POST","path":"/clusters","body":{"displayName":"test","labels":{"tidb.cloud/project":"${TIDBCLOUD_PROJECT_ID}"},"region":{"name":"${TIDBCLOUD_REGION_NAME:-regions/aws-us-east-1}"}}}' --sut tidbx

tidbcloud-manager secure-exec poll '{"method":"GET","path":"/clusters/123","expect":"body.state == ACTIVE","max_retries":60,"delay":30}' --sut tidbx
```

### What the agent sees:

**Input** (no credentials):
```json
{"method": "GET", "path": "/clusters"}
```

**Output** (no credentials):
```json
{
  "success": true,
  "status_code": 200,
  "body": {
    "clusters": [...]
  },
  "duration_ms": 245
}
```

## CLI Tools (AWS, Azure, GCP)

For CLI commands, credentials are read from standard locations:

| Tool | Credential Source |
|------|-------------------|
| AWS CLI | `~/.aws/credentials` or `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` |
| Azure CLI | `az login` session |
| GCP CLI | `gcloud auth login` session |
| mysqlsh | Connection string in request |

Example:
```bash
tidbcloud-manager secure-exec cli '{"tool":"aws","args":["ec2","describe-vpcs","--region","us-east-1"]}'
```

## Verification

Test that credentials are working:

```bash
# Should return cluster list without showing credentials
tidbcloud-manager secure-exec http '{"method":"GET","path":"/clusters"}' --sut tidbx
```

If you see `{"success":false,"error":"...401..."}`, check your credentials.

## Troubleshooting

### "Credentials not found"

1. Check environment variables: `echo $TIDB_PUBLIC_KEY`
2. Check credential file exists: `ls -la ~/.tidb-credentials.json`
3. Verify file permissions: should be `600`

### "401 Unauthorized"

1. Verify your keys are correct
2. Check if keys have expired
3. Ensure you're using the right environment (production or something else)

### Claude still sees sensitive data?

This should **never happen** if using `tidbcloud-manager secure-exec`. The runner:
- Loads credentials internally
- Never includes them in output
- Only returns `status_code`, `body`, `error`, `duration_ms`

If you're seeing credentials in Claude's context:
- You might be using `curl` directly instead of `tidbcloud-manager secure-exec`
- Check that SKILL.md instructs to use `tidbcloud-manager secure-exec`
