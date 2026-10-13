"""v08: 토큰 → 비용($) 계산.

가격은 $/1M 토큰: (입력, 출력, 캐시 읽기, 캐시 쓰기(5분))
- Anthropic 1st-party API 가격 (2026-09 기준). 최신 가격은 공식 문서를 확인하자.
- ~ 표시: 캐시 가격을 일반 규칙(읽기 0.1×, 쓰기 1.25×)으로 추정한 값.
- 표에 없는 모델(OpenAI 등)은 --price 로 직접 넣거나 비용 미상($?)으로 표시한다.
"""

from llm import Usage

Price = tuple[float, float, float, float]

PRICES: dict[str, Price] = {
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5),
    "claude-fable-5": (10.0, 50.0, 1.0, 12.5),       # ~
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-opus-5": (5.0, 25.0, 0.50, 6.25),        # ~
    "claude-opus-4-8": (5.0, 25.0, 0.50, 6.25),      # ~
    "claude-opus-4-7": (5.0, 25.0, 0.50, 6.25),      # ~
    "claude-opus-4-6": (5.0, 25.0, 0.50, 6.25),      # ~
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),     # ~ (쓰기)
    "claude-sonnet-5": (2.0, 10.0, 0.20, 2.5),       # ~
    "claude-sonnet-4-6": (3.0, 15.0, 0.30, 3.75),    # ~
    "claude-haiku-4-5": (1.0, 5.0, 0.10, 1.25),      # ~
}


def price_for(model: str, override: Price | None = None) -> Price | None:
    if override:
        return override
    if model in PRICES:
        return PRICES[model]
    # 날짜가 붙은 ID (claude-haiku-4-5-20251001) 또는 OpenRouter 슬러그 (anthropic/claude-...)
    for name, p in PRICES.items():
        if name in model:
            return p
    return None


def cost_of(usage: Usage, model: str, override: Price | None = None) -> float | None:
    p = price_for(model, override)
    if p is None:
        return None
    inp, out, cache_read, cache_write = p
    return (usage.input_tokens * inp + usage.output_tokens * out + usage.cache_read_tokens * cache_read
            + usage.cache_write_tokens * cache_write) / 1_000_000


def parse_price(text: str) -> Price:
    """'2,10' 또는 '2,10,0.2,2.5' → Price"""
    parts = [float(x) for x in text.split(",")]
    if len(parts) == 2:
        parts += [parts[0] * 0.1, parts[0] * 1.25]
    if len(parts) != 4:
        raise ValueError("--price 형식: 입력,출력[,캐시읽기,캐시쓰기] ($/1M 토큰)")
    return tuple(parts)  # type: ignore[return-value]
