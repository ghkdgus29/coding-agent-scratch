"""provider 레지스트리: "provider:model" 문자열 → LLM 객체.

    anthropic:claude-opus-5-5
    openai:gpt-5
    openrouter:<vendor>/<model>
    ollama:qwen3-coder               (로컬, 키 불필요)
"""


from .anthropic import AnthropicLLM
from .base import LLM, APIError, LLMResponse, Message, ToolCall, Usage
from .fake import FakeLLM
from .openai import OpenAILLM

__all__ = ["LLM", "APIError", "LLMResponse", "Message", "ToolCall", "Usage", "FakeLLM", "get_llm"]

DEFAULT_MODEL = "anthropic:claude-opus-5-5"


def get_llm(spec: str = DEFAULT_MODEL, effort: str | None = "high", base_url: str | None = None,
            log=print) -> LLM:
    provider, _, model = spec.partition(":")
    if provider == "anthropic":
        return AnthropicLLM(model or "claude-opus-5-5", effort=effort, log=log)
    if not model:
        raise SystemExit(f"'{provider}:<model>' 형태로 모델을 지정하세요.")
    if provider == "openai":
        return OpenAILLM(model, base_url or "https://api.openai.com/v1", "OPENAI_API_KEY", log=log)
    if provider == "openrouter":
        return OpenAILLM(model, base_url or "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", log=log)
    if provider == "ollama":
        return OpenAILLM(model, base_url or "http://localhost:11434/v1", None, log=log)
    raise SystemExit(f"알 수 없는 provider: {provider} (anthropic | openai | openrouter | ollama)")
