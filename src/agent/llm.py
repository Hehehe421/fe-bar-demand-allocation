"""Resolve a Claude FMAPI endpoint at runtime.

No endpoint name is hardcoded anywhere in notebook or application code. The
preference order lives in `src/config.py` and this module walks it, returning
the first endpoint the workspace reports as READY. The endpoint actually chosen
is recorded in the agent's MLflow tags and in `evidence/04_genai_agent.md`.

Endpoint availability differs between workspaces and changes over time -- the
build prompt for this project named four endpoints, only three of which exist
here, and a fourth that does exist was not on the list. Resolving at runtime is
the only thing that keeps the agent portable.
"""

from __future__ import annotations

import functools
import os
import sys
from dataclasses import dataclass

from databricks.sdk import WorkspaceClient

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import config  # noqa: E402


@dataclass(frozen=True)
class ResolvedEndpoint:
    name: str
    ready: str
    considered: tuple[str, ...]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.name} (ready={self.ready})"


def _client(profile: str | None = None) -> WorkspaceClient:
    # Inside Model Serving there is no profile and no .databrickscfg; the
    # endpoint's own identity is injected into the environment, so the
    # no-argument constructor is the one that works in both places.
    if profile:
        return WorkspaceClient(profile=profile)
    return WorkspaceClient()


@functools.lru_cache(maxsize=4)
def resolve_llm_endpoint(profile: str | None = None) -> ResolvedEndpoint:
    """First READY endpoint from ``config.FMAPI_ENDPOINT_PREFERENCE``."""
    w = _client(profile)
    state: dict[str, str] = {}
    for e in w.serving_endpoints.list():
        if e.name in config.FMAPI_ENDPOINT_PREFERENCE:
            state[e.name] = str(getattr(e.state, "ready", "") or "")

    for name in config.FMAPI_ENDPOINT_PREFERENCE:
        if "READY" in state.get(name, ""):
            return ResolvedEndpoint(
                name=name,
                ready=state[name],
                considered=config.FMAPI_ENDPOINT_PREFERENCE,
            )

    raise RuntimeError(
        "no READY Claude endpoint among "
        f"{config.FMAPI_ENDPOINT_PREFERENCE}; observed states: {state}"
    )


def openai_client(profile: str | None = None):
    """OpenAI-compatible client pointed at the workspace's serving gateway."""
    return _client(profile).serving_endpoints.get_open_ai_client()
