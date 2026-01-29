# Knowledge Base Schema

The knowledge base enables cross-session learning. It stores successful patterns, failure patterns, and operation statistics.

## Storage Location

```
~/.tidbcloud-manager/
├── knowledge/
│   ├── tidbcloud_dedicated/
│   │   ├── patterns.yaml        # Successful patterns
│   │   ├── pitfalls.yaml        # Known failure patterns
│   │   └── stats.yaml           # Operation statistics
│   └── tidbcloud_serverless/
│       └── ...
└── sessions/
    └── {session_id}.yaml        # Session history
```

## Successful Patterns Schema

```yaml
# patterns.yaml
patterns:
  - id: "pattern_001"
    name: "create_cluster_and_poll"
    description: "Create a cluster and poll until ACTIVE"
    
    # When to match this pattern
    trigger:
      intent_keywords: ["create", "cluster"]
      precondition:
        resource_not_exists: "cluster"
    
    # The step sequence
    steps:
      - operation_id: "ClusterService_CreateCluster"
        request_template:
          method: "POST"
          path: "/clusters"
          body_keys: ["displayName", "regionId", "tidbNodeSetting", "tikvNodeSetting"]
        save:
          - key: "cluster_1"
            eval: "body.clusterId"
      
      - operation_id: "ClusterService_GetCluster"
        request_template:
          method: "GET"
          path: "/clusters/{clusterId}"
          path_params: ["clusterId"]
        expect:
          status_code: 200
          cel: "body.state == 'ACTIVE'"
        save:
          - key: "nodegroup_1"
            eval: "body.tidbNodeSetting.tidbNodeGroups[0].tidbNodeGroupId"
        max_retries: 120
        delay_after: 30
    
    # Statistics
    success_count: 15
    failure_count: 2
    last_used: "2025-01-12T10:00:00Z"
    avg_duration_seconds: 180

  - id: "pattern_002"
    name: "scale_out_tidb"
    description: "Scale out TiDB nodes"
    
    trigger:
      intent_keywords: ["scale", "out", "tidb"]
      precondition:
        resource_state:
          cluster: "ACTIVE"
    
    steps:
      - operation_id: "ClusterService_UpdateCluster"
        request_template:
          method: "PATCH"
          path: "/clusters/{clusterId}"
          body_keys: ["tidbNodeSetting"]
        note: "Requires nodegroup_1 to be saved from previous GetCluster"
      
      - operation_id: "ClusterService_GetCluster"
        expect:
          cel: "body.state == 'ACTIVE'"
        max_retries: 120
        delay_after: 30
    
    success_count: 8
    failure_count: 1
    last_used: "2025-01-11T15:30:00Z"
```

## Failure Patterns (Pitfalls) Schema

```yaml
# pitfalls.yaml
pitfalls:
  - id: "pitfall_001"
    name: "update_without_nodegroup_id"
    description: "Attempting to update cluster without tidbNodeGroupId"
    
    # When this pitfall applies
    trigger:
      operation_id: "ClusterService_UpdateCluster"
      missing_variable: "nodegroup_1"
    
    # The error that occurs
    error_pattern:
      status_code: 400
      message_contains: "tidbNodeGroupId is required"
    
    # How to fix it
    resolution:
      description: "Save nodegroup_1 from GetCluster response before UpdateCluster"
      fix_steps:
        - "Add save for nodegroup_1 in the polling GetCluster step"
        - "Use body.tidbNodeSetting.tidbNodeGroups[0].tidbNodeGroupId"
    
    # Statistics
    occurrence_count: 5
    last_occurred: "2025-01-10T14:20:00Z"

  - id: "pitfall_002"
    name: "delete_while_modifying"
    description: "Attempting to delete cluster in MODIFYING state"
    
    trigger:
      operation_id: "ClusterService_DeleteCluster"
      resource_state:
        cluster: "MODIFYING"
    
    error_pattern:
      status_code: 409
      message_contains: "cluster is not in a deletable state"
    
    resolution:
      description: "Wait for cluster to return to ACTIVE state before deleting"
      fix_steps:
        - "Poll GetCluster until state == 'ACTIVE'"
        - "Then execute DeleteCluster"
    
    occurrence_count: 3
    last_occurred: "2025-01-09T11:45:00Z"

  - id: "pitfall_003"
    name: "private_endpoint_without_service_name"
    description: "Creating private endpoint without getting privateLinkService first"
    
    trigger:
      operation_id: "network_private-endpoint_create"
      missing_variable: "privatelinkservice_1"
    
    error_pattern:
      message_contains: "private-connection-resource-id is required"
    
    resolution:
      description: "Get privateLinkService name before creating private endpoint"
      fix_steps:
        - "Call GetPrivateLinkService first"
        - "Save service name as privatelinkservice_1"
        - "Use it in private endpoint creation"
    
    occurrence_count: 2
```

## Operation Statistics Schema

```yaml
# stats.yaml
operations:
  ClusterService_CreateCluster:
    total_attempts: 25
    successes: 23
    failures: 2
    success_rate: 0.92
    avg_duration_ms: 1500
    common_errors:
      - error: "displayName already exists"
        count: 1
      - error: "quota exceeded"
        count: 1
    last_used: "2025-01-12T10:00:00Z"

  ClusterService_GetCluster:
    total_attempts: 500
    successes: 498
    failures: 2
    success_rate: 0.996
    avg_duration_ms: 200
    # Often used for polling, so high attempt count
    common_errors:
      - error: "cluster not found"
        count: 2
    last_used: "2025-01-12T10:05:00Z"

  ClusterService_UpdateCluster:
    total_attempts: 15
    successes: 12
    failures: 3
    success_rate: 0.80
    avg_duration_ms: 800
    common_errors:
      - error: "tidbNodeGroupId is required"
        count: 2
      - error: "invalid node count"
        count: 1
    last_used: "2025-01-12T09:30:00Z"

# State transition statistics
state_transitions:
  cluster:
    CREATING_to_ACTIVE:
      avg_poll_attempts: 45
      avg_duration_seconds: 1350
    MODIFYING_to_ACTIVE:
      avg_poll_attempts: 20
      avg_duration_seconds: 600
    PAUSING_to_PAUSED:
      avg_poll_attempts: 10
      avg_duration_seconds: 300
```

## Knowledge Update Rules

### When to Record Success

```python
def record_success(operation_id, step, result, session):
    # Update operation stats
    stats[operation_id].successes += 1
    stats[operation_id].total_attempts += 1
    
    # Check if this completes a pattern
    if is_pattern_complete(session.confirmed_steps):
        pattern = extract_pattern(session.confirmed_steps)
        if pattern not in patterns:
            patterns.append(pattern)
        else:
            patterns[pattern.id].success_count += 1
```

### When to Record Failure

```python
def record_failure(operation_id, step, error, session):
    # Update operation stats
    stats[operation_id].failures += 1
    stats[operation_id].total_attempts += 1
    
    # Extract pitfall if new error pattern
    pitfall = extract_pitfall(operation_id, step, error, session.current_state)
    if pitfall:
        existing = find_similar_pitfall(pitfall)
        if existing:
            existing.occurrence_count += 1
        else:
            pitfalls.append(pitfall)
```

### Pattern Extraction Logic

```python
def extract_pattern(confirmed_steps):
    # Group steps by logical unit
    # e.g., [Create, Poll] → "create_and_poll" pattern
    
    # Identify common step sequences
    # Abstract specific values into templates
    # Calculate confidence from success rate
    
    return Pattern(
        id=generate_id(),
        name=infer_name(confirmed_steps),
        steps=abstract_steps(confirmed_steps),
        trigger=infer_trigger(confirmed_steps)
    )
```

## Querying Knowledge

### Find Matching Pattern

```python
def find_matching_pattern(intent, current_state, patterns):
    matches = []
    for pattern in patterns:
        # Check intent keywords
        if not matches_intent(pattern.trigger.intent_keywords, intent):
            continue
        
        # Check preconditions
        if not satisfies_precondition(pattern.trigger.precondition, current_state):
            continue
        
        # Calculate confidence from success rate
        confidence = pattern.success_count / (pattern.success_count + pattern.failure_count)
        
        matches.append((pattern, confidence))
    
    # Return best match
    return max(matches, key=lambda x: x[1]) if matches else None
```

### Check for Pitfalls

```python
def check_pitfalls(operation_id, current_state, variables, pitfalls):
    warnings = []
    for pitfall in pitfalls:
        # Check if operation matches
        if pitfall.trigger.operation_id != operation_id:
            continue
        
        # Check if conditions match
        if pitfall.trigger.missing_variable:
            if pitfall.trigger.missing_variable not in variables:
                warnings.append(pitfall)
        
        if pitfall.trigger.resource_state:
            for resource, state in pitfall.trigger.resource_state.items():
                if current_state.get(resource, {}).get("state") == state:
                    warnings.append(pitfall)
    
    return warnings
```
