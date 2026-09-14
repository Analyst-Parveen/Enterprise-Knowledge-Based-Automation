"""Model registry: logical roles -> concrete Bedrock model IDs.

Business logic asks for a ROLE ("chat.primary", "vision", "embedding"), never a
model ID. Swapping a model is a config change, never a code change.

FACTS THAT CONSTRAIN THIS FILE (see .claude/rules/ai-model-usage.md section 3):
  * GPT-4 is NOT on Bedrock. Proprietary OpenAI models live on the OpenAI API
    and Azure OpenAI.
  * Amazon Nova models CANNOT be invoked by bare model ID. They require an
    inference profile, which is the same ID with a region-group prefix:
    `us.`, `eu.`, `apac.` or `global.`. Calling `amazon.nova-lite-v1:0`
    directly returns ValidationException; `us.amazon.nova-lite-v1:0` works.
  * Amazon Titan embeddings are the opposite - direct invoke, NO prefix.
  * Available models differ per region. Always verify before depending on one:
        aws bedrock list-inference-profiles --region $AWS_REGION
        aws bedrock list-foundation-models  --region $AWS_REGION
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.core.config import settings


class ModelRole(str, Enum):
    CHAT_PRIMARY = "chat.primary"
    CHAT_FALLBACK = "chat.fallback"
    VISION = "vision"
    EMBEDDING = "embedding"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    model_id: str
    role: ModelRole
    supports_vision: bool
    # USD per 1M tokens. Verify against current Bedrock pricing for your region -
    # these drive estimated_cost, not billing.
    input_cost_per_1m: float
    output_cost_per_1m: float
    dimension: int | None = None


# Inference-profile prefixes. An ID may arrive as `us.amazon.nova-lite-v1:0`,
# so the prefix is stripped before any lookup below - otherwise pricing silently
# falls through to the default and vision routing misreads the model family.
_PROFILE_PREFIXES = ("us.", "eu.", "apac.", "global.")

# Pricing is approximate and region-dependent. Treat estimated_cost as an
# indicator for budget tracking, not an invoice.
_PRICING: dict[str, tuple[float, float]] = {
    "amazon.nova-micro-v1:0": (0.035, 0.14),
    "amazon.nova-lite-v1:0": (0.06, 0.24),
    "amazon.nova-2-lite-v1:0": (0.06, 0.24),
    "amazon.nova-pro-v1:0": (0.80, 3.20),
    "amazon.nova-premier-v1:0": (2.50, 12.50),
    "amazon.titan-embed-text-v2:0": (0.02, 0.0),
    "cohere.embed-v4:0": (0.12, 0.0),
    "embed-multilingual-v3.0": (0.10, 0.0),
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "openai/gpt-oss-20b": (0.10, 0.50),
    "openai.gpt-oss-20b-1:0": (0.07, 0.30),
    "openai.gpt-oss-120b-1:0": (0.15, 0.60),
}

# Model families that cannot accept image input. Checked against the base ID.
_TEXT_ONLY_PREFIXES = (
    "openai.gpt-oss",
    "amazon.titan-embed",
    "amazon.nova-micro",
    "cohere.embed",
)


def base_model_id(model_id: str) -> str:
    """Strip an inference-profile prefix, leaving the underlying model ID."""
    for prefix in _PROFILE_PREFIXES:
        if model_id.startswith(prefix):
            return model_id[len(prefix) :]
    return model_id


def _pricing(model_id: str) -> tuple[float, float]:
    # Conservative default: over-estimating spend is the safe direction when
    # the whole project runs under a $20 ceiling.
    return _PRICING.get(base_model_id(model_id), (0.10, 0.40))


def _supports_vision(model_id: str) -> bool:
    return not base_model_id(model_id).startswith(_TEXT_ONLY_PREFIXES)


def resolve(role: ModelRole) -> ModelSpec:
    """Return the concrete model configured for a logical role."""
    if role is ModelRole.CHAT_PRIMARY:
        if settings.llm_provider == "groq":
            model_id = settings.effective_chat_model_id
        elif settings.llm_provider == "bedrock" and settings.llm_model:
            model_id = settings.llm_model
        else:
            model_id = settings.bedrock_chat_primary_model_id
    elif role is ModelRole.CHAT_FALLBACK:
        model_id = settings.bedrock_chat_fallback_model_id
    elif role is ModelRole.VISION:
        model_id = settings.bedrock_vision_model_id
    else:
        model_id = (
            settings.cohere_embed_model
            if settings.embed_provider == "cohere"
            else settings.bedrock_embedding_model_id
        )

    in_cost, out_cost = _pricing(model_id)
    return ModelSpec(
        model_id=model_id,
        role=role,
        supports_vision=_supports_vision(model_id),
        input_cost_per_1m=in_cost,
        output_cost_per_1m=out_cost,
        dimension=settings.embedding_dimension if role is ModelRole.EMBEDDING else None,
    )


def estimate_cost(spec: ModelSpec, input_tokens: int, output_tokens: int) -> float:
    return round(
        (input_tokens / 1_000_000) * spec.input_cost_per_1m
        + (output_tokens / 1_000_000) * spec.output_cost_per_1m,
        8,
    )


def validate_vision_routing(spec: ModelSpec) -> None:
    """Guard against sending an image to a text-only model."""
    if not spec.supports_vision:
        raise ValueError(
            f"model {spec.model_id} is text-only and cannot accept image input; "
            "route vision work to BEDROCK_VISION_MODEL_ID"
        )
