# Rule: AI and Model Usage

## 1. Platform — Bedrock only

**Amazon Bedrock provides both the LLM and the embeddings.** No external AI
provider is required, configured, or permitted by default.

- **Amazon Transcribe** handles all speech-to-text (audio files, video audio
  tracks, and voice input).
- There is no OpenAI API path, no Azure OpenAI path, and no Vertex AI path in this
  project.

## 2. Model registry

A single registry module maps logical roles to concrete Bedrock model IDs read
from configuration:

| Role | Default model ID | Constraint |
|---|---|---|
| `chat.primary` | `openai.gpt-oss-20b-1:0` | Text-only; limited regions |
| `chat.fallback` | `amazon.nova-lite-v1:0` | Cheaper, wider availability |
| `vision` | `amazon.nova-lite-v1:0` | Image + video understanding |
| `embedding` | `amazon.titan-embed-text-v2:0` | 1024 dimensions |

## 3. Facts that constrain model choice

State these correctly; do not repeat the common mistakes:

- **GPT-4 is not available on Amazon Bedrock.** Proprietary OpenAI models live on
  the OpenAI API and Azure OpenAI. Bedrock hosts only OpenAI's *open-weight*
  `gpt-oss` family.
- **`openai.gpt-oss-*` models are text-only.** Never route an image, diagram,
  table screenshot, or video frame to them. Vision goes to Nova Lite.
- **OpenAI embedding models are not on Bedrock.** Embeddings use Titan v2.
- **Gemini 2.5 Flash is not on Bedrock** — it is a Google Vertex AI model. It is
  not used in this project, and it would never be an embedding model regardless.

## 4. Selection rules

- Model IDs are **configuration**, never hardcoded in business logic. Swapping a
  model must not require a code change.
- Verify regional availability before depending on a model:
  `aws bedrock list-foundation-models --region $AWS_REGION`
- If the primary chat model is unavailable in the region, fall back to
  `chat.fallback`. Never silently fall back to a different model *class* — a
  fallback is logged, reported in `model_used`, and surfaced in metrics.
- **No model is ever used for embeddings except the configured embedding model.**

## 3. Model routing

Routing is explicit and explainable. The router chooses based on:

- Query complexity and length
- Whether multimodal input is involved
- Required context window
- Cost ceiling and current usage
- Configured tenant tier

The chosen model is always reported in the response as `model_used`. Routing
decisions are traced to LangSmith with the correlation ID.

## 4. Embeddings

- One embedding model per Qdrant collection. Vector dimension is fixed at
  collection creation.
- Changing the embedding model requires a new collection and a re-index migration.
  Never mix vectors from different embedding models in one collection.
- The embedding model ID and dimension are recorded in configuration and in
  document metadata so re-index needs are detectable.

## 5. Prompts

- System prompts are versioned in source and released through the
  `prompt_releases` table. Prompt changes are reviewable diffs, not ad-hoc edits.
- System prompts are never constructed by concatenating user or document text.
  See [guardrails.md](guardrails.md).
- Each prompt states explicitly that retrieved content is reference data, not
  instructions, and that answers must be grounded in provided context with
  citations.

## 6. Cost and token discipline

**The spend ceiling is $20 total, not $140.** Every call is accounted for:

- Record `input_tokens`, `output_tokens`, `estimated_cost`, `latency_ms`, and
  `model_used` for every AI call into `request_usage`.
- Enforce a maximum output token cap per request and a per-user daily cost ceiling.
- **Semantic cache before inference**, always. Report `cache_hit` in the response.
  A cache hit still runs authentication, tenant filtering, and the output
  guardrail.
- Prefer the cheapest model that satisfies the quality bar. Do not default to the
  largest model.
- Batch embedding calls during ingestion instead of one call per chunk.
- No background or scheduled AI calls that a user did not trigger, unless
  explicitly part of an ingestion job.

## 7. Agents (LangGraph)

- Graphs are explicit and bounded: a maximum step count and a wall-clock timeout
  on every workflow. No unbounded loops.
- Tenant context is threaded through graph state from the entry node.
- Every tool is typed with Pydantic schemas, validates its inputs, and returns
  validated output.
- Agent runs are traced to LangSmith with the same correlation ID as the HTTP
  request, and their token/cost usage is accumulated into `request_usage`.
- A failing node degrades gracefully with a partial, clearly-labelled result
  rather than a silent empty answer.

## 8. Determinism and evaluation

- Temperature and sampling parameters are configuration, defaulted low for
  extraction and comparison workloads.
- Any change to a model, prompt, chunking strategy, embedding model, or retrieval
  parameter requires an evaluation run (see [testing.md](testing.md)) and a
  report in `docs/reports/`.

## 9. Prohibitions

- No AI provider credentials in code, environment files committed to git, or
  container images. See [secrets-management.md](secrets-management.md).
- No sending raw documents to a provider outside the configured, approved path.
- No disabling guardrails to "test the model quickly".
