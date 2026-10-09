"""The allocation agent: an MLflow `ResponsesAgent` on Claude via FMAPI.

Tool-calling loop, traced end to end. The FMAPI endpoint is resolved at runtime
from `config.FMAPI_ENDPOINT_PREFERENCE` and the system prompt is loaded from the
**MLflow prompt registry by version**, so a trace records exactly which model
and which prompt version produced it.

Tracing is explicit rather than autologged. Autolog would capture the LLM calls
but not the tool executions, and the tool spans are the interesting part: they
are what the numeric-grounding scorer checks the final answer against.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any

import mlflow
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
)

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import config  # noqa: E402
from agent import prompts as prompt_module  # noqa: E402
from agent.llm import openai_client, resolve_llm_endpoint  # noqa: E402
from agent.tools import TOOL_SCHEMAS, call_tool  # noqa: E402

MAX_TOOL_ITERATIONS = 6


def load_system_prompt(version: int | str | None = None) -> tuple[str, str]:
    """Load the system prompt by version. Returns (prompt_text, provenance).

    Three sources are tried in order and the provenance string records which one
    answered, so a trace never hides where its prompt came from:

    1. **MLflow prompt registry** — the intended path. Disabled at organisation
       level on this workspace (``FEATURE_DISABLED: CreatePrompt``).
    2. **Unity Catalog prompt store** — `src/agent/prompt_store.py`, the
       documented substitute. Versioned, content-addressed and governed.
    3. **Source file** — last resort so the agent still answers inside Model
       Serving if the endpoint's identity cannot reach the warehouse.
    """
    uri = f"prompts:/{config.PROMPT_REGISTRY_NAME}"
    try:
        p = (
            mlflow.genai.load_prompt(uri, version=version)
            if version is not None
            else mlflow.genai.load_prompt(uri)
        )
        return p.template, f"mlflow-registry {uri}/{p.version}"
    except Exception as mlflow_err:  # noqa: BLE001
        try:
            from agent import prompt_store

            pv = prompt_store.load_prompt(config.PROMPT_REGISTRY_NAME, version)
            return pv.template, f"uc-prompt-store {pv.uri} sha={pv.sha256[:12]}"
        except Exception as uc_err:  # noqa: BLE001
            return (
                prompt_module.SYSTEM_PROMPT,
                "source-file fallback ("
                f"mlflow: {type(mlflow_err).__name__}, uc: {type(uc_err).__name__})",
            )


class AllocationAgent(ResponsesAgent):
    """Planner-facing allocation analyst with five real tools."""

    def __init__(
        self,
        prompt_version: int | str | None = None,
        endpoint_name: str | None = None,
        profile: str | None = None,
    ) -> None:
        self._prompt_version = prompt_version
        self._endpoint_override = endpoint_name
        self._profile = profile
        self._system_prompt: str | None = None
        self._prompt_provenance: str = "not-loaded"
        self._endpoint: str | None = None

    # -- lazy setup so construction never does I/O -------------------------

    @property
    def endpoint(self) -> str:
        if self._endpoint is None:
            self._endpoint = (
                self._endpoint_override
                or resolve_llm_endpoint(self._profile).name
            )
        return self._endpoint

    @property
    def system_prompt(self) -> str:
        if self._system_prompt is None:
            self._system_prompt, self._prompt_provenance = load_system_prompt(
                self._prompt_version
            )
        return self._system_prompt

    @property
    def prompt_provenance(self) -> str:
        _ = self.system_prompt
        return self._prompt_provenance

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _to_chat_messages(req: ResponsesAgentRequest) -> list[dict[str, Any]]:
        """Flatten a Responses-style input list into chat messages."""
        out: list[dict[str, Any]] = []
        for item in req.input:
            d = item if isinstance(item, dict) else item.model_dump()
            role = d.get("role") or "user"
            content = d.get("content")
            if isinstance(content, list):
                parts = []
                for c in content:
                    cd = c if isinstance(c, dict) else {}
                    if cd.get("type") in (None, "text", "input_text", "output_text"):
                        parts.append(str(cd.get("text") or ""))
                content = "\n".join(p for p in parts if p)
            out.append({"role": role, "content": str(content or "")})
        return out

    def _run_tool(self, name: str, arguments: str) -> str:
        """One tool execution, as its own named span so the trace shows the evidence.

        Named per tool rather than decorated, so the span tree reads
        `tool:recommend_allocation` instead of four identical `_run_tool` spans.
        """
        with mlflow.start_span(name=f"tool:{name}", span_type="TOOL") as span:
            span.set_inputs({"tool": name, "arguments": arguments})
            result = call_tool(name, arguments)
            span.set_outputs({"result_chars": len(result), "result": result[:4000]})
            return result

    # -- the loop ---------------------------------------------------------

    # NOTE: no @mlflow.trace here on purpose. MLflow traces
    # `ResponsesAgent.predict` automatically, and decorating it as well produces
    # a duplicated root span -- which made the resulting trace fail to load with
    # "span data is corrupted". The tool and LLM spans below are still created
    # explicitly, because auto-tracing covers the predict boundary only.
    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        messages = [{"role": "system", "content": self.system_prompt}]
        messages += self._to_chat_messages(request)

        client = openai_client(self._profile)
        output_items: list[dict[str, Any]] = []
        tool_calls_made: list[str] = []
        usage = {"input_tokens": 0, "output_tokens": 0}

        mlflow.update_current_trace(
            tags={
                "llm_endpoint": self.endpoint,
                "prompt": self.prompt_provenance,
                "agent": "allocation_analyst",
            }
        )

        final_text = ""
        for _ in range(MAX_TOOL_ITERATIONS):
            with mlflow.start_span(name="llm_call", span_type="LLM") as span:
                span.set_inputs({"messages": messages[-4:], "endpoint": self.endpoint})
                completion = client.chat.completions.create(
                    model=self.endpoint,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    max_tokens=2048,
                    temperature=0.0,
                )
                choice = completion.choices[0]
                if completion.usage:
                    usage["input_tokens"] += int(completion.usage.prompt_tokens or 0)
                    usage["output_tokens"] += int(completion.usage.completion_tokens or 0)
                span.set_outputs({
                    "finish_reason": choice.finish_reason,
                    "content": (choice.message.content or "")[:2000],
                    "tool_calls": [
                        t.function.name for t in (choice.message.tool_calls or [])
                    ],
                })

            msg = choice.message
            if not msg.tool_calls:
                final_text = msg.content or ""
                break

            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": t.id,
                        "type": "function",
                        "function": {
                            "name": t.function.name,
                            "arguments": t.function.arguments,
                        },
                    }
                    for t in msg.tool_calls
                ],
            })

            for t in msg.tool_calls:
                result = self._run_tool(t.function.name, t.function.arguments)
                tool_calls_made.append(t.function.name)
                output_items.append({
                    "type": "function_call",
                    "id": f"fc_{uuid.uuid4().hex[:12]}",
                    "call_id": t.id,
                    "name": t.function.name,
                    "arguments": t.function.arguments,
                })
                output_items.append({
                    "type": "function_call_output",
                    "call_id": t.id,
                    "output": result,
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": t.id,
                    "content": result,
                })
        else:
            final_text = (
                "Stopped after the maximum number of tool iterations without "
                "reaching a final answer."
            )

        output_items.append({
            "type": "message",
            "id": f"msg_{uuid.uuid4().hex[:12]}",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": final_text}],
        })

        mlflow.update_current_trace(
            tags={"tools_called": ",".join(tool_calls_made) or "none"}
        )
        return ResponsesAgentResponse(
            output=output_items,
            custom_outputs={
                "llm_endpoint": self.endpoint,
                "prompt": self.prompt_provenance,
                "tools_called": tool_calls_made,
                "token_usage": usage,
            },
        )

    def predict_stream(self, request: ResponsesAgentRequest):
        """Non-incremental stream: emit the completed response as one event."""
        resp = self.predict(request)
        for item in resp.output:
            yield ResponsesAgentStreamEvent(type="response.output_item.done", item=item)


def answer(question: str, **kwargs: Any) -> ResponsesAgentResponse:
    """Convenience wrapper used by the notebook and the evaluation."""
    agent = AllocationAgent(**kwargs)
    return agent.predict(
        ResponsesAgentRequest(input=[{"role": "user", "content": question}])
    )


def final_text(resp: ResponsesAgentResponse) -> str:
    """Extract the assistant's final message text from a response."""
    for item in reversed(resp.output):
        d = item if isinstance(item, dict) else item.model_dump()
        if d.get("type") == "message":
            for c in d.get("content") or []:
                if (c or {}).get("type") == "output_text":
                    return str(c.get("text") or "")
    return ""


def tool_outputs(resp: ResponsesAgentResponse) -> list[dict[str, Any]]:
    """Every tool result in a response, parsed back from JSON."""
    out = []
    for item in resp.output:
        d = item if isinstance(item, dict) else item.model_dump()
        if d.get("type") == "function_call_output":
            try:
                out.append(json.loads(d.get("output") or "{}"))
            except json.JSONDecodeError:
                out.append({"_unparsed": d.get("output")})
    return out


#: Set so `mlflow.models.set_model` can pick the agent up when logged from a file.
AGENT = AllocationAgent()
mlflow.models.set_model(AGENT)
