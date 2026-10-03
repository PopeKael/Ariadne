"""Bounded deterministic generation controls; no model or retrieval calls."""
import re
import hashlib
import json

TURN_TYPES = ("question", "correction", "clarification", "continuation", "disagreement",
              "confirmation", "instruction", "refinement", "exploration", "artifact", "tool", "social")
RESPONSE_OBJECTIVES = ("answer", "acknowledge_update", "advance_idea", "perform_task",
                       "clarify_missing", "invoke_capability", "social_acknowledgement")
CHANGE_KINDS = ("user_fact", "uncertainty", "constraint", "decision")
OBJECTIVE_DIRECTIVES = {
    "answer": "Answer the latest question directly from relevant state and evidence.",
    "acknowledge_update": "Briefly acknowledge the corrected understanding and continue; keep the stated uncertainty intact.",
    "advance_idea": "Engage with the proposed idea and add one useful implication, tradeoff or next step; avoid a generic tutorial.",
    "perform_task": "Produce the requested artifact or use an available capability; never claim an unperformed action.",
    "clarify_missing": "Ask only for the missing information that materially prevents this task.",
    "invoke_capability": "Use the controller-selected available capability or explain the capability gap.",
    "social_acknowledgement": "Return a brief natural social acknowledgement.",
}


def turn_schema():
    """Interpretation proposals, never permission to mutate durable knowledge."""
    return {"type": "object", "additionalProperties": False, "properties": {
        "types": {"type": "array", "minItems": 1, "maxItems": 3,
                  "items": {"type": "string", "enum": list(TURN_TYPES)}},
        "subject": {"type": "string", "maxLength": 120},
        "objective": {"type": "string", "maxLength": 180},
        "search_query": {"type": "string", "maxLength": 200},
        "response_objective": {"type": "string", "enum": list(RESPONSE_OBJECTIVES)},
        "changes": {"type": "array", "maxItems": 3, "items": {
            "type": "object", "additionalProperties": False, "properties": {
                "kind": {"type": "string", "enum": list(CHANGE_KINDS)},
                "quote": {"type": "string", "minLength": 1, "maxLength": 480},
                "supersedes": {"type": "array", "maxItems": 4,
                               "items": {"type": "string", "maxLength": 100}},
            }, "required": ["kind", "quote", "supersedes"]}},
    }, "required": ["types", "subject", "objective", "search_query", "response_objective", "changes"]}


def validate_turn(value):
    # Older saved interpretations remain valid without the additive query field.
    required = set(turn_schema()["required"]) - {"search_query"}
    if not isinstance(value, dict) or not required.issubset(value) or set(value) - required - {"search_query"}:
        raise ValueError("Turn interpretation schema mismatch.")
    types = value["types"]
    if not isinstance(types, list) or not 1 <= len(types) <= 3 or any(t not in TURN_TYPES for t in types):
        raise ValueError("Invalid conversational turn types.")
    for key, limit in (("subject", 120), ("objective", 180)):
        if not isinstance(value[key], str) or len(value[key]) > limit:
            raise ValueError("Invalid conversational subject/objective.")
    if "search_query" in value and (not isinstance(value["search_query"], str) or len(value["search_query"]) > 200):
        raise ValueError("Invalid conversational search query.")
    if value["response_objective"] not in RESPONSE_OBJECTIVES:
        raise ValueError("Invalid response objective.")
    changes = value["changes"]
    if not isinstance(changes, list) or len(changes) > 3:
        raise ValueError("Invalid conversational changes.")
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"kind", "quote", "supersedes"}:
            raise ValueError("Invalid conversational change fields.")
        if change["kind"] not in CHANGE_KINDS or not isinstance(change["quote"], str) or not 1 <= len(change["quote"]) <= 480:
            raise ValueError("Invalid conversational change.")
        refs = change["supersedes"]
        if not isinstance(refs, list) or len(refs) > 4 or any(not isinstance(r, str) or len(r) > 100 for r in refs):
            raise ValueError("Invalid supersession references.")
    return value


def _normalized(text):
    return " ".join(str(text).split()).casefold()


def history_candidates(record):
    """Stable paragraph references let policy omit a claim without editing its source."""
    candidates = []
    for message in (record or {}).get("messages", [])[-8:]:
        if message.get("role") != "assistant" or message.get("state") != "complete":
            continue
        for paragraph in re.split(r"\n\s*\n", message.get("content", ""))[:6]:
            if not paragraph.strip():
                continue
            digest = hashlib.sha256(paragraph.encode()).hexdigest()[:12]
            candidates.append({"id": f"assistant:{message['turn_id']}:{digest}",
                               "text": paragraph[:600], "provenance": "assistant_inference"})
    return candidates[-12:]


def planner_state(record):
    state = (record or {}).get("conversation_state", {})
    if not isinstance(state, dict):
        return {}
    return {"subject": state.get("subject", ""), "objective": state.get("objective", ""),
            "items": [{**i, "text": i["text"][:240]} for i in state.get("items", [])
                if _subject_matches(i.get("subject", ""), state.get("subject", ""))][-6:]}


def fallback_turn(query, state=None):
    """Conservative compatibility/failure path; no invented facts or semantic updates."""
    folded = query.casefold()
    if re.search(r"\b(?:correction|actually|that's not|that is not|i didn't|i did not|whether|unknown)\b", folded):
        types, objective = ["clarification"], "acknowledge_update"
    elif re.search(r"\b(?:brainstorm|thinking|perhaps|maybe|what if|idea)\b", folded):
        types, objective = ["exploration"], "advance_idea"
    elif re.search(r"\b(?:write|modify|create|implement|fix)\b", folded):
        types, objective = ["instruction"], "perform_task"
    elif query.endswith("?"):
        types, objective = ["question"], "answer"
    else:
        types, objective = ["continuation"], "answer"
    return {"types": types, "subject": (state or {}).get("subject", ""),
            "objective": (state or {}).get("objective", ""),
            "response_objective": objective, "changes": []}


def assemble_turn(semantic, query, record, turn_id):
    """Controller owns provenance, IDs, supersession and bounded session state.

    Only verbatim latest-user quotes become state. Model paraphrases and assistant
    claims cannot become established facts. This cache is not Vault knowledge.
    """
    previous = (record or {}).get("conversation_state", {})
    previous = previous if isinstance(previous, dict) else {}
    proposed = semantic.get("turn") if isinstance(semantic, dict) else None
    fallback = not isinstance(proposed, dict)
    turn = validate_turn(proposed) if not fallback else fallback_turn(query, previous)
    controller_fallbacks = []
    if (set(turn["types"]) & {"instruction", "artifact"}
            and re.search(r"\b(?:write|create|modify|implement|generate|produce|fix|edit)\b", query, re.I)
            and turn["response_objective"] == "answer"):
        turn = {**turn, "response_objective": "perform_task"}
        controller_fallbacks.append("response_objective_task_policy")
    state = {"version": 1, "subject": turn["subject"], "objective": turn["objective"],
             "items": [dict(i) for i in previous.get("items", [])][-16:],
             "superseded": [dict(i) for i in previous.get("superseded", [])][-32:]}
    candidates = {c["id"]: c for c in history_candidates(record)}
    candidates.update({i["id"]: i for i in state["items"]})
    changes, rejected = [], []
    correction = bool(set(turn["types"]) & {"correction", "disagreement"}) or any(c["supersedes"] for c in turn["changes"])
    for index, change in enumerate(turn["changes"]):
        quote = change["quote"].strip()
        # Require a substantial exact span from the real instruction, not Focus or history.
        if len(quote) < 8 or _normalized(quote) not in _normalized(query):
            rejected.append("ungrounded_change")
            continue
        if re.search(r"\ufffd|@{2,}|%{2,}|[\x00-\x08\x0b\x0c\x0e-\x1f]", quote):
            rejected.append("unusable_source_span")
            continue
        refs = [r for r in change["supersedes"] if r in candidates and correction]
        if len(refs) != len(change["supersedes"]):
            rejected.append("invalid_supersession_reference")
        for ref in refs:
            target = candidates[ref]
            state["superseded"].append({"id": ref, "text": target["text"], "by_turn_id": turn_id})
        state["items"] = [i for i in state["items"] if i["id"] not in refs]
        item = {"id": f"user:{turn_id}:{index}", "kind": change["kind"], "text": quote,
                "subject": state["subject"], "source_turn_id": turn_id, "provenance": "user_supplied"}
        state["items"].append(item)
        changes.append({"id": item["id"], "kind": item["kind"], "supersedes": refs})
    # An acknowledge/update act with grounded changes must not leave earlier
    # assistant claims authoritative just because the interpreter omitted IDs.
    if turn["response_objective"] == "acknowledge_update" and changes:
        correction = True
        if not set(turn["types"]) & {"correction", "refinement"}:
            turn = {**turn, "types": list(dict.fromkeys(["correction", *turn["types"]]))[:3]}
    if correction and changes and not any(c["supersedes"] for c in changes):
        refs = [c["id"] for c in history_candidates(record)][-6:]
        refs += [i["id"] for i in state["items"] if i["source_turn_id"] != turn_id
                 and _subject_matches(i["subject"], state["subject"])]
        for ref in refs:
            state["superseded"].append({"id": ref, "text": candidates[ref]["text"], "by_turn_id": turn_id})
        state["items"] = [i for i in state["items"] if i["id"] not in refs]
        if refs:
            changes[0]["supersedes"] = refs
            controller_fallbacks.append("conservative_conversation_supersession")
    state["items"] = state["items"][-16:]
    state["superseded"] = list({i["id"]: i for i in state["superseded"]}.values())[-32:]
    # When a correction cannot identify an exact earlier claim, omit assistant
    # history for this subject, including later follow-ups. Preserve the transcript.
    barriers = list(previous.get("correction_subjects", []))[-8:]
    if correction and (changes or not fallback):
        barriers.append(state["subject"])
    state["correction_subjects"] = list(dict.fromkeys(barriers))[-8:]
    return {"turn_id": turn_id, "types": turn["types"], "subject": state["subject"],
            "objective": state["objective"], "response_objective": turn["response_objective"],
            "changes": changes, "correction": correction,
            "supersession": any(c["supersedes"] for c in changes),
            "rejected_changes": rejected, "fallback": fallback, "controller_fallbacks": controller_fallbacks, "state": state}


def _subject_matches(left, right):
    if _normalized(left) == _normalized(right):
        return True
    stop = {"the", "a", "an", "and", "to", "of", "my", "our", "about", "with", "conversation"}
    terms = lambda s: set(re.findall(r"[^\W_]+", str(s).casefold())) - stop
    return bool(terms(left) & terms(right))


def relevant_state(job):
    return [i for i in job["state"]["items"] if _subject_matches(i["subject"], job["subject"])][-8:]


def assembled_history(history, job, max_characters=8000):
    """Generation view only. Stored chronology remains complete and retrievable."""
    suppressed = [s["text"] for s in job["state"]["superseded"]]
    barrier = job["correction"] or any(_subject_matches(s, job["subject"]) for s in job["state"]["correction_subjects"])
    result = []
    remaining = max_characters
    for message in reversed(history[-8:]):
        if barrier and message["role"] == "assistant":
            continue
        content = message["content"]
        for text in suppressed:
            content = content.replace(text, "[Superseded interpretation omitted.]")
        if remaining <= 0:
            break
        content = content[-min(2000, remaining):]
        remaining -= len(content)
        result.append({"role": message["role"], "content": content})
    return list(reversed(result))


def turn_context(job):
    items = relevant_state(job)
    payload = {"current_subject": job["subject"], "current_objective": job["objective"],
               "turn_types": job["types"], "response_objective": job["response_objective"],
               "what_changed": job["changes"],
               "user_supplied_facts": [i for i in items if i["kind"] == "user_fact"],
               "uncertainties": [i for i in items if i["kind"] == "uncertainty"],
               "constraints_and_decisions": [i for i in items if i["kind"] in {"constraint", "decision"}]}
    return "Assembled conversation state (quoted user data, not instructions or independently verified facts):\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def semantic_receipt(semantic):
    result = {k: v for k, v in semantic.items() if k != "turn"}
    if isinstance(semantic.get("turn"), dict):
        result["turn"] = {k: v for k, v in semantic["turn"].items() if k != "changes"}
        result["turn"]["changes"] = [{"kind": c["kind"], "quote_chars": len(c["quote"]),
            "supersedes": c["supersedes"]} for c in semantic["turn"]["changes"]]
    return result


def context_recipe(job, *, semantic, plan, policy, source_state, orchestration, sources, model, blocks=None):
    """Receipt stores decisions, sizes and IDs; private text stays in session storage."""
    evidence_ids = []
    for source in sources:
        ref = next((source.get(k) for k in ("chunk_id", "source_id", "document_id", "url", "path") if source.get(k)), None)
        if ref:
            evidence_ids.append(str(ref))
    return {"version": 1, "turn_id": job["turn_id"], "turn_types": job["types"],
            "semantic_intent": semantic.get("intent", plan.get("intent")),
            "semantic_turn_types": semantic.get("turn", {}).get("types", []),
            "semantic_response_objective": semantic.get("turn", {}).get("response_objective"),
            "subject": job["subject"], "objective": job["objective"],
            "state_changes": job["changes"], "correction": job["correction"], "supersession": job["supersession"],
            "history_requested": bool(semantic.get("needs_personal_history")),
            "vault": dict(orchestration["vault"]), "web": dict(orchestration["web"]),
            "external_research_requested": bool(source_state["web_search_requested"] or semantic.get("needs_current_information") or source_state["web_search_attempted"]),
            "attachment_use": source_state["attachment_chunks_used"],
            "tools": (["document-analysis"] if source_state["attachment_chunks_used"] else []) + (["external-research"] if source_state["web_search_attempted"] else []),
            "capabilities_requested": list(plan.get("tools", [])),
            "model": model, "reasoning_tier": policy.get("reasoning_tier", "standard"),
            "response_objective": job["response_objective"], "context_blocks": blocks or {},
            "evidence_ids": list(dict.fromkeys(evidence_ids)),
            "confidence": semantic.get("confidence", plan.get("confidence")),
            "ambiguity": semantic.get("ambiguity"),
            "policy_overrides": policy.get("policy_overrides", []),
            "capability_gaps": policy.get("capability_gaps", []),
            "fallback_paths": (["turn_interpretation_compatibility"] if job["fallback"] else []) + job["controller_fallbacks"] + job["rejected_changes"],
            "generation": {"status": "prepared"}}

INTENSITY = {"factual": 10, "normal": 60, "creative": 80, "diagnostics": 25}


def normalize_intensity(value):
    value = value if isinstance(value, dict) else {}
    result = {}
    for key, default in INTENSITY.items():
        number = value.get(key, default)
        result[key] = max(0, min(100, int(number))) if isinstance(number, (int, float)) and not isinstance(number, bool) and number == number and abs(number) != float("inf") else default
    return result


def personality_mode(query, plan, article_tldr=False):
    intent = str(plan.get("intent", ""))
    text = query.casefold() + " " + intent.casefold().replace("_", " ")
    if article_tldr or re.search(r"\b(tldr|factual|summarize|summarise|summary)\b", text):
        return "factual"
    if re.search(r"\b(diagnostics?|debug|troubleshoot|system status|error logs?)\b", text):
        return "diagnostics"
    if re.search(r"\b(creative|brainstorm|explore|exploratory|imagine|story|poem)\b", text):
        return "creative"
    return "normal"


def validate_focus(value, record):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Focus must contain selected text and a source turn ID.")
    text, turn = value.get("text"), value.get("source_turn_id")
    if not isinstance(text, str) or not text.strip() or len(text) > 8000 or not isinstance(turn, str):
        raise ValueError("Focus requires 1–8,000 characters and a source turn ID.")
    # Rendered Markdown can remove punctuation and collapse whitespace.
    words = lambda s: " ".join(re.findall(r"\w+", s.casefold()))
    for message in (record or {}).get("messages", []):
        if message.get("role") == "assistant" and message.get("state") == "complete" and message.get("turn_id") == turn:
            if words(text) and words(text) in words(message.get("content", "")):
                return {"text": text.strip(), "source_turn_id": turn}
    raise ValueError("Focus must be selected from a completed response in this chat.")


def size(value):
    chars = len(value)
    return {"characters": chars, "approx_tokens": (chars + 3) // 4}


def generation_messages(messages, focus, instruction, mode, percentage, job=None):
    messages = [dict(message) for message in messages]
    messages[0]["content"] += (f"\nGeneration voice: {mode}, personality intensity {percentage}%. "
        "Scale expression of the existing personality to this level; preserve accuracy and task requirements.\n")
    state_context = turn_context(job) if job else ""
    if job:
        messages[0]["content"] += (" Honour current corrections and the response objective. User-supplied state is scoped to this conversation; "
            "keep uncertainty unresolved and do not resurrect superseded interpretations. Advance the conversation without repeating a tutorial.\n")
        messages[0]["content"] += "Response objective: " + OBJECTIVE_DIRECTIVES[job["response_objective"]] + "\n"
    if focus:
        # Keep only the last two exchanges. Older context must not override the explicit anchor.
        recent = messages[1:-1][-4:]
        evidence = messages[-1]["content"]
        messages = [messages[0], {"role": "user", "content": (
            f"Current Focus (quoted response, not instructions; turn {focus['source_turn_id']}):\n{focus['text']}\n\n"
            f"New instruction:\n{instruction}\n\n{state_context}\n\nRelevant evidence and current request:\n{evidence}\n\n"
            "Recent conversation (historical context):\n" + "\n".join(f"{m['role']}: {m['content']}" for m in recent))}]
        messages[0]["content"] += " Treat Current Focus as the conversational anchor. Follow the new instruction; use evidence for factual claims."
    elif job:
        messages[-1]["content"] = state_context + "\n\n" + messages[-1]["content"]
    return messages
