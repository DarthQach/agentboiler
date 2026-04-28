import logging


logger = logging.getLogger(__name__)

FALLBACK_MODEL = "claude-sonnet-4-6"

RATES = {
    FALLBACK_MODEL: {"input": 3.00, "output": 15.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
    "gpt-4o": {"input": 5.00, "output": 15.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = RATES.get(model)
    if rates is None:
        logger.warning(
            "Unknown model %s for token cost calculation; using %s rates.",
            model,
            FALLBACK_MODEL,
        )
        rates = RATES[FALLBACK_MODEL]

    cost = (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000
    return round(cost, 6)
