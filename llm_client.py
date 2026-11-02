import ssl
import sys
import threading
from typing import TYPE_CHECKING, Any

from langchain_core.callbacks.base import BaseCallbackHandler

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI

def _optional_number(value: Any) -> float | None:
    """A number the user set, or None when the field was left on the model's default."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sampling_params_from_settings(
    data: dict[str, Any] | None,
) -> tuple[float | None, float | None, dict[str, Any] | None, str | None]:
    """(temperature, top_p, extra_body, reasoning_effort) from saved settings.

    None means the user left that setting on "Model default", so it is not sent at all:
    a model never receives a parameter it may not support unless the user chose it.
    Reasoning effort is sent as typed, because models name their levels differently.
    """
    data = data or {}
    extra = dict(data.get("extraParams") or {}) if isinstance(data.get("extraParams"), dict) else {}
    top_k = _optional_number(data.get("topK"))
    if top_k is not None:
        extra["top_k"] = int(top_k)  # not part of the OpenAI API, so it goes in the request body as-is
    effort = str(data.get("reasoningEffort") or "").strip()
    return (
        _optional_number(data.get("temperature")),
        _optional_number(data.get("topP")),
        extra or None,
        effort or None,
    )


# ── LLM call counter ──────────────────────────────────────────────────────────
# Process-wide totals (reported by /api/llm_call_count); each job can also pass its own
# `usage` dict to make_llm so concurrent jobs are counted separately.
_call_count: int = 0
_input_tokens: int = 0
_output_tokens: int = 0
_call_count_lock = threading.Lock()


def new_usage() -> dict[str, int]:
    return {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0}


class _CountingCallback(BaseCallbackHandler):
    def __init__(self, usage: dict[str, int] | None = None) -> None:
        self.usage = usage

    def on_chat_model_start(self, serialized, messages, **kwargs):
        global _call_count
        with _call_count_lock:
            _call_count += 1
            if self.usage is not None:
                self.usage["llm_calls"] = self.usage.get("llm_calls", 0) + 1

    def on_llm_end(self, response, **kwargs):
        global _input_tokens, _output_tokens
        usage = (response.llm_output or {}).get("token_usage") or {}  # some services report no usage
        inp = usage.get("prompt_tokens", 0) or 0
        out = usage.get("completion_tokens", 0) or 0
        with _call_count_lock:
            _input_tokens += inp
            _output_tokens += out
            if self.usage is not None:
                self.usage["input_tokens"] = self.usage.get("input_tokens", 0) + inp
                self.usage["output_tokens"] = self.usage.get("output_tokens", 0) + out


def reset_llm_call_count() -> None:
    global _call_count, _input_tokens, _output_tokens
    with _call_count_lock:
        _call_count = 0
        _input_tokens = 0
        _output_tokens = 0


def get_llm_call_count() -> int:
    with _call_count_lock:
        return _call_count


def get_token_counts() -> tuple[int, int]:
    with _call_count_lock:
        return _input_tokens, _output_tokens


# ── Certificates ──────────────────────────────────────────────────────────────
def tls_verify() -> ssl.SSLContext | bool:
    """Verify the AI service's HTTPS certificate with this computer's certificate store, as a
    browser does, so certificates an organization installs are trusted too. A Linux system
    without a store falls back to the list that ships with Python."""
    if sys.platform.startswith("linux"):
        paths = ssl.get_default_verify_paths()
        if not (paths.cafile or paths.capath):
            return True
    import truststore  # reads the Windows, macOS or Linux certificate store
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


# ── LLM factory ───────────────────────────────────────────────────────────────
LLM_TIMEOUT = 300  # seconds per API call before aborting


def make_llm(
    base_url: str,
    api_key: str | None,
    model: str,
    temperature: float | None = None,
    top_p: float | None = None,
    extra_body: dict[str, Any] | None = None,
    reasoning_effort: str | None = None,
    callbacks: list[BaseCallbackHandler] | None = None,
    usage: dict[str, int] | None = None,
) -> "ChatOpenAI":
    """A chat model for the AI service. Only the sampling settings the user set are sent."""
    # Imported on first use: the OpenAI client takes seconds to load (see agent_runtime.run_agent).
    from langchain_openai import ChatOpenAI
    from openai import DefaultAsyncHttpxClient, DefaultHttpxClient

    extra_kwargs: dict[str, Any] = {}
    if temperature is not None:
        extra_kwargs["temperature"] = temperature
    if top_p is not None:
        extra_kwargs["top_p"] = top_p
    if extra_body:
        extra_kwargs["extra_body"] = extra_body  # provider-specific fields, added to every request as-is
    if reasoning_effort:
        extra_kwargs["reasoning_effort"] = reasoning_effort
    verify = tls_verify()
    if verify is not True:
        extra_kwargs["http_client"] = DefaultHttpxClient(verify=verify)
        extra_kwargs["http_async_client"] = DefaultAsyncHttpxClient(verify=verify)
    return ChatOpenAI(
        base_url=base_url,
        api_key=api_key or "not-needed",  # the client requires a value; local servers ignore it
        model=model,
        timeout=LLM_TIMEOUT,
        callbacks=[_CountingCallback(usage), *(callbacks or [])],
        **extra_kwargs,
    )
