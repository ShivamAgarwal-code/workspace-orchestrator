"""Real Anthropic-backed LLMProvider."""
import json

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.llm.base import ChatMessage, LLMProvider


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str):
        import anthropic

        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._retryable = (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError)

    def _retry(self):
        return retry(
            retry=retry_if_exception_type(self._retryable),
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
            reraise=True,
        )

    async def complete(self, system: str, messages: list[ChatMessage], max_tokens: int = 1024) -> str:
        @self._retry()
        async def _call():
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": m.role, "content": m.content} for m in messages],
            )
            return "".join(block.text for block in resp.content if block.type == "text")

        return await _call()

    async def structured_complete(
        self,
        system: str,
        messages: list[ChatMessage],
        schema: dict,
        tool_name: str = "emit_result",
        max_tokens: int = 1024,
    ) -> dict:
        @self._retry()
        async def _call():
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                tools=[{"name": tool_name, "description": "Emit the structured result.", "input_schema": schema}],
                tool_choice={"type": "tool", "name": tool_name},
            )
            for block in resp.content:
                if block.type == "tool_use" and block.name == tool_name:
                    return block.input
            # Should not happen with tool_choice forced, but degrade gracefully.
            text = "".join(b.text for b in resp.content if b.type == "text")
            return json.loads(text)

        return await _call()
