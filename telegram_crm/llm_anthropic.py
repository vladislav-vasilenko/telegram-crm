"""Claude (Anthropic API) backend: one structured-output request per part of the LLM batch."""
from contextlib import asynccontextmanager

from .models import LLMBatchResult

# USD per 1M input / output tokens, used only for the pre-run estimate when no price is set
# in Settings. Claude Haiku 5.5 prices apply to prompts up to 100K tokens (each part is far below).
ANTHROPIC_PRICES = {"claude-haiku-5-5": (0.10, 0.50)}

# Thinking is always on for Claude Haiku 5.5 and its tokens count against max_tokens,
# so leave room above the answer budget (16K keeps non-streaming requests well within timeouts).
MIN_MAX_TOKENS = 16_000


def is_anthropic_model(model: str) -> bool:
    return model.startswith("claude-")


@asynccontextmanager
async def anthropic_backend(api_key: str, model: str):
    """Yields call(system, user, max_tokens, schema) -> parsed schema | None (None = no usable answer)."""
    from anthropic import AsyncAnthropic

    async with AsyncAnthropic(api_key=api_key, timeout=300, max_retries=2) as client:
        async def call(system: str, user: str, max_tokens: int, schema=LLMBatchResult):
            response = await client.messages.parse(
                model=model,
                max_tokens=max(MIN_MAX_TOKENS, max_tokens),
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
            )
            # A refusal (Claude Haiku 5.5 has no server-side fallback) or a cut-off answer is not a
            # complete result for this part; the caller discards the part as a whole.
            if response.stop_reason in ("refusal", "max_tokens"):
                return None
            return response.parsed_output

        yield call
