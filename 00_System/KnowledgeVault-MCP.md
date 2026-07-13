# Ariadne KnowledgeVault MCP Server

`ariadne_mcp.py` exposes the existing KnowledgeVault to an MCP client over standard input/output. It is read-only and uses no embeddings, vector database, web service, or dependency beyond Python 3.12.

## Data model

- `00_System/library.json` remains the authoritative catalogue.
- `Processed/` remains the source corpus.
- The server creates no index files and never writes to the vault.
- Search is deterministic lexical scoring over the library title, topic, subtopics, tags, map entry, summary, and links. Results include a short excerpt from the processed Markdown.

## Tools

- `search_knowledge_chunks(query, limit=5)` is the preferred retrieval tool. It ranks heading-aware Markdown passages inside the strongest catalogue candidates and returns only the relevant chunks, with their source and heading.
- `get_knowledge_chunk(chunk_id)` re-reads a passage returned by chunk search.
- `search_knowledge_vault(query, limit=5)` remains available for document-level catalogue browsing.
- `get_knowledge_document(document_id, offset=0, max_chars=12000)` returns processed Markdown for a selected result. Use `next_offset` to page a long document.

## Local MCP configuration

Configure an MCP client with this stdio command:

```json
{
  "mcpServers": {
    "ariadne-knowledge-vault": {
      "command": "py",
      "args": ["-3", "D:\\Downloads\\KnowledgeVault\\00_System\\ariadne_mcp.py"]
    }
  }
}
```

In the client instructions, tell the model: “For questions about my KnowledgeVault, use `search_knowledge_chunks` first, answer from the returned chunks, and distinguish vault evidence from general knowledge. Use whole-document retrieval only when the chunks do not provide sufficient context.” MCP gives ChatGPT the tools; that instruction establishes the retrieval-first policy.

## Smoke test

Run from the vault root:

```powershell
$requests = @(
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}',
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}',
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search_knowledge_chunks","arguments":{"query":"Ariadne retrieval architecture","limit":3}}}'
)
$requests | py -3 .\00_System\ariadne_mcp.py
```
