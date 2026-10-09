"""Versioned prompt store backed by a Unity Catalog Delta table.

**Why this exists.** The intended implementation was the MLflow prompt
registry. It is not available on this workspace — both the Unity Catalog and the
workspace registry paths fail at an organisation-level feature gate:

    registry_uri=databricks-uc:
        RestException: FEATURE_DISABLED: CreatePrompt: Prompt registry is not
        enabled for organization 7474649643770783.
    registry_uri=databricks:
        MlflowException: The 'register_prompt' API is not supported with the
        current registry. Prompts are supported in OSS MLflow and Unity
        Catalog, but not in the legacy Databricks workspace registry.

Rather than drop the capability, it is implemented against Unity Catalog
directly. The properties that actually matter for the stage are preserved:

* the prompt is **versioned**, with an immutable row per version;
* the agent **loads it back by version number**, not from the file it was
  authored in;
* versions are content-addressed by SHA-256, so re-registering an unchanged
  prompt is idempotent and does not inflate the version count;
* it is **governed** — the table lives in the project catalog and inherits the
  Stage B grants, which the MLflow registry would not have done;
* the version that produced a given answer is recorded on the agent's traces.

Migrating to the MLflow registry once the feature is enabled means swapping the
two functions below; `agent.py` already tries MLflow first and falls back here.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import config  # noqa: E402

TABLE = config.fq("ml", "prompt_versions")
_WAREHOUSE = os.environ.get("DBX_WAREHOUSE_ID", config.WAREHOUSE_ID)

_w: WorkspaceClient | None = None


def _client() -> WorkspaceClient:
    global _w
    if _w is None:
        _w = WorkspaceClient()
    return _w


def _sql(statement: str) -> list[list[Any]]:
    resp = _client().statement_execution.execute_statement(
        statement=statement, warehouse_id=_WAREHOUSE, wait_timeout="50s"
    )
    while resp.status and resp.status.state in (
        StatementState.PENDING,
        StatementState.RUNNING,
    ):
        resp = _client().statement_execution.get_statement(resp.statement_id)
    if resp.status and resp.status.state != StatementState.SUCCEEDED:
        err = getattr(resp.status.error, "message", resp.status.state)
        raise RuntimeError(f"prompt store query failed: {err}")
    return [list(r) for r in ((resp.result.data_array or []) if resp.result else [])]


def _q(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("'", "\\'")


@dataclass(frozen=True)
class PromptVersion:
    name: str
    version: int
    template: str
    sha256: str
    commit_message: str
    created_at: str

    @property
    def uri(self) -> str:
        return f"uc-prompt:/{self.name}/{self.version}"


def ensure_table() -> None:
    _sql(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
          prompt_name     STRING  COMMENT 'Fully-qualified prompt identifier.',
          version         INT     COMMENT 'Monotonic version, 1-based.',
          template        STRING  COMMENT 'The prompt text as registered.',
          template_sha256 STRING  COMMENT 'Content hash; makes registration idempotent.',
          commit_message  STRING  COMMENT 'Why this version was created.',
          tags_json       STRING  COMMENT 'Free-form tags as JSON.',
          created_at      TIMESTAMP COMMENT 'Registration time.',
          created_by      STRING  COMMENT 'Identity that registered the version.'
        )
        COMMENT 'Versioned prompt store for the allocation agent. Stands in for the
MLflow prompt registry, which is disabled at organisation level on this
workspace (FEATURE_DISABLED: CreatePrompt). Immutable one row per version,
content-addressed by SHA-256. SYNTHETIC-DATA PROJECT.'
        """
    )


def register_prompt(
    name: str,
    template: str,
    commit_message: str = "",
    tags: dict[str, str] | None = None,
) -> PromptVersion:
    """Register ``template``; idempotent if the content is unchanged."""
    ensure_table()
    sha = hashlib.sha256(template.encode("utf-8")).hexdigest()

    existing = _sql(
        f"""
        SELECT version, template_sha256, commit_message, CAST(created_at AS STRING)
        FROM {TABLE} WHERE prompt_name = '{_q(name)}'
        ORDER BY version DESC LIMIT 1
        """
    )
    if existing and existing[0][1] == sha:
        v = int(existing[0][0])
        return PromptVersion(name, v, template, sha, existing[0][2] or "", existing[0][3])

    next_version = (int(existing[0][0]) + 1) if existing else 1
    _sql(
        f"""
        INSERT INTO {TABLE}
        SELECT '{_q(name)}', {next_version}, '{_q(template)}', '{sha}',
               '{_q(commit_message)}', '{_q(json.dumps(tags or {}))}',
               current_timestamp(), current_user()
        """
    )
    row = _sql(
        f"""
        SELECT CAST(created_at AS STRING) FROM {TABLE}
        WHERE prompt_name = '{_q(name)}' AND version = {next_version}
        """
    )
    return PromptVersion(
        name, next_version, template, sha, commit_message, row[0][0] if row else ""
    )


def load_prompt(name: str, version: int | str | None = None) -> PromptVersion:
    """Load a prompt version; latest when ``version`` is None."""
    where = f"prompt_name = '{_q(name)}'"
    if version is not None:
        where += f" AND version = {int(version)}"
    rows = _sql(
        f"""
        SELECT version, template, template_sha256, commit_message,
               CAST(created_at AS STRING)
        FROM {TABLE} WHERE {where} ORDER BY version DESC LIMIT 1
        """
    )
    if not rows:
        raise LookupError(f"no prompt '{name}' version {version} in {TABLE}")
    r = rows[0]
    return PromptVersion(name, int(r[0]), r[1], r[2], r[3] or "", r[4])


def list_versions(name: str) -> list[PromptVersion]:
    rows = _sql(
        f"""
        SELECT version, template, template_sha256, commit_message,
               CAST(created_at AS STRING)
        FROM {TABLE} WHERE prompt_name = '{_q(name)}' ORDER BY version
        """
    )
    return [
        PromptVersion(name, int(r[0]), r[1], r[2], r[3] or "", r[4]) for r in rows
    ]
