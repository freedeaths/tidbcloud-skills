# E2E Explorer Workflow

## Execution Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     Main Execution Loop                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. Initialize Session                                          │
│     ├── Load SUT config (sut.yaml)                              │
│     ├── Load OpenAPI spec                                       │
│     ├── Load knowledge base                                     │
│     └── Parse target description                                │
│                                                                 │
│  2. While target not reached:                                   │
│     │                                                           │
│     ├── 2a. Suggest next step                                   │
│     │   ├── Find relevant operations from OpenAPI               │
│     │   ├── Filter by state machine (knowledge)                 │
│     │   ├── Match against known patterns                        │
│     │   ├── Check for known pitfalls                            │
│     │   └── Calculate confidence score                          │
│     │                                                           │
│     ├── 2b. Check intervention needed (AUTO mode)               │
│     │   ├── Low confidence? → Ask user                          │
│     │   ├── Known pitfall? → Warn user                          │
│     │   ├── Checkpoint operation? → Confirm                     │
│     │   └── All clear? → Continue                               │
│     │                                                           │
│     ├── 2c. Execute step                                        │
│     │   ├── HTTP: requests library                              │
│     │   └── CLI: subprocess                                     │
│     │                                                           │
│     ├── 2d. Analyze result                                      │
│     │   ├── Success? → Save step, update state                  │
│     │   ├── Expected failure (polling)? → Retry                 │
│     │   └── Unexpected failure? → Intervention                  │
│     │                                                           │
│     └── 2e. Update knowledge                                    │
│         ├── Record success/failure                              │
│         └── Extract patterns if session complete                │
│                                                                 │
│  3. Generate output YAML                                        │
│     └── Compile confirmed steps into reusable format            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Step Suggestion Algorithm

```python
def suggest_next_step(current_state, target, history):
    # 1. Extract intent from target description
    intent = parse_intent(target)  # e.g., "scale out" → UPDATE operation
    
    # 2. Find candidate operations from OpenAPI spec
    candidates = find_operations_matching_intent(intent, openapi_spec)
    
    # 3. Filter by state machine constraints
    for resource_type, resource_state in current_state.items():
        allowed_ops = knowledge.state_machines[resource_type].allowed_operations[resource_state]
        candidates = [c for c in candidates if c in allowed_ops]
    
    # 4. Check for known successful patterns
    pattern = knowledge.find_matching_pattern(intent, current_state)
    if pattern:
        return Suggestion(
            operation=pattern.next_step,
            confidence=0.9,
            source="known_pattern",
            pattern_name=pattern.name
        )
    
    # 5. Check for known pitfalls
    pitfalls = knowledge.check_pitfalls(candidates, current_state)
    
    # 6. Rank candidates by historical success rate
    ranked = rank_by_success_rate(candidates, knowledge.operation_stats)
    
    # 7. Calculate confidence
    confidence = calculate_confidence(ranked, pitfalls, pattern)
    
    return Suggestion(
        operation=ranked[0],
        alternatives=ranked[1:3],
        confidence=confidence,
        pitfalls=pitfalls
    )
```

## Intervention Decision Matrix

| Condition | Confidence | Action |
|-----------|------------|--------|
| Known pattern match | 90%+ | Auto-execute |
| Single clear candidate | 80%+ | Auto-execute |
| Multiple candidates, one dominant | 70-80% | Auto-execute with note |
| Multiple candidates, similar scores | 50-70% | Ask user to choose |
| Known pitfall detected | Any | Warn and ask |
| Checkpoint operation | Any | Confirm before execute |
| No matching operation found | <50% | Ask for clarification |

## State Management

### Current State Structure

```yaml
current_state:
  resources:
    clusters:
      "cluster_1":
        id: "12345678"
        state: "ACTIVE"
        attributes:
          displayName: "test-cluster"
          regionId: "us-east-1"
    tidb_node_groups:
      "nodegroup_1":
        id: "87654321"
        parent_id: "12345678"
        state: "ACTIVE"
        attributes:
          nodeCount: 2
  
  variables:
    cluster_1: "12345678"
    nodegroup_1: "87654321"
    region_id: "us-east-1"
```

### State Update Rules

1. **After successful CREATE**: Add resource to state, save ID as variable
2. **After successful GET**: Update resource attributes, extract nested IDs
3. **After successful UPDATE**: Update resource state to MODIFYING, then poll
4. **After successful DELETE**: Remove resource from state
5. **After CLI command**: Parse output, extract created resource IDs

## Polling Logic

```python
def poll_until_ready(operation, expect_condition, max_retries=120, delay=30):
    for attempt in range(max_retries):
        result = executor.execute(operation)
        
        if evaluate_condition(result, expect_condition):
            return PollResult(success=True, result=result, attempts=attempt+1)
        
        if is_terminal_failure(result):
            return PollResult(success=False, error="Terminal state reached")
        
        time.sleep(delay)
    
    return PollResult(success=False, error="Max retries exceeded")
```

## Error Recovery

### Recoverable Errors

| Error Type | Recovery Action |
|------------|-----------------|
| 429 Rate Limited | Wait and retry |
| 503 Service Unavailable | Wait and retry |
| Network timeout | Retry with backoff |
| Resource not ready | Continue polling |

### Non-Recoverable Errors (Request Intervention)

| Error Type | Intervention |
|------------|--------------|
| 400 Bad Request | Show error, suggest fix |
| 401/403 Auth Error | Ask for credentials |
| 404 Not Found | Check if resource exists |
| 409 Conflict | Show state conflict |
| 500 Server Error | Log and ask user |

## Session Persistence

Sessions are persisted to enable:
- Resume after interruption
- Learning from completed sessions
- Sharing sessions between users

```yaml
# ~/.tidbcloud-manager/sessions/{session_id}.yaml
session:
  id: "ses_abc123"
  sut: "dedicated"
  target: "create cluster, scale out, delete"
  mode: "auto"
  created_at: "2025-01-12T10:00:00Z"
  updated_at: "2025-01-12T10:15:00Z"
  status: "in_progress"  # in_progress | completed | aborted | failed

current_state:
  # ... as shown above

confirmed_steps:
  - order: 0
    operation_id: "ClusterService_CreateCluster"
    request: { ... }
    response: { ... }
    saved_variables: ["cluster_1"]
    
pending_step: null

rejected_steps:
  - operation_id: "ClusterService_UpdateCluster"
    error: "tidbNodeGroupId is required"
    user_action: "added save for nodegroup_1"
```
