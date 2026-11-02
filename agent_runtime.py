"""Shared plumbing for every planning agent.

One place owns the retry loop, the step limit, cancellation, AI logging and the
acceptance gate, so the agents cannot drift apart on those rules.
"""
import functools
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from langchain_core.callbacks.base import BaseCallbackHandler

if TYPE_CHECKING:
    from langchain_core.tools import StructuredTool

from app_paths import AI_LOG_DIR
from llm_client import make_llm

# Model turns plus tool turns allowed in one attempt before it is abandoned.
AGENT_RECURSION_LIMIT = 40


class AgentCanceled(Exception):
    """Raised to stop an agent mid-run. Deliberately not a ToolException, so it is never fed back to the model."""


@dataclass
class LLMSettings:
    api_url: str = ""
    model: str = ""
    api_key: str = ""
    temperature: float | None = None
    top_p: float | None = None
    extra_body: dict[str, Any] | None = None
    reasoning_effort: str | None = None


class _CancelBeforeModelCall(BaseCallbackHandler):
    raise_error = True

    def __init__(self, cancel_check: Callable[[], bool] | None) -> None:
        self.cancel_check = cancel_check

    def on_chat_model_start(self, serialized, messages, **kwargs):
        if self.cancel_check and self.cancel_check():
            raise AgentCanceled("Job canceled.")


def agent_tool(fn: Callable[..., str], cancel_check: Callable[[], bool] | None = None) -> "StructuredTool":
    """Wrap fn as a LangChain tool: errors go back to the model, cancellation stops the run."""
    from langchain_core.tools import StructuredTool, ToolException

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> str:
        if cancel_check and cancel_check():
            raise AgentCanceled("Job canceled.")
        try:
            return fn(*args, **kwargs)
        except (AgentCanceled, ToolException):
            raise
        except Exception as exc:
            raise ToolException(str(exc)) from exc

    tool = StructuredTool.from_function(wrapper)
    tool.handle_tool_error = True
    return tool


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    candidates: list[str] = []
    start = -1
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    candidates.append(text[start:index + 1])
                    start = -1

    parse_errors: list[str] = []
    decoder = json.JSONDecoder()
    for candidate in candidates:
        repaired = re.sub(r'(?<=")\s*;\s*(?=")', ", ", candidate)
        try:
            obj, _ = decoder.raw_decode(repaired)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError as exc:
            parse_errors.append(str(exc))

    start = text.find("{")
    if start == -1:
        raise ValueError("AI response did not contain a JSON object.")
    json_text = text[start:]
    try:
        obj, _ = decoder.raw_decode(json_text)
        return obj
    except json.JSONDecodeError:
        end = json_text.rfind("}")
        if end == -1 or end <= 0:
            raise ValueError(parse_errors[-1] if parse_errors else "AI response did not contain valid JSON.")
        repaired = re.sub(r'(?<=")\s*;\s*(?=")', ", ", json_text[: end + 1])
        obj, _ = decoder.raw_decode(repaired)
        return obj


def parse_json_argument(text: str) -> Any:
    """Parse a JSON string a model passed as a tool argument, tolerating code fences and prose."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return extract_json_object(str(text))


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for item in content:
            if isinstance(item, str):
                pieces.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or item.get("output_text")
                if text:
                    pieces.append(str(text))
        return "\n".join(piece for piece in pieces if piece)
    if isinstance(content, dict):
        text = content.get("text") or content.get("content") or content.get("output_text")
        return str(text) if text else ""
    return str(content)


def agent_response_text(response: dict[str, Any]) -> str:
    messages = response.get("messages", []) if isinstance(response, dict) else []
    for message in reversed(messages):
        if getattr(message, "type", "") != "ai" and message.__class__.__name__ != "AIMessage":
            continue
        text = _content_to_text(getattr(message, "content", ""))
        if text.strip():
            return text
    return ""


def summarize_agent_response(response: dict[str, Any]) -> str:
    messages = response.get("messages", []) if isinstance(response, dict) else []
    summary: list[dict[str, Any]] = []
    for message in messages:
        additional = getattr(message, "additional_kwargs", {}) or {}
        response_metadata = getattr(message, "response_metadata", {}) or {}
        tool_calls = getattr(message, "tool_calls", None) or additional.get("tool_calls")
        summary.append({
            "type": message.__class__.__name__,
            "content": _content_to_text(getattr(message, "content", "")),
            "additional_keys": sorted(additional.keys()),
            "additional_kwargs": additional,
            "response_metadata": response_metadata,
            "tool_calls": tool_calls,
        })
    return json.dumps({"messages": summary}, ensure_ascii=False, default=str)


def write_ai_log(
    *,
    input_label: str,
    api_url: str,
    model: str,
    prompt: dict[str, Any],
    raw_output: str,
    parsed_plan: dict[str, Any] | None = None,
    error: str = "",
) -> str:
    try:
        AI_LOG_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", input_label or "input").strip("_") or "input"
        path = AI_LOG_DIR / f"{timestamp}_{safe_label}_{int(time.time() * 1000) % 100000}.json"
        payload = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "input_label": input_label,
            "api_url": api_url,
            "model": model,
            "prompt": prompt,
            "raw_output": raw_output,
            "parsed_plan": parsed_plan,
            "error": error,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return str(path)
    except OSError:
        # Logging must never be the reason an agent run fails.
        return ""


def run_agent(
    *,
    label: str,
    settings: LLMSettings,
    tools: list["StructuredTool"],
    system_prompt: str,
    prompt: dict[str, Any],
    submitted: list[dict[str, Any]],
    accept: Callable[[dict[str, Any]], dict[str, Any]],
    submit_tool_name: str,
    max_attempts: int = 3,
    progress: Callable[[int, str], None] | None = None,
    progress_message: str = "Planning...",
    progress_start: int = 20,
    cancel_check: Callable[[], bool] | None = None,
    usage: dict[str, int] | None = None,
    recursion_limit: int = AGENT_RECURSION_LIMIT,
) -> dict[str, Any]:
    """Run a tool-using agent until its submit tool accepts a plan.

    `accept` is the single acceptance gate. The submit tool calls it, and so does
    the fallback for a model that answers with JSON instead of calling the tool,
    so both paths enforce exactly the same rules.
    """
    # Imported here rather than at the top: they take seconds to load and are only needed
    # once a step runs, so the app window opens without waiting for them.
    from langchain.agents import create_agent
    from langgraph.errors import GraphRecursionError

    llm = make_llm(
        settings.api_url, settings.api_key, settings.model,
        settings.temperature, settings.top_p, settings.extra_body, settings.reasoning_effort,
        callbacks=[_CancelBeforeModelCall(cancel_check)],
        usage=usage,
    )
    agent = create_agent(model=llm, tools=tools, system_prompt=system_prompt)

    errors: list[str] = []
    log_paths: list[str] = []
    for attempt in range(1, max_attempts + 1):
        if cancel_check and cancel_check():
            raise AgentCanceled("Job canceled.")
        submitted.clear()
        if progress:
            progress(progress_start + min(20, attempt * 8), progress_message)
        attempt_prompt = dict(prompt)
        attempt_prompt["attempt"] = attempt
        if errors:
            attempt_prompt["previous_error"] = errors[-1]
        raw_log_output = ""
        content = ""
        try:
            response = agent.invoke(
                {"messages": [{"role": "user", "content": json.dumps(attempt_prompt, ensure_ascii=False)}]},
                config={"recursion_limit": recursion_limit},
            )
            raw_log_output = summarize_agent_response(response)
            content = agent_response_text(response)
            if submitted:
                plan = submitted[-1]
            elif content.strip():
                plan = accept(extract_json_object(content))
            else:
                raise ValueError(f"Agent finished without calling {submit_tool_name}.")
            plan["ai_log_path"] = write_ai_log(
                input_label=label, api_url=settings.api_url, model=settings.model,
                prompt=attempt_prompt, raw_output=raw_log_output or content, parsed_plan=plan,
            )
            plan["attempts"] = attempt
            return plan
        except AgentCanceled:
            raise
        except GraphRecursionError:
            errors.append(
                f"Stopped after {recursion_limit} steps without an accepted {submit_tool_name} call. "
                f"Use fewer exploratory calls and submit sooner."
            )
        except Exception as exc:
            errors.append(str(exc))
        log_paths.append(write_ai_log(
            input_label=f"{label}_attempt_{attempt}_invalid", api_url=settings.api_url, model=settings.model,
            prompt=attempt_prompt, raw_output=raw_log_output or content, error=errors[-1],
        ))
    raise ValueError(
        f"{label} agent failed after {max_attempts} attempts. "
        f"Logs: {', '.join(p for p in log_paths if p) or 'none'}. Last error: {errors[-1] if errors else 'unknown error'}"
    )
