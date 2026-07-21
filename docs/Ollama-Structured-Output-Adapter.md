# Ollama Structured-Output Adapter

## Verified local configuration

Environment tested on 2026-07-21:

- Ollama `0.32.1`
- Model `gpt-oss:20b`, digest `17052f91a42e97930ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`
- Model metadata: GGUF / gptoss, 20.9B parameters, MXFP4 quantisation, 131072-token context; the installed Modelfile defaults to medium reasoning and temperature `1`.
- Endpoint: `POST http://localhost:11434/api/chat`

The smallest reliable request form uses a `messages` array, `stream: false`, a JSON Schema object in `format`, and `temperature: 0`. Do not use `/api/generate` for structured GPT-OSS output in this environment: all tested generate variants returned an empty final response.

GPT-OSS returns the final schema-constrained value in `message.content` and may separately emit reasoning in `message.thinking`. Validation must use `message.content`; diagnostics should retain both fields and the complete response envelope.

The Python `ollama` client library is not installed. The rebuild adapter deliberately uses Ollama's loopback REST API through Python's standard library.

## Compatibility result

On the fixed Cerberus input, all four tested `/api/generate` variants returned an empty `response`. The `/api/chat` schema variants returned valid JSON; the minimal schema form and the full rebuild-enrichment schema both passed repeated trials. The complete failed pilot request/envelopes remain in `00_System/Data/rebuild-v1/model-captures.jsonl`.
