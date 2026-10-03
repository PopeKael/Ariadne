# Live semantic conversation evaluation

Synthetic data; actual existing interpreter and answer adapters, Qwen 3.5 9B. No private Vault retrieval.

## A Correction

User: I've identified myself with their support team and we communicate by email. Whether I'm really inside the fold is from their perspective. I'm just here to help.

Interpreted turn/state:
```json
{
  "turn_id": "5fca2193b2fe41b0b67fce418119e7d4",
  "types": [
    "correction",
    "clarification"
  ],
  "subject": "relationship_status_with_support_team",
  "objective": "determine_if_assistant_can_proceed_without_vault_context_or_if_user_fact_is_needed_to_override_prior_claim",
  "response_objective": "acknowledge_update",
  "changes": [
    {
      "id": "user:5fca2193b2fe41b0b67fce418119e7d4:0",
      "kind": "user_fact",
      "supersedes": [
        "assistant:8346a455d0ed45388cdf40f9e065f65c:1f56e971f782"
      ]
    },
    {
      "id": "user:5fca2193b2fe41b0b67fce418119e7d4:1",
      "kind": "uncertainty",
      "supersedes": []
    }
  ],
  "correction": true,
  "supersession": true,
  "rejected_changes": [],
  "fallback": false,
  "controller_fallbacks": []
}
```
Accepted conversation state:
```json
{
  "version": 1,
  "subject": "relationship_status_with_support_team",
  "objective": "determine_if_assistant_can_proceed_without_vault_context_or_if_user_fact_is_needed_to_override_prior_claim",
  "items": [
    {
      "id": "user:5fca2193b2fe41b0b67fce418119e7d4:0",
      "kind": "user_fact",
      "text": "I've identified myself with their support team and we communicate by email.",
      "subject": "relationship_status_with_support_team",
      "source_turn_id": "5fca2193b2fe41b0b67fce418119e7d4",
      "provenance": "user_supplied"
    },
    {
      "id": "user:5fca2193b2fe41b0b67fce418119e7d4:1",
      "kind": "uncertainty",
      "text": "Whether I'm really inside the fold is from their perspective.",
      "subject": "relationship_status_with_support_team",
      "source_turn_id": "5fca2193b2fe41b0b67fce418119e7d4",
      "provenance": "user_supplied"
    }
  ],
  "superseded": [
    {
      "id": "assistant:8346a455d0ed45388cdf40f9e065f65c:1f56e971f782",
      "text": "Morgan is a valued partner to VoiceWorks engineering.",
      "by_turn_id": "5fca2193b2fe41b0b67fce418119e7d4"
    }
  ],
  "correction_subjects": [
    "relationship_status_with_support_team"
  ]
}
```
Context recipe (includes evidence/history decision and response objective):
```json
{
  "version": 1,
  "turn_id": "5fca2193b2fe41b0b67fce418119e7d4",
  "turn_types": [
    "correction",
    "clarification"
  ],
  "semantic_intent": "clarify_missing",
  "semantic_turn_types": [
    "clarification"
  ],
  "semantic_response_objective": "acknowledge_update",
  "subject": "relationship_status_with_support_team",
  "objective": "determine_if_assistant_can_proceed_without_vault_context_or_if_user_fact_is_needed_to_override_prior_claim",
  "state_changes": [
    {
      "id": "user:5fca2193b2fe41b0b67fce418119e7d4:0",
      "kind": "user_fact",
      "supersedes": [
        "assistant:8346a455d0ed45388cdf40f9e065f65c:1f56e971f782"
      ]
    },
    {
      "id": "user:5fca2193b2fe41b0b67fce418119e7d4:1",
      "kind": "uncertainty",
      "supersedes": []
    }
  ],
  "correction": true,
  "supersession": true,
  "history_requested": true,
  "vault": {
    "used": false,
    "reason": "current_conversation_update_sufficient"
  },
  "web": {
    "used": false,
    "reason": "no_provider"
  },
  "external_research_requested": false,
  "attachment_use": 0,
  "tools": [],
  "capabilities_requested": [],
  "model": "qwen3.5:9b-q4_K_M",
  "reasoning_tier": "standard",
  "response_objective": "acknowledge_update",
  "context_blocks": {
    "system": {
      "characters": 2950,
      "approx_tokens": 738
    },
    "focus": {
      "characters": 0,
      "approx_tokens": 0
    },
    "instruction": {
      "characters": 160,
      "approx_tokens": 40
    },
    "attachment": {
      "characters": 0,
      "approx_tokens": 0
    },
    "vault": {
      "characters": 0,
      "approx_tokens": 0
    },
    "web": {
      "characters": 0,
      "approx_tokens": 0
    },
    "recent_conversation": {
      "characters": 32,
      "approx_tokens": 8
    },
    "older_context": {
      "characters": 0,
      "approx_tokens": 0
    },
    "adaptive_profile": {
      "characters": 2,
      "approx_tokens": 1
    },
    "world_state": {
      "characters": 0,
      "approx_tokens": 0
    },
    "turn_state": {
      "characters": 1246,
      "approx_tokens": 312
    },
    "total": {
      "characters": 4522,
      "approx_tokens": 1131
    }
  },
  "evidence_ids": [],
  "confidence": 0.95,
  "ambiguity": "low",
  "policy_overrides": [
    "current_conversation_update_sufficient"
  ],
  "capability_gaps": [],
  "fallback_paths": [],
  "generation": {
    "status": "complete",
    "source_guard_rejected": false
  },
  "history_reason": "semantic_personal_or_chronological_reference",
  "before_generation_ms": 5495.999,
  "receipt_persistence_ms": 10.979
}
```
Generated answer:

That clarifies things. You have formally introduced yourself to their support team, and your primary channel of communication is email. However, you are correct that being "inside the fold" depends on how they perceive and accept that introduction from their side.

Since I don't have access to their internal view or recent interactions with them, I cannot confirm if they consider you fully integrated yet. For now, treat your status as pending their confirmation. How would you like to proceed given this uncertainty?

## B Generic fact

User: What is a hash table?

Interpreted turn/state:
```json
{
  "turn_id": "909a538eb083428fb0fbfe80acfd48a8",
  "types": [
    "question"
  ],
  "subject": "hash table",
  "objective": "define hash table",
  "response_objective": "answer",
  "changes": [],
  "correction": false,
  "supersession": false,
  "rejected_changes": [],
  "fallback": false,
  "controller_fallbacks": []
}
```
Accepted conversation state:
```json
{
  "version": 1,
  "subject": "hash table",
  "objective": "define hash table",
  "items": [],
  "superseded": [],
  "correction_subjects": []
}
```
Context recipe (includes evidence/history decision and response objective):
```json
{
  "version": 1,
  "turn_id": "909a538eb083428fb0fbfe80acfd48a8",
  "turn_types": [
    "question"
  ],
  "semantic_intent": "definition",
  "semantic_turn_types": [
    "question"
  ],
  "semantic_response_objective": "answer",
  "subject": "hash table",
  "objective": "define hash table",
  "state_changes": [],
  "correction": false,
  "supersession": false,
  "history_requested": false,
  "vault": {
    "used": false,
    "reason": "not_required"
  },
  "web": {
    "used": false,
    "reason": "no_provider"
  },
  "external_research_requested": false,
  "attachment_use": 0,
  "tools": [],
  "capabilities_requested": [],
  "model": "qwen3.5:9b-q4_K_M",
  "reasoning_tier": "standard",
  "response_objective": "answer",
  "context_blocks": {
    "system": {
      "characters": 2922,
      "approx_tokens": 731
    },
    "focus": {
      "characters": 0,
      "approx_tokens": 0
    },
    "instruction": {
      "characters": 21,
      "approx_tokens": 6
    },
    "attachment": {
      "characters": 0,
      "approx_tokens": 0
    },
    "vault": {
      "characters": 0,
      "approx_tokens": 0
    },
    "web": {
      "characters": 0,
      "approx_tokens": 0
    },
    "recent_conversation": {
      "characters": 0,
      "approx_tokens": 0
    },
    "older_context": {
      "characters": 0,
      "approx_tokens": 0
    },
    "adaptive_profile": {
      "characters": 2,
      "approx_tokens": 1
    },
    "world_state": {
      "characters": 0,
      "approx_tokens": 0
    },
    "turn_state": {
      "characters": 320,
      "approx_tokens": 80
    },
    "total": {
      "characters": 3396,
      "approx_tokens": 849
    }
  },
  "evidence_ids": [],
  "confidence": 0.95,
  "ambiguity": "low",
  "policy_overrides": [],
  "capability_gaps": [],
  "fallback_paths": [],
  "generation": {
    "status": "complete",
    "source_guard_rejected": false
  },
  "history_reason": "no_personal_history_dependency",
  "before_generation_ms": 12009.334,
  "receipt_persistence_ms": 9.372
}
```
Generated answer:

A hash table is a data structure that stores key-value pairs and allows for fast retrieval, insertion, and deletion of data. It uses a function called a **hash function** to compute an index (or "bucket") where each value should be stored based on its key.

Here's how it works:
1.  **Hash Function**: When you insert or look up a key, the hash function converts that key into an integer index within the bounds of the table's array.
2.  **Storage**: The corresponding value is placed in the bucket at that index.
3.  **Collision Handling**: If two keys produce the same index (a collision), the table must resolve it—common methods include chaining (using linked lists) or open addressing (probing for the next available slot).

**Why use one?**
Hash tables typically offer average-case time complexity of $O(1)$ for these operations, making them highly efficient for scenarios like caching, database indexing, or implementing dictionaries.

## C Chronology

User: What did we decide about VoiceWorks last month?

Interpreted turn/state:
```json
{
  "turn_id": "cc242940e49449988679d9954c4855b2",
  "types": [
    "question"
  ],
  "subject": "VoiceWorks decision",
  "objective": "retrieve stored decision from prior month",
  "response_objective": "answer",
  "changes": [],
  "correction": false,
  "supersession": false,
  "rejected_changes": [],
  "fallback": false,
  "controller_fallbacks": []
}
```
Accepted conversation state:
```json
{
  "version": 1,
  "subject": "VoiceWorks decision",
  "objective": "retrieve stored decision from prior month",
  "items": [],
  "superseded": [],
  "correction_subjects": []
}
```
Context recipe (includes evidence/history decision and response objective):
```json
{
  "version": 1,
  "turn_id": "cc242940e49449988679d9954c4855b2",
  "turn_types": [
    "question"
  ],
  "semantic_intent": "retrieve_prior_decision",
  "semantic_turn_types": [
    "question"
  ],
  "semantic_response_objective": "answer",
  "subject": "VoiceWorks decision",
  "objective": "retrieve stored decision from prior month",
  "state_changes": [],
  "correction": false,
  "supersession": false,
  "history_requested": true,
  "vault": {
    "used": true,
    "reason": "existing_evidence_policy"
  },
  "web": {
    "used": false,
    "reason": "no_provider"
  },
  "external_research_requested": false,
  "attachment_use": 0,
  "tools": [],
  "capabilities_requested": [],
  "model": "qwen3.5:9b-q4_K_M",
  "reasoning_tier": "standard",
  "response_objective": "answer",
  "context_blocks": {
    "system": {
      "characters": 4022,
      "approx_tokens": 1006
    },
    "focus": {
      "characters": 0,
      "approx_tokens": 0
    },
    "instruction": {
      "characters": 47,
      "approx_tokens": 12
    },
    "attachment": {
      "characters": 0,
      "approx_tokens": 0
    },
    "vault": {
      "characters": 123,
      "approx_tokens": 31
    },
    "web": {
      "characters": 0,
      "approx_tokens": 0
    },
    "recent_conversation": {
      "characters": 0,
      "approx_tokens": 0
    },
    "older_context": {
      "characters": 0,
      "approx_tokens": 0
    },
    "adaptive_profile": {
      "characters": 2,
      "approx_tokens": 1
    },
    "world_state": {
      "characters": 181,
      "approx_tokens": 46
    },
    "turn_state": {
      "characters": 353,
      "approx_tokens": 89
    },
    "total": {
      "characters": 4975,
      "approx_tokens": 1244
    }
  },
  "evidence_ids": [
    "synthetic-history-1"
  ],
  "confidence": 0.95,
  "ambiguity": "low",
  "policy_overrides": [],
  "capability_gaps": [],
  "fallback_paths": [],
  "generation": {
    "status": "complete",
    "source_guard_rejected": false
  },
  "history_reason": "semantic_personal_or_chronological_reference",
  "before_generation_ms": 12235.446,
  "receipt_persistence_ms": 10.131
}
```
Generated answer:

We decided last month that you should send concise bug reports to VoiceWorks support via email.

## D Thinking aloud

User: I'm thinking we could put a small cache between the layers, and let each layer invalidate only its own entries.

Interpreted turn/state:
```json
{
  "turn_id": "77a6527b51bd4c6084904408395a7586",
  "types": [
    "exploration"
  ],
  "subject": "inter-layer caching strategy",
  "objective": "evaluate the proposed cache invalidation approach between layers",
  "response_objective": "advance_idea",
  "changes": [],
  "correction": false,
  "supersession": false,
  "rejected_changes": [],
  "fallback": false,
  "controller_fallbacks": []
}
```
Accepted conversation state:
```json
{
  "version": 1,
  "subject": "inter-layer caching strategy",
  "objective": "evaluate the proposed cache invalidation approach between layers",
  "items": [],
  "superseded": [],
  "correction_subjects": []
}
```
Context recipe (includes evidence/history decision and response objective):
```json
{
  "version": 1,
  "turn_id": "77a6527b51bd4c6084904408395a7586",
  "turn_types": [
    "exploration"
  ],
  "semantic_intent": "exploration",
  "semantic_turn_types": [
    "exploration"
  ],
  "semantic_response_objective": "advance_idea",
  "subject": "inter-layer caching strategy",
  "objective": "evaluate the proposed cache invalidation approach between layers",
  "state_changes": [],
  "correction": false,
  "supersession": false,
  "history_requested": false,
  "vault": {
    "used": false,
    "reason": "not_required"
  },
  "web": {
    "used": false,
    "reason": "no_provider"
  },
  "external_research_requested": false,
  "attachment_use": 0,
  "tools": [],
  "capabilities_requested": [],
  "model": "qwen3.5:9b-q4_K_M",
  "reasoning_tier": "standard",
  "response_objective": "advance_idea",
  "context_blocks": {
    "system": {
      "characters": 2963,
      "approx_tokens": 741
    },
    "focus": {
      "characters": 0,
      "approx_tokens": 0
    },
    "instruction": {
      "characters": 111,
      "approx_tokens": 28
    },
    "attachment": {
      "characters": 0,
      "approx_tokens": 0
    },
    "vault": {
      "characters": 0,
      "approx_tokens": 0
    },
    "web": {
      "characters": 0,
      "approx_tokens": 0
    },
    "recent_conversation": {
      "characters": 0,
      "approx_tokens": 0
    },
    "older_context": {
      "characters": 0,
      "approx_tokens": 0
    },
    "adaptive_profile": {
      "characters": 2,
      "approx_tokens": 1
    },
    "world_state": {
      "characters": 0,
      "approx_tokens": 0
    },
    "turn_state": {
      "characters": 394,
      "approx_tokens": 99
    },
    "total": {
      "characters": 3601,
      "approx_tokens": 901
    }
  },
  "evidence_ids": [],
  "confidence": 0.95,
  "ambiguity": "low",
  "policy_overrides": [],
  "capability_gaps": [],
  "fallback_paths": [],
  "generation": {
    "status": "complete",
    "source_guard_rejected": false
  },
  "history_reason": "current_conversation_sufficient",
  "before_generation_ms": 12571.97,
  "receipt_persistence_ms": 10.74
}
```
Generated answer:

That approach isolates invalidation logic cleanly, but it introduces a consistency risk: if Layer A updates data that Layer B also serves, Layer B won't know to drop its copy unless you define a shared invalidation signal or versioning scheme across layers.

Implication: You'll need a lightweight coordination mechanism—either a global cache version number incremented on writes, or an event bus that broadcasts invalidations when critical data changes. Without it, stale reads can slip through between layers even if each layer manages its own entries well.

Next step: Sketch the invalidation trigger points (e.g., write to primary store) and decide whether to use a shared version header or an explicit broadcast channel. Which fits your latency and complexity tolerance better?

## E Coding

User: Write a Python function that sorts a list of integers without modifying the input.

Interpreted turn/state:
```json
{
  "turn_id": "c7e9f9c380e64e17891a2e0c49b25f74",
  "types": [
    "instruction"
  ],
  "subject": "Python sorting function implementation",
  "objective": "Provide a Python function that sorts a list of integers immutably.",
  "response_objective": "perform_task",
  "changes": [],
  "correction": false,
  "supersession": false,
  "rejected_changes": [],
  "fallback": false,
  "controller_fallbacks": [
    "response_objective_task_policy"
  ]
}
```
Accepted conversation state:
```json
{
  "version": 1,
  "subject": "Python sorting function implementation",
  "objective": "Provide a Python function that sorts a list of integers immutably.",
  "items": [],
  "superseded": [],
  "correction_subjects": []
}
```
Context recipe (includes evidence/history decision and response objective):
```json
{
  "version": 1,
  "turn_id": "c7e9f9c380e64e17891a2e0c49b25f74",
  "turn_types": [
    "instruction"
  ],
  "semantic_intent": "instruction",
  "semantic_turn_types": [
    "instruction"
  ],
  "semantic_response_objective": "answer",
  "subject": "Python sorting function implementation",
  "objective": "Provide a Python function that sorts a list of integers immutably.",
  "state_changes": [],
  "correction": false,
  "supersession": false,
  "history_requested": false,
  "vault": {
    "used": false,
    "reason": "not_required"
  },
  "web": {
    "used": false,
    "reason": "no_provider"
  },
  "external_research_requested": false,
  "attachment_use": 0,
  "tools": [],
  "capabilities_requested": [],
  "model": "qwen3.5:9b-q4_K_M",
  "reasoning_tier": "standard",
  "response_objective": "perform_task",
  "context_blocks": {
    "system": {
      "characters": 2950,
      "approx_tokens": 738
    },
    "focus": {
      "characters": 0,
      "approx_tokens": 0
    },
    "instruction": {
      "characters": 82,
      "approx_tokens": 21
    },
    "attachment": {
      "characters": 0,
      "approx_tokens": 0
    },
    "vault": {
      "characters": 0,
      "approx_tokens": 0
    },
    "web": {
      "characters": 0,
      "approx_tokens": 0
    },
    "recent_conversation": {
      "characters": 0,
      "approx_tokens": 0
    },
    "older_context": {
      "characters": 0,
      "approx_tokens": 0
    },
    "adaptive_profile": {
      "characters": 2,
      "approx_tokens": 1
    },
    "world_state": {
      "characters": 0,
      "approx_tokens": 0
    },
    "turn_state": {
      "characters": 406,
      "approx_tokens": 102
    },
    "total": {
      "characters": 3571,
      "approx_tokens": 893
    }
  },
  "evidence_ids": [],
  "confidence": 0.95,
  "ambiguity": "low",
  "policy_overrides": [],
  "capability_gaps": [],
  "fallback_paths": [
    "response_objective_task_policy"
  ],
  "generation": {
    "status": "complete",
    "source_guard_rejected": false
  },
  "history_reason": "current_conversation_sufficient",
  "before_generation_ms": 12428.211,
  "receipt_persistence_ms": 11.035
}
```
Generated answer:

Here is a Python function that sorts a list of integers immutably by returning a new sorted list, leaving the original unchanged:

```python
def sort_immutable(integers):
    return sorted(integers)
```

The built-in `sorted()` function creates and returns a new list containing all items from the input iterable in ascending order. It does not modify the original list passed as an argument.

## F STT noise

User: Explain a hash table, splork @@%%, and how collisions are handled.

Interpreted turn/state:
```json
{
  "turn_id": "d23d2984d190439ab72baf7d30f7808d",
  "types": [
    "instruction"
  ],
  "subject": "hash table and collision handling",
  "objective": "explain hash tables, collisions, and resolution strategies",
  "response_objective": "answer",
  "changes": [],
  "correction": false,
  "supersession": false,
  "rejected_changes": [],
  "fallback": false,
  "controller_fallbacks": []
}
```
Accepted conversation state:
```json
{
  "version": 1,
  "subject": "hash table and collision handling",
  "objective": "explain hash tables, collisions, and resolution strategies",
  "items": [],
  "superseded": [],
  "correction_subjects": []
}
```
Context recipe (includes evidence/history decision and response objective):
```json
{
  "version": 1,
  "turn_id": "d23d2984d190439ab72baf7d30f7808d",
  "turn_types": [
    "instruction"
  ],
  "semantic_intent": "explain_concept",
  "semantic_turn_types": [
    "instruction"
  ],
  "semantic_response_objective": "answer",
  "subject": "hash table and collision handling",
  "objective": "explain hash tables, collisions, and resolution strategies",
  "state_changes": [],
  "correction": false,
  "supersession": false,
  "history_requested": false,
  "vault": {
    "used": false,
    "reason": "not_required"
  },
  "web": {
    "used": false,
    "reason": "no_provider"
  },
  "external_research_requested": false,
  "attachment_use": 0,
  "tools": [],
  "capabilities_requested": [],
  "model": "qwen3.5:9b-q4_K_M",
  "reasoning_tier": "standard",
  "response_objective": "answer",
  "context_blocks": {
    "system": {
      "characters": 2922,
      "approx_tokens": 731
    },
    "focus": {
      "characters": 0,
      "approx_tokens": 0
    },
    "instruction": {
      "characters": 66,
      "approx_tokens": 17
    },
    "attachment": {
      "characters": 0,
      "approx_tokens": 0
    },
    "vault": {
      "characters": 0,
      "approx_tokens": 0
    },
    "web": {
      "characters": 0,
      "approx_tokens": 0
    },
    "recent_conversation": {
      "characters": 0,
      "approx_tokens": 0
    },
    "older_context": {
      "characters": 0,
      "approx_tokens": 0
    },
    "adaptive_profile": {
      "characters": 2,
      "approx_tokens": 1
    },
    "world_state": {
      "characters": 0,
      "approx_tokens": 0
    },
    "turn_state": {
      "characters": 387,
      "approx_tokens": 97
    },
    "total": {
      "characters": 3508,
      "approx_tokens": 877
    }
  },
  "evidence_ids": [],
  "confidence": 0.95,
  "ambiguity": "low",
  "policy_overrides": [],
  "capability_gaps": [],
  "fallback_paths": [],
  "generation": {
    "status": "complete",
    "source_guard_rejected": false
  },
  "history_reason": "current_conversation_sufficient",
  "before_generation_ms": 10866.4,
  "receipt_persistence_ms": 8.942
}
```
Generated answer:

A **hash table** is a data structure that stores key-value pairs using an array (the "table") and a function called a **hash function**. The hash function takes a key (like a string or number) and converts it into an index—a specific position in the array—where the value should be stored. This allows for constant-time, $O(1)$, average-case lookups, insertions, and deletions.

You wrote "splork @@%%" instead of asking about collisions. I assume you meant **collisions**, which happen when two different keys produce the same hash index. Since an array slot can only hold one item at a time, a collision must be resolved.

Here are the primary strategies for handling collisions:

1.  **Separate Chaining**: Each array slot holds a list (or linked list) of entries. When a collision occurs, the new entry is simply appended to the list at that index. This keeps the hash table simple but can degrade performance if many items collide (creating long lists).
2.  **Open Addressing**: All entries are stored directly in the array. When a collision occurs, the algorithm probes for the next available slot using a specific sequence (e.g., linear probing: check the next index; quadratic probing: jump by increasing intervals; double hashing: use a second hash function to determine the step size). This avoids extra memory overhead but can lead to clustering, where groups of filled slots make future insertions slower.

In practice, modern implementations often combine techniques or tune parameters (like keeping the table less than 70% full) to maintain efficiency. If you need details on a specific language's implementation or performance trade-offs, let me know.
