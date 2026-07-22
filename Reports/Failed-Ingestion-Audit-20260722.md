# Failed ingestion audit

Generated: 2026-07-22T11:40:56.701941+00:00

## Scope

- Rebuild-v1 rejected records: 67
- Current `Failed/` Markdown files: 67

## Classification

- `manual-review`: 17
- `model-json-failure`: 12
- `obsolete`: 1
- `pipeline-defect`: 15
- `retry-safe`: 22

## Confirmed findings

- All 67 records received an Ollama response envelope and have no recorded transport error.
- The adapter POSTs to /api/chat and reads response.message.content; it does not use /api/generate.
- Twelve captured message.content values are malformed/truncated JSON; no Markdown fences were observed in those captures.
- The JSON schema permits any string for proposed_domains even though semantic validation requires the controlled vocabulary; this is a pipeline defect behind the 15 invalid_domain_proposal rejections.
- The active catalogue contains none of the failed stable IDs or canonical content hashes.
- One 70-byte untitled source is empty/metadata-only after front matter and is obsolete intake material.

## Not observed / not proven

- Malformed HTTP request payloads, unsupported endpoint/format errors, transport timeouts, encoding/read failures, duplicate source identities, multiple JSON objects, concurrent-run evidence, or partial source moves.
- A fixed context-limit cause cannot be proved from the captures alone; malformed JSON ends mid-object and is consistent with truncated final generation.

## Per-source findings

| File | Type | Size | Failure stage | JSON | Classification | Recommended action |
|---|---:|---:|---|---|---|---|
| `Failed/(2) The Thailand Vloggers Hub _ Interesting article from Pattaya Mail.md` | facebook | 23834 | controlled_domain_validation | yes | `pipeline-defect` | Retry after enforcing the controlled domain enum in the JSON schema; preserve the original source unchanged. |
| `Failed/C&W Channel - Suburban Bangkok Entertainment2026-07-15T09_50_09+07_00__51cf13d44b7d.md` | chatgpt | 10262 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/C&W Channel - YouTube Packaging Workflow2026-07-15T09_47_54+07_00__1830d53068b1.md` | chatgpt | 2925 | semantic_validation | yes | `manual-review` | Leave in Failed for human relevance review; the model returned structurally valid but semantically insufficient enrichment. |
| `Failed/Code Stuff I don't Understand - Business Architecture Blueprint2026-07-14T12_36_18+07_00__1169d7497fcf.md` | chatgpt | 18771 | model_final_output_json_parse | no | `model-json-failure` | Keep in Failed; retry only after the captured truncated model final-output issue is addressed. |
| `Failed/Correspondence - Social Post Draft2026-07-14T18_53_59+07_00__7093c9f9640c.md` | chatgpt | 1022 | semantic_validation | yes | `manual-review` | Leave in Failed for human relevance review; the model returned structurally valid but semantically insufficient enrichment. |
| `Failed/Crowley and Epstein Conspiracy__f88cf8270282.md` | chatgpt | 4073 | controlled_domain_validation | yes | `pipeline-defect` | Retry after enforcing the controlled domain enum in the JSON schema; preserve the original source unchanged. |
| `Failed/Custom Instructions for Codex2026-07-11T19_36_32+07_00__48293d916efa.md` | chatgpt | 3356 | semantic_validation | yes | `manual-review` | Leave in Failed for human relevance review; the model returned structurally valid but semantically insufficient enrichment. |
| `Failed/DGX Spark Output Rates2026-07-11T18_25_06+07_00__aab6aec49882.md` | chatgpt | 17747 | controlled_domain_validation | yes | `pipeline-defect` | Retry after enforcing the controlled domain enum in the JSON schema; preserve the original source unchanged. |
| `Failed/Discord Communication Advice2026-07-14T18_30_37+07_00__88afbf38b697.md` | chatgpt | 4157 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/Extract OG Image URL2026-07-20T17_37_51+07_00__49c5a532cb72.md` | chatgpt | 2096 | semantic_validation | yes | `manual-review` | Leave in Failed for human relevance review; the model returned structurally valid but semantically insufficient enrichment. |
| `Failed/Foreign Land Ownership in Thailand2026-07-10T19_56_42+07_00__3c43b32e6816.md` | chatgpt | 15455 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/I need this transcribed2026-07-12T11_49_32+07_00.md` | web | 42542 | controlled_domain_validation | yes | `pipeline-defect` | Retry after enforcing the controlled domain enum in the JSON schema; preserve the original source unchanged. |
| `Failed/In this video which is over a year old - _The Last Fuel We'll Ever Need__ -...2026-07-12T11_50_01+07_00.md` | web | 7518 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/Local AI Model Landscape2026-07-08T00_51_19+07_00__be1c4e55ccd6.md` | chatgpt | 105139 | semantic_validation | yes | `manual-review` | Leave in Failed for human relevance review; the model returned structurally valid but semantically insufficient enrichment. |
| `Failed/Lyrica Medication Information2026-07-11T18_54_10+07_00__4bf59e75a8a2.md` | chatgpt | 2169 | controlled_domain_validation | yes | `pipeline-defect` | Retry after enforcing the controlled domain enum in the JSON schema; preserve the original source unchanged. |
| `Failed/My art - Banner Animation Workflow2026-07-14T13_25_29+07_00__a30471c82472.md` | chatgpt | 10714 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/My art - Branch · Illuminatus Trilogy Summary2026-07-14T12_42_07+07_00__96e760606900.md` | chatgpt | 38530 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/My art - Image Meme Creation2026-07-14T12_59_46+07_00__79e1df1b1068.md` | chatgpt | 4085 | model_final_output_json_parse | no | `model-json-failure` | Keep in Failed; retry only after the captured truncated model final-output issue is addressed. |
| `Failed/My art - Music Creation Inquiry2026-07-14T12_40_18+07_00__ddad824eb951.md` | chatgpt | 15684 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/My art - Photo Enhancement Request2026-07-14T13_09_49+07_00__6d155a0df6d1.md` | chatgpt | 14158 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/My art - Photo Sharpening Request2026-07-14T13_16_08+07_00__5b9ae4ffa86f.md` | chatgpt | 13273 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/My art - Remove person from scene2026-07-14T13_11_48+07_00__f09c62a0cca0.md` | chatgpt | 9612 | controlled_domain_validation | yes | `pipeline-defect` | Retry after enforcing the controlled domain enum in the JSON schema; preserve the original source unchanged. |
| `Failed/My art - Shot Analysis and Critique2026-07-14T13_29_53+07_00__a4b6c1c4ae0a.md` | chatgpt | 12505 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/My art - Song Comment Refinement2026-07-14T12_44_27+07_00__c9d8740438f8.md` | chatgpt | 21769 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/My art - Song creation vibe check2026-07-14T12_43_56+07_00__4d165a517fb2.md` | chatgpt | 41733 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/My Hosting - Stage 1.7 Parse2026-07-09T23_12_30+07_00__6bfc4c90eb0b.md` | chatgpt | 8056 | controlled_domain_validation | yes | `pipeline-defect` | Retry after enforcing the controlled domain enum in the JSON schema; preserve the original source unchanged. |
| `Failed/New chat2026-07-12T08_10_45+07_00__f5372c7a08f4.md` | gemini | 858 | semantic_validation | yes | `manual-review` | Leave in Failed for human relevance review; the model returned structurally valid but semantically insufficient enrichment. |
| `Failed/Pope Kael TV - 4️⃣ Tech & Streaming Setup Thread2026-07-14T13_39_47+07_00__e8f90ab0ad97.md` | chatgpt | 8435 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/Pope Kael TV - AI Task Nibbling2026-07-19T13_55_12+07_00__4bf7da46d15f.md` | chatgpt | 9085 | model_final_output_json_parse | no | `model-json-failure` | Keep in Failed; retry only after the captured truncated model final-output issue is addressed. |
| `Failed/Pope Kael TV - AI Video Title Ideas2026-07-19T13_54_04+07_00__a20320581c7d.md` | chatgpt | 10213 | semantic_validation | yes | `manual-review` | Leave in Failed for human relevance review; the model returned structurally valid but semantically insufficient enrichment. |
| `Failed/Pope Kael TV - Everyday AI Correspondence2026-07-19T13_56_32+07_00__1c70979cd3a7.md` | chatgpt | 2887 | controlled_domain_validation | yes | `pipeline-defect` | Retry after enforcing the controlled domain enum in the JSON schema; preserve the original source unchanged. |
| `Failed/Pope Kael TV - Smarter Tribes AI Workflows2026-07-14T14_02_23+07_00__a6bc9107abfd.md` | chatgpt | 5884 | model_final_output_json_parse | no | `model-json-failure` | Keep in Failed; retry only after the captured truncated model final-output issue is addressed. |
| `Failed/Pope Kael TV - Song Publishing Strategy2026-07-14T13_37_34+07_00__5c07fe28fbd2.md` | chatgpt | 4291 | semantic_validation | yes | `manual-review` | Leave in Failed for human relevance review; the model returned structurally valid but semantically insufficient enrichment. |
| `Failed/Project Milestone Checkpoint2026-07-11T19_35_09+07_00__8d09f292e828.md` | chatgpt | 6357 | semantic_validation | yes | `manual-review` | Leave in Failed for human relevance review; the model returned structurally valid but semantically insufficient enrichment. |
| `Failed/Real Aussie Expat Life - 2026-07-09 12.46__0f588d87858f.md` | markdown | 62509 | model_final_output_json_parse | no | `model-json-failure` | Keep in Failed; retry only after the captured truncated model final-output issue is addressed. |
| `Failed/ROCm Installation Issue2026-07-11T12_17_48+07_00__48903e2930c6.md` | chatgpt | 52970 | semantic_validation | yes | `manual-review` | Leave in Failed for human relevance review; the model returned structurally valid but semantically insufficient enrichment. |
| `Failed/Ryzen Strix Halo Availability2026-07-18T12_40_24+07_00__6f2ea56da6df.md` | chatgpt | 7847 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/Solar Cost-Benefit Analysis2026-07-11T15_03_35+07_00__8c94bd479ee5.md` | chatgpt | 16403 | semantic_validation | yes | `manual-review` | Leave in Failed for human relevance review; the model returned structurally valid but semantically insufficient enrichment. |
| `Failed/Speechify technical support summary2026-07-12T09_17_19+07_00__94f12e641e6a.md` | web | 1540 | semantic_validation | yes | `manual-review` | Leave in Failed for human relevance review; the model returned structurally valid but semantically insufficient enrichment. |
| `Failed/Sukhothai Trip.md` | youtube | 35207 | semantic_validation | yes | `manual-review` | Leave in Failed for human relevance review; the model returned structurally valid but semantically insufficient enrichment. |
| `Failed/Swanpass — Nightlife Spots & Entertainment Map in Thailand, Vietnam,2026-07-17T10_40_30+07_00[[Swanpass]]__paign=amzRef.md` | web | 20744 | controlled_domain_validation | yes | `pipeline-defect` | Retry after enforcing the controlled domain enum in the JSON schema; preserve the original source unchanged. |
| `Failed/Thailand Administrative Reforms2026-07-18T12_07_10+07_00__dd149c642f38.md` | chatgpt | 1941 | semantic_validation | yes | `manual-review` | Leave in Failed for human relevance review; the model returned structurally valid but semantically insufficient enrichment. |
| `Failed/Thailand Medical Hub Story2026-07-11T15_04_10+07_00__5bdd2ffd9807.md` | chatgpt | 29944 | controlled_domain_validation | yes | `pipeline-defect` | Retry after enforcing the controlled domain enum in the JSON schema; preserve the original source unchanged. |
| `Failed/The Patreon Tribe - Ambient Track Artwork Ideas2026-07-14T12_34_02+07_00__ef5ce6d3802c.md` | chatgpt | 13612 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/The Patreon Tribe - IFTTT Build Optimization2026-07-14T12_32_28+07_00__e32065ff33a3.md` | chatgpt | 13717 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/The Rabbit Hole - AI in Daily Life2026-07-13T22_43_05+07_00__cfc7d380c450.md` | chatgpt | 7306 | controlled_domain_validation | yes | `pipeline-defect` | Retry after enforcing the controlled domain enum in the JSON schema; preserve the original source unchanged. |
| `Failed/The Rabbit Hole - Fake Earth Captions2026-07-13T22_52_31+07_00__6b7a069be845.md` | chatgpt | 5324 | model_final_output_json_parse | no | `model-json-failure` | Keep in Failed; retry only after the captured truncated model final-output issue is addressed. |
| `Failed/The Rabbit Hole - Illuminatus Trilogy Summary2026-07-12T23_33_08+07_00__1ae0bc4afa14.md` | chatgpt | 8663 | model_final_output_json_parse | no | `model-json-failure` | Keep in Failed; retry only after the captured truncated model final-output issue is addressed. |
| `Failed/The Truth about Thai Hospitals - 2026-07-09 12.45__e0861522596f.md` | markdown | 23921 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/Tungsten Supply and Demand2026-07-11T12_15_23+07_00__6b6c8403451e.md` | chatgpt | 4216 | controlled_domain_validation | yes | `pipeline-defect` | Retry after enforcing the controlled domain enum in the JSON schema; preserve the original source unchanged. |
| `Failed/Typical challenges living in Thailand2026-07-12T09_18_42+07_00__deaf9a661802.md` | web | 3538 | semantic_validation | yes | `manual-review` | Leave in Failed for human relevance review; the model returned structurally valid but semantically insufficient enrichment. |
| `Failed/untitled__b54ca9b3557a.md` | markdown | 70 | semantic_validation | yes | `obsolete` | Archive as an empty/metadata-only intake item; do not retry. |
| `Failed/Video Map Pin Strategy2026-07-18T12_39_58+07_00__08e10c9604f2.md` | chatgpt | 11838 | semantic_validation | yes | `manual-review` | Leave in Failed for human relevance review; the model returned structurally valid but semantically insufficient enrichment. |
| `Failed/What does it do exactly_2026-07-12T11_50_42+07_00.md` | web | 2208 | controlled_domain_validation | yes | `pipeline-defect` | Retry after enforcing the controlled domain enum in the JSON schema; preserve the original source unchanged. |
| `Failed/Window into the World - Structural Change Filter2026-07-11T19_42_31+07_00__cca6f761db71.md` | chatgpt | 18278 | controlled_domain_validation | yes | `pipeline-defect` | Retry after enforcing the controlled domain enum in the JSON schema; preserve the original source unchanged. |
| `Failed/YouTube Archive Refresh - 5 Things I Love About Bangkok2026-07-12T11_37_18+07_00__66faefb79174.md` | chatgpt | 10010 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/YouTube Archive Refresh - Archive Refresh Update2026-07-12T11_36_07+07_00__4d745eba576b.md` | chatgpt | 35260 | model_final_output_json_parse | no | `model-json-failure` | Keep in Failed; retry only after the captured truncated model final-output issue is addressed. |
| `Failed/YouTube Archive Refresh - Fixing Thailand Water Supply2026-07-12T11_35_45+07_00__c557d4daf6c7.md` | chatgpt | 9166 | model_final_output_json_parse | no | `model-json-failure` | Keep in Failed; retry only after the captured truncated model final-output issue is addressed. |
| `Failed/YouTube Archive Refresh - Ladyboy Dating in Thailand2026-07-12T11_38_50+07_00__9ae51e1bbc03.md` | chatgpt | 33199 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/YouTube Archive Refresh - Thailand Day Gone Wrong2026-07-12T11_38_05+07_00__a4ee8b605cb8.md` | chatgpt | 10345 | model_final_output_json_parse | no | `model-json-failure` | Keep in Failed; retry only after the captured truncated model final-output issue is addressed. |
| `Failed/YouTube Archive Refresh - Thailand Plans Gone Awry2026-07-12T11_39_04+07_00__e4a35524ae22.md` | chatgpt | 27987 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/YouTube Archive Refresh - Thailand Video Titles Refresh2026-07-12T11_37_35+07_00__59f505a8e826.md` | chatgpt | 26870 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/YouTube Search and Ranking2026-07-10T19_46_48+07_00__b49edf5bd759.md` | chatgpt | 17473 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/10 Self-Hosted AI Tools That KILL Your Entire SaaS Stack2026-07-21T10_54_48+07_002026-07-01T07_30_31-07_00[[The Stack]].md` | youtube | 20812 | model_final_output_json_parse | no | `model-json-failure` | Keep in Failed; retry only after the captured truncated model final-output issue is addressed. |
| `Failed/AI Integration Review2026-07-11T17_38_02+07_00__14f2b7010155.md` | chatgpt | 3373 | controlled_domain_validation | yes | `pipeline-defect` | Retry after enforcing the controlled domain enum in the JSON schema; preserve the original source unchanged. |
| `Failed/Asian Arowana Overview2026-07-10T19_52_29+07_00__db350795a125.md` | chatgpt | 4523 | model_final_output_json_parse | no | `model-json-failure` | Keep in Failed; retry only after the captured truncated model final-output issue is addressed. |
| `Failed/🇹🇭 Thai Visa & Immigration G... • Open WebUI2026-07-12T22_57_12+07_00__e19fafc425b7.md` | web | 4052 | semantic_validation | yes | `manual-review` | Leave in Failed for human relevance review; the model returned structurally valid but semantically insufficient enrichment. |

The JSON companion contains hashes, timestamps, exact failure messages, capture references, and response diagnostics for every row.
