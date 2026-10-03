"""Opt-in live Qwen evaluation against synthetic chats, never the user's Vault.

Run with --live to generate six answers and a Markdown intermediate-state report.
This does not launch/restart services or change inference settings.
"""
import argparse
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import server
from home_chat_store import ChatStore
from librarian_events import LibrarianEventStream
from test_turn_assembly import OLD_CLAIM, CORRECTION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, default=Path("runtime/turn-assembly-evaluation.md"))
    args = parser.parse_args()
    real_mcp = server._home_mcp()

    class SyntheticMcp:
        def identity_system_prefix(self, *args):
            return "You are Ariadne, Morgan's conversational assistant.", {"id": "synthetic-evaluation"}

        def retrieve_evidence(self, arguments):
            return {"results": [{"chunk_id": "synthetic-history-1", "document_id": "synthetic-history",
                "content": "Last month Morgan decided to send concise bug reports to VoiceWorks support by email.",
                "title": "Synthetic prior decision", "path": "Synthetic/history.md", "combined_score": .9}],
                "candidate_count": 1, "selected_count": 1, "match_count": 1}

        def ollama_chat(self, messages, **kwargs):
            return real_mcp.ollama_chat(messages, **kwargs)

    class NoExternalSearch:
        def route(self):
            return None

    def planner_context(query, history, attachments, mode, selected):
        return {"request": query, "active_knowledge_source": mode, "attachments": attachments,
            "selected_tool_ids": sorted(selected), "available_tools": [],
            "capabilities": {"vault_available": True, "external_research_allowed": False},
            "conversation_state": {"recent_messages": history[-4:]}, "world_state": {}}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("# Evaluation starting\n", encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="ariadne-turn-eval-") as temporary:
        root = Path(temporary)
        adapter = SyntheticMcp()
        overrides = {"HOME_CHAT_STORE": ChatStore(root), "_home_mcp": lambda: adapter,
            "home_planner_context": planner_context,
            "home_adaptive_context_for_query": lambda *a, **k: {},
            "SEARCH_PROVIDER_REGISTRY": NoExternalSearch(),
            "HOME_EVENTS_PATH": root / "home-events.md", "DOCUMENT_WORK_ROOT": root / "documents",
            "LIBRARIAN_EVENT_STREAM": LibrarianEventStream(root / "recipes.jsonl"),
            "record_model_residency_event": lambda *a, **k: None,
            "publish_home_activity": lambda *a, **k: {}}
        with patch.multiple(server, **overrides):
            report = ["# Live semantic conversation evaluation", "", "Synthetic data; actual existing interpreter and answer adapters, Qwen 3.5 9B. No private Vault retrieval.", ""]
            cases = [("A Correction", CORRECTION), ("B Generic fact", "What is a hash table?"),
                ("C Chronology", "What did we decide about VoiceWorks last month?"),
                ("D Thinking aloud", "I'm thinking we could put a small cache between the layers, and let each layer invalidate only its own entries."),
                ("E Coding", "Write a Python function that sorts a list of integers without modifying the input."),
                ("F STT noise", "Explain a hash table, splork @@%%, and how collisions are handled.")]
            for label, query in cases:
                chat = server.HOME_CHAT_STORE.create()
                if label.startswith("A"):
                    old_id, _ = server.HOME_CHAT_STORE.begin_turn(chat["chat_id"], "Tell me about that relationship.", "previous-model", {})
                    server.HOME_CHAT_STORE.complete_turn(chat["chat_id"], old_id, OLD_CLAIM,
                        model="previous-model", used_vault=False, sources=[], retrieval={}, timing={}, identity_kernel={})
                result = server.home_chat_payload(query, [], "all", chat["chat_id"])
                message = server.HOME_CHAT_STORE.get(chat["chat_id"])["messages"][-1]
                job = message["turn_assembly"]
                recipe = result["timing"]["context_recipe"]
                if recipe["generation"]["status"] != "complete":
                    raise RuntimeError(f"{label}: expected a generated answer, got {recipe['generation']}")
                expected_vault = label.startswith("C")
                if result["used_vault"] != expected_vault:
                    raise RuntimeError(f"{label}: incorrect Vault decision")
                if label.startswith("A") and (not job["supersession"] or not any(i["kind"] == "uncertainty" for i in job["state"]["items"])):
                    raise RuntimeError("Correction did not supersede inference and preserve uncertainty")
                if label.startswith("D") and job["response_objective"] != "advance_idea":
                    raise RuntimeError("Thinking-aloud objective is not advance_idea")
                if label.startswith("E") and job["response_objective"] != "perform_task":
                    raise RuntimeError("Coding objective is not perform_task")
                if label.startswith("F") and "splork" in json.dumps(job["state"]):
                    raise RuntimeError("Malformed fragment entered conversation state")
                report.extend([f"## {label}", "", f"User: {query}", "", "Interpreted turn/state:", "```json",
                    json.dumps({k: v for k, v in job.items() if k != "state"}, indent=2), "```",
                    "Accepted conversation state:", "```json", json.dumps(job["state"], indent=2), "```",
                    "Context recipe (includes evidence/history decision and response objective):", "```json",
                    json.dumps(recipe, indent=2), "```", "Generated answer:", "", result["answer"], ""])
                args.output.write_text("\n".join(report), encoding="utf-8")
                print(json.dumps({"case": label, "types": job["types"], "supersession": job["supersession"],
                    "vault": result["used_vault"], "objective": job["response_objective"],
                    "fallback": result["planner"]["fallback"], "answer_chars": len(result["answer"])}), flush=True)
    print(f"REPORT {args.output.resolve()}", flush=True)


if __name__ == "__main__":
    main()
