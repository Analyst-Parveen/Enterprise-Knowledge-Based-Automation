"""Model registry: logical roles -> concrete Bedrock model IDs.

Business logic asks for a ROLE ("chat.primary", "vision", "embedding"), never a
model ID. Swapping a model is a config change, never a code change.

FACTS THAT CONSTRAIN THIS FILE (see .claude/rules/ai-model-usage.md section 3):
  * GPT-4 is NOT on Bedrock. Only OpenAI's open-weight gpt-oss family is.
  * gpt-oss models are TEXT-ONLY - never route an image to one.
  * OpenAI embedding models are NOT on Bedrock. Embeddings use Titan v2.
  * Gemini 2.5 Flash is NOT on Bedrock (it is Google Vertex AI). Unused here.
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


# Pricing is approximate and region-dependent. Treat estimated_cost as an
# indicator for budget tracking, not an invoice.
_PRICING: dict[str, tuple[float, float]] = {
    "openai.gpt-oss-20b-1:0": (0.07, 0.30),
    "openai.gpt-oss-120b-1:0": (0.15, 0.60),
    "amazon.nova-micro-v1:0": (0.035, 0.14),
    "amazon.nova-lite-v1:0": (0.06, 0.24),
    "amazon.nova-pro-v1:0": (0.80, 3.20),
    "amazon.titan-embed-text-v2:0": (0.02, 0.0),
}

# Model families that cannot accept image input.
_TEXT_ONLY_PREFIXES = ("openai.gpt-oss", "amazon.titan-embed", "amazon.nova-micro")


def _pricing(model_id: str) -> tuple[float, float]:
    return _PRICING.get(model_id, (0.10, 0.40))  # conservative default


def _supports_vision(model_id: str) -> bool:
    return not model_id.startswith(_TEXT_ONLY_PREFIXES)


def resolve(role: ModelRole) -> ModelSpec:
    """Return the concrete model configured for a logical role."""
    model_id = {
        ModelRole.CHAT_PRIMARY: settings.bedrock_chat_primary_model_id,
        ModelRole.CHAT_FALLBACK: settings.bedrock_chat_fallback_model_id,
        ModelRole.VISION: settings.bedrock_vision_model_id,
        ModelRole.EMBEDDING: settings.bedrock_embedding_model_id,
    }[role]

    in_cost, out_cost = _pricing(model_id)
    return ModelSpec(
        model_id=model_id,
        role=role,
        supports_vision=_supports_vision(model_id),
        input_cost_per_1m=in_cost,
        output_cost_per_1m=out_cost,
        dimension=settings.bedrock_embedding_dimension if role is ModelRole.EMBEDDING else None,
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
