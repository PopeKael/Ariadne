# Ariadne KnowledgeVault MCP Server

`ariadne_mcp.py` exposes the existing KnowledgeVault to an MCP client over standard input/output. It is read-only. Chunk embeddings are optional and are generated only by a locally hosted Ollama model; no vault content is sent to an external embedding service.

## Data model

- `00_System/library.json` remains the authoritative catalogue.
- `Processed/` remains the source corpus.
- `00_System/Data/embedding-index.json` is an atomic, persistent local index. It contains vectors plus path, heading, chunk ID, content hash, model, and index version. `library.json` remains unchanged.
- Search uses bounded hybrid ranking: catalogue/chunk lexical relevance, cosine similarity, then existing entity/graph metadata signals. If the index or Ollama is unavailable, it degrades cleanly to lexical and graph ranking.

## Tools

- `search_knowledge_chunks(query, limit=5)` is the preferred retrieval tool. It ranks the existing heading-aware Markdown passages and returns source and heading. Additive diagnostics are `lexical_score`, `semantic_score`, `graph_score`, and `combined_score`; clients may ignore them.
- `get_knowledge_chunk(chunk_id)` re-reads a passage returned by chunk search.
- `search_knowledge_vault(query, limit=5)` remains available for document-level catalogue browsing.
- `get_knowledge_document(document_id, offset=0, max_chars=12000)` returns processed Markdown for a selected result. Use `next_offset` to page a long document.

## Retrieval evaluation

`evaluation/retrieval_cases.json` is the versioned regression set for chunk retrieval. Cases may expect a document ID or, where necessary, an exact chunk ID. Run it after ranking, chunking, or embedding changes:

```powershell
py -3 .\00_System\evaluate_retrieval.py
```

The JSON report includes Recall@K and mean reciprocal rank (MRR), plus the returned chunk IDs for misses. Add cases from real queries and regressions; do not silently change an expectation just to improve a score.

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

## Local embedding index

Install a local embedding model once (the default is `nomic-embed-text`), then run from the vault root:

```powershell
ollama pull nomic-embed-text
.\00_System\Build-Embeddings.ps1
```

The normal command only embeds new or changed chunks, removes deleted/replaced chunks, and retries previously failed chunks. It writes a complete replacement to a temporary file and atomically commits it, preserving the previous valid index if a run fails before commit.

```powershell
.\00_System\Build-Embeddings.ps1 -Status
.\00_System\Build-Embeddings.ps1 -Rebuild   # intentionally discards cached vectors
```

Set `ARIADNE_EMBEDDING_MODEL` (or pass `-Model`) to use another locally installed Ollama embedding model. `ARIADNE_OLLAMA_URL` is constrained to `localhost` / `127.0.0.1`.

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
