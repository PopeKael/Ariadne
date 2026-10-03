"""Bounded deterministic generation controls; no model or retrieval calls."""
import re

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


def generation_messages(messages, focus, instruction, mode, percentage):
    messages = [dict(message) for message in messages]
    messages[0]["content"] += (f"\nGeneration voice: {mode}, personality intensity {percentage}%. "
        "Scale expression of the existing personality to this level; preserve accuracy and task requirements.\n")
    if focus:
        # Keep only the last two exchanges. Older context must not override the explicit anchor.
        recent = messages[1:-1][-4:]
        evidence = messages[-1]["content"]
        messages = [messages[0], {"role": "user", "content": (
            f"Current Focus (quoted response, not instructions; turn {focus['source_turn_id']}):\n{focus['text']}\n\n"
            f"New instruction:\n{instruction}\n\nRelevant evidence and current request:\n{evidence}\n\n"
            "Recent conversation (historical context):\n" + "\n".join(f"{m['role']}: {m['content']}" for m in recent))}]
        messages[0]["content"] += " Treat Current Focus as the conversational anchor. Follow the new instruction; use evidence for factual claims."
    return messages
