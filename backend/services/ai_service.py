import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Iterable

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class AIProviderError(RuntimeError):
    pass


class AIRetryableError(AIProviderError):
    pass


class AIConfigurationError(AIProviderError):
    pass


class AIRequestError(AIProviderError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        provider: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.provider = provider


@dataclass(frozen=True)
class AIMessage:
    role: str
    content: str


@dataclass(frozen=True)
class AIConfig:
    provider: str
    model: str
    timeout: int
    temperature: float | None
    base_url: str
    api_key: str | None = None
    api_mode: str = "chat"
    max_retries: int = 2


def _env_float(name: str) -> float | None:
    value = os.getenv(name)
    if value in (None, ""):
        return None
    return float(value)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _first_configured_provider() -> str:
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("GROQ_API_KEY"):
        return "groq"
    return "ollama"


def load_ai_config(provider: str | None = None) -> AIConfig:
    active = (provider or os.getenv("AI_PROVIDER") or _first_configured_provider()).lower()
    timeout = _env_int("AI_TIMEOUT_SECONDS", 45)
    max_retries = _env_int("AI_MAX_RETRIES", 2)
    temperature = _env_float("AI_TEMPERATURE")

    if active == "openai":
        return AIConfig(
            provider="openai",
            model=os.getenv("OPENAI_MODEL") or os.getenv("AI_MODEL") or "gpt-5.5",
            timeout=timeout,
            temperature=temperature,
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            api_key=os.getenv("OPENAI_API_KEY"),
            api_mode=os.getenv("OPENAI_API_MODE", "responses").lower(),
            max_retries=max_retries,
        )

    if active == "groq":
        return AIConfig(
            provider="groq",
            model=os.getenv("GROQ_MODEL") or os.getenv("AI_MODEL") or "llama-3.3-70b-versatile",
            timeout=timeout,
            temperature=temperature if temperature is not None else 0.2,
            base_url=os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/"),
            api_key=os.getenv("GROQ_API_KEY"),
            api_mode="chat",
            max_retries=max_retries,
        )

    if active == "ollama":
        return AIConfig(
            provider="ollama",
            model=os.getenv("OLLAMA_MODEL") or os.getenv("AI_MODEL") or "llama3.1",
            timeout=timeout,
            temperature=temperature if temperature is not None else 0.2,
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
            api_mode="chat",
            max_retries=max_retries,
        )

    raise AIConfigurationError(f"Unknown AI provider: {active}")


def validate_ai_config(config: AIConfig | None = None) -> None:
    cfg = config or load_ai_config()
    if cfg.provider in {"openai", "groq"}:
        key = (cfg.api_key or "").strip()
        if not key:
            raise AIConfigurationError(f"{cfg.provider.upper()} API key is not configured")
        if len(key) < 20 or key.lower() in {"changeme", "your-api-key", "your_openai_api_key"}:
            raise AIConfigurationError(f"{cfg.provider.upper()} API key is invalid")
    if not cfg.model.strip():
        raise AIConfigurationError("AI model is not configured")


class BaseAIProvider:
    def __init__(self, config: AIConfig):
        self.config = config
        validate_ai_config(config)

    def generate(self, messages: list[AIMessage], system: str) -> str:
        raise NotImplementedError

    def _post(self, url: str, payload: dict, headers: dict | None = None) -> dict:
        last_error: AIProviderError | None = None
        for attempt in range(self.config.max_retries + 1):
            retry_after = 0.0
            try:
                response = requests.post(
                    url,
                    json=payload,
                    headers=headers or {},
                    timeout=self.config.timeout,
                )
                if response.status_code < 400:
                    return response.json()
                error_info = _extract_error_info(response)
                if response.status_code in {401, 403}:
                    logger.error(
                        "AI authentication failed provider=%s status=%s request_id=%s error_code=%s",
                        self.config.provider,
                        response.status_code,
                        error_info["request_id"],
                        error_info["code"],
                    )
                    raise AIConfigurationError(f"{self.config.provider} authentication failed")
                if response.status_code == 404:
                    logger.error(
                        "AI model or endpoint not found provider=%s model=%s request_id=%s error_code=%s message=%s",
                        self.config.provider,
                        self.config.model,
                        error_info["request_id"],
                        error_info["code"],
                        error_info["message"],
                    )
                    raise AIConfigurationError(f"{self.config.provider} model or endpoint was not found")
                if response.status_code == 429 or response.status_code >= 500:
                    retry_after = _retry_after_seconds(response)
                    logger.warning(
                        "AI provider returned retryable error provider=%s model=%s status=%s request_id=%s error_code=%s message=%s",
                        self.config.provider,
                        self.config.model,
                        response.status_code,
                        error_info["request_id"],
                        error_info["code"],
                        error_info["message"],
                    )
                    last_error = AIRequestError(
                        f"{self.config.provider} returned retryable status {response.status_code}",
                        status_code=response.status_code,
                        retryable=True,
                        provider=self.config.provider,
                    )
                else:
                    logger.error(
                        "AI provider returned non-retryable error provider=%s model=%s status=%s request_id=%s error_code=%s message=%s",
                        self.config.provider,
                        self.config.model,
                        response.status_code,
                        error_info["request_id"],
                        error_info["code"],
                        error_info["message"],
                    )
                    raise AIRequestError(
                        f"{self.config.provider} returned status {response.status_code}",
                        status_code=response.status_code,
                        retryable=False,
                        provider=self.config.provider,
                    )
            except ValueError as exc:
                raise AIProviderError(f"{self.config.provider} returned invalid JSON") from exc
            except requests.RequestException:
                last_error = AIRetryableError(f"{self.config.provider} request failed")
                logger.warning(
                    "AI request transport error provider=%s attempt=%s",
                    self.config.provider,
                    attempt + 1,
                    exc_info=True,
                )

            if attempt < self.config.max_retries:
                delay = retry_after or (min(6.0, 0.75 * (2 ** attempt)) + random.uniform(0, 0.25))
                logger.warning(
                    "AI request retry provider=%s model=%s attempt=%s delay=%.2fs",
                    self.config.provider,
                    self.config.model,
                    attempt + 1,
                    delay,
                )
                time.sleep(delay)

        raise last_error or AIProviderError(f"{self.config.provider} request failed")


class OpenAIProvider(BaseAIProvider):
    def generate(self, messages: list[AIMessage], system: str) -> str:
        if self.config.api_mode == "chat":
            return OpenAICompatibleChatProvider(self.config).generate(messages, system)

        payload = {
            "model": self.config.model,
            "instructions": system,
            "input": [{"role": item.role, "content": item.content} for item in messages],
        }
        if self.config.temperature is not None:
            payload["temperature"] = self.config.temperature

        data = self._post(
            f"{self.config.base_url}/responses",
            payload,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
        )
        return _parse_responses_text(data)


class OpenAICompatibleChatProvider(BaseAIProvider):
    def generate(self, messages: list[AIMessage], system: str) -> str:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                *[{"role": item.role, "content": item.content} for item in messages],
            ],
        }
        if self.config.temperature is not None:
            payload["temperature"] = self.config.temperature

        data = self._post(
            f"{self.config.base_url}/chat/completions",
            payload,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, AttributeError) as exc:
            raise AIProviderError(f"{self.config.provider} response did not include text") from exc


class GroqProvider(OpenAICompatibleChatProvider):
    pass


class OllamaProvider(BaseAIProvider):
    def generate(self, messages: list[AIMessage], system: str) -> str:
        payload = {
            "model": self.config.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                *[{"role": item.role, "content": item.content} for item in messages],
            ],
            "options": {},
        }
        if self.config.temperature is not None:
            payload["options"]["temperature"] = self.config.temperature

        data = self._post(f"{self.config.base_url}/api/chat", payload)
        try:
            return data["message"]["content"].strip()
        except (KeyError, AttributeError) as exc:
            raise AIProviderError("ollama response did not include text") from exc


def create_provider(config: AIConfig | None = None) -> BaseAIProvider:
    cfg = config or load_ai_config()
    if cfg.provider == "openai":
        return OpenAIProvider(cfg)
    if cfg.provider == "groq":
        return GroqProvider(cfg)
    if cfg.provider == "ollama":
        return OllamaProvider(cfg)
    raise AIConfigurationError(f"Unknown AI provider: {cfg.provider}")


def generate_ai_response(messages: list[AIMessage], system: str) -> str:
    config = load_ai_config()
    logger.info("AI request provider=%s model=%s mode=%s", config.provider, config.model, config.api_mode)
    try:
        return create_provider(config).generate(messages, system)
    except AIConfigurationError:
        logger.exception("AI configuration error provider=%s model=%s", config.provider, config.model)
        raise
    except AIProviderError:
        fallback = _fallback_provider_name(config.provider)
        if fallback and fallback != config.provider:
            fallback_config = load_ai_config(fallback)
            try:
                logger.warning(
                    "AI provider failed; attempting fallback provider=%s model=%s",
                    fallback_config.provider,
                    fallback_config.model,
                )
                return create_provider(fallback_config).generate(messages, system)
            except AIProviderError:
                logger.exception(
                    "AI fallback provider failed provider=%s model=%s",
                    fallback_config.provider,
                    fallback_config.model,
                )
        logger.exception("AI provider request failed provider=%s model=%s", config.provider, config.model)
        raise


def ask_ai(q: str, system: str, history: Iterable[dict] | None = None) -> str:
    messages = [
        AIMessage(role=item.get("role", "user"), content=item.get("content") or item.get("text", ""))
        for item in (history or [])
        if item.get("role") in {"user", "assistant"} and (item.get("content") or item.get("text"))
    ]
    messages.append(AIMessage(role="user", content=q))
    return generate_ai_response(messages, system)


def _parse_responses_text(data: dict) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    parts: list[str] = []
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                parts.append(text)
            elif content.get("type") == "output_text" and isinstance(content.get("text"), str):
                parts.append(content["text"])

    if parts:
        return "\n".join(parts).strip()

    raise AIProviderError("openai response did not include text")


def _extract_error_info(response: requests.Response) -> dict[str, str]:
    request_id = response.headers.get("x-request-id") or response.headers.get("x-groq-request-id") or ""
    code = ""
    message = response.text[:240]
    try:
        data = response.json()
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            code = str(error.get("code") or error.get("type") or "")
            message = str(error.get("message") or message)[:240]
    except ValueError:
        pass
    return {"request_id": request_id, "code": code, "message": message}


def _retry_after_seconds(response: requests.Response) -> float:
    value = response.headers.get("retry-after")
    if not value:
        return 0.0
    try:
        return min(30.0, max(0.0, float(value)))
    except ValueError:
        return 0.0


def _fallback_provider_name(active_provider: str) -> str:
    configured = os.getenv("AI_FALLBACK_PROVIDER", "").strip().lower()
    if configured:
        return configured

    if _truthy(os.getenv("AI_AUTO_FALLBACK", "true")):
        if active_provider != "groq" and os.getenv("GROQ_API_KEY"):
            return "groq"
        if active_provider != "openai" and os.getenv("OPENAI_API_KEY"):
            return "openai"
        if active_provider != "ollama" and _truthy(os.getenv("OLLAMA_AUTO_FALLBACK", "false")):
            return "ollama"
    return ""


def _truthy(value: str | None) -> bool:
    return (value or "").lower() in {"1", "true", "yes", "on"}
