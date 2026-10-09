"""Generate evidence/04_genai_agent.md.

Structural facts (endpoint state, model versions, prompt versions, table counts)
are queried live. The agent's own output -- its answers, the span tree, the
per-row evaluation table -- is lifted verbatim from the committed notebook
export, because that is the actual run and re-executing it would produce a
different trace than the one committed.

    python tools/evidence_04_genai_agent.py
"""

from __future__ import annotations

import datetime as dt
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from databricks.sdk import WorkspaceClient  # noqa: E402

from tools.dbsql import table  # noqa: E402

PROFILE = "vm-summer-classic"
HOST = "https://fevm-stable-classic-pqjg18.cloud.databricks.com"
C = "toy_demand_allocation"
AGENT_MODEL = f"{C}.ml.allocation_agent"
AGENT_ENDPOINT = "toy-allocation-agent"
PROMPT_NAME = f"{C}.ml.allocation_analyst_system_prompt"
EXPORT = pathlib.Path("notebooks/exports/04_allocation_agent.md")


def block_after(heading_fragment: str, which: int = 0, max_chars: int = 14000) -> str:
    """Return the Nth fenced ```text output block after a heading fragment."""
    if not EXPORT.exists():
        return "_(notebook export not found)_"
    md = EXPORT.read_text()
    i = md.find(heading_fragment)
    if i < 0:
        return f"_(section not found: {heading_fragment})_"
    blocks = re.findall(r"```text\n(.*?)```", md[i:], flags=re.S)
    if len(blocks) <= which:
        return "_(no output block found)_"
    return blocks[which][:max_chars].rstrip()


def main() -> int:
    w = WorkspaceClient(profile=PROFILE)
    out: list[str] = []
    a = out.append

    a("# Evidence 04 — Stage C2: the allocation agent")
    a("")
    a("| | |")
    a("|---|---|")
    a("| Produced by | `notebooks/04_allocation_agent.ipynb` on cluster "
      "`1008-205814-qhbfm7tc`, then `tools/evidence_04_genai_agent.py` |")
    a(f"| Generated at | {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M:%S} UTC |")
    a(f"| Workspace | `{HOST}` |")
    a("| Profile | `vm-summer-classic` |")
    a(f"| Registered agent | `{AGENT_MODEL}` |")
    a(f"| Serving endpoint | `{AGENT_ENDPOINT}` |")
    a(f"| Prompt store | `{C}.ml.prompt_versions` |")
    a(f"| UC objects read | `{C}.gold.fct_stockout_risk`, "
      f"`{C}.gold.fct_inbound_eta`, `{C}.gold.mv_fill_rate`, "
      f"`{C}.ml.supplier_doc_chunks`, `{C}.gold.dim_product` |")
    a("| Data | 100% synthetic |")
    a("")
    a("---")
    a("")

    # 1. endpoint resolution
    a("## 1. FMAPI endpoint, resolved at runtime")
    a("")
    a("No endpoint name is hardcoded in notebook or application code. The")
    a("preference order lives in `config.FMAPI_ENDPOINT_PREFERENCE` and")
    a("`src/agent/llm.py` returns the first the workspace reports READY. This is")
    a("not ceremony: the build brief for this project named four endpoints, one of")
    a("which does not exist in this workspace, while `databricks-claude-sonnet-4-6`")
    a("does exist and was not on the list.")
    a("")
    a("```text")
    a(block_after("## 1. Resolve the FMAPI endpoint", 0))
    a("```")
    a("")
    a("Live endpoint states for the candidates, at evidence-generation time:")
    a("")
    prefs = [
        "databricks-claude-sonnet-4-5", "databricks-claude-sonnet-4-6",
        "databricks-claude-sonnet-5", "databricks-claude-sonnet-5-5",
        "databricks-claude-haiku-4-5",
    ]
    states = {
        e.name: str(getattr(e.state, "ready", ""))
        for e in w.serving_endpoints.list()
        if e.name in prefs
    }
    a("```text")
    for p in prefs:
        a(f"  {p:34} {states.get(p, '(not present in this workspace)')}")
    a("```")
    a("")

    # 2. prompt versioning
    a("## 2. Prompt versioning — and a feature that is not available here")
    a("")
    a("The intended implementation was the **MLflow prompt registry**. It is")
    a("disabled at organisation level on this workspace, on both available paths.")
    a("The attempt and the verbatim errors are in the notebook output below rather")
    a("than hidden:")
    a("")
    a("```text")
    a("registry_uri=databricks-uc:")
    a("    RestException: FEATURE_DISABLED: CreatePrompt: Prompt registry is not")
    a("    enabled for organization 7474649643770783.")
    a("registry_uri=databricks:")
    a("    MlflowException: The 'register_prompt' API is not supported with the")
    a("    current registry. Prompts are supported in OSS MLflow and Unity Catalog,")
    a("    but not in the legacy Databricks workspace registry.")
    a("```")
    a("")
    a("Rather than drop the capability, it is implemented against Unity Catalog in")
    a("`src/agent/prompt_store.py`. Every property the stage actually needs")
    a("survives: the prompt is versioned with an immutable row per version, the")
    a("agent loads it back **by version number** rather than from the file it was")
    a("authored in, versions are content-addressed by SHA-256 so re-registering")
    a("unchanged text is idempotent, and the table inherits the Stage B grants —")
    a("which the MLflow registry would not have done. `agent.py` tries MLflow")
    a("first and falls back, recording on every trace which path answered, so")
    a("migrating once the feature is enabled is a two-function change.")
    a("")
    a("```text")
    a(block_after("## 2. Version the system prompt", 0))
    a("```")
    a("")
    a("Idempotence and the version list:")
    a("")
    a("```text")
    a(block_after("## 2. Version the system prompt", 1))
    a("```")
    a("")
    a("Live state of the prompt store:")
    a("")
    a("```text")
    try:
        a(table(f"""
SELECT prompt_name, version, template_sha256, LEFT(commit_message, 56) AS commit_message,
       LENGTH(template) AS template_chars, CAST(created_at AS STRING) AS created_at,
       created_by
FROM {C}.ml.prompt_versions ORDER BY version
""", max_col=70))
    except Exception as e:  # noqa: BLE001
        a(f"(prompt store query failed: {type(e).__name__}: {e})")
    a("```")
    a("")

    # 3. tools
    a("## 3. The five tools, each invoked against real Unity Catalog objects")
    a("")
    a("| Tool | Reads | Purpose |")
    a("|---|---|---|")
    a("| `get_stockout_risk` | `gold.fct_stockout_risk` | what is at risk, when, "
      "what it is worth |")
    a("| `get_inbound_eta` | `gold.fct_inbound_eta` | what is on the water and when "
      "it lands |")
    a("| `get_fill_rate_history` | `gold.mv_fill_rate` **metric view** | how each "
      "account tracks against its commitment |")
    a("| `search_supplier_docs` | `ml.supplier_doc_chunks` | supplier terms, quoted "
      "lead times, materials, safety standards |")
    a("| `recommend_allocation` | deterministic Python + the above | the split "
      "calculation itself |")
    a("")
    a("Tool 3 queries the **governed metric view** rather than recomputing fill")
    a("rate, so the agent cannot disagree with the dashboard or with Genie about")
    a("what fill rate means. Tool 5 is the one the model is explicitly forbidden")
    a("from doing itself.")
    a("")
    a("```text")
    a(block_after("## 3. The five tools, each exercised directly", 0))
    a("```")
    a("")
    a("### The Stage B column mask reaches the agent")
    a("")
    a("The allocator wants margin per unit as its value signal, but")
    a("`gold.dim_product.gross_margin_pct` carries the Stage B column mask and reads")
    a("NULL for every identity except the finance persona — including the one the")
    a("agent runs as. Rather than treat NULL as zero, the allocator detects it,")
    a("falls back to wholesale price, and **says so in its own tool output**. The")
    a("governance is not bypassed and the response is honest about which number it")
    a("could see. This is the governance from Stage B binding on the agent in Stage")
    a("C2, not a separate mechanism.")
    a("")
    a("```text")
    a(block_after("### The column mask reaches the agent", 0))
    a("```")
    a("")

    # 4. verbatim answers
    a("## 4. One full agent response, verbatim")
    a("")
    a("```text")
    a(block_after("## 4. One full agent response, verbatim", 0))
    a("```")
    a("")
    a("### A second question, to exercise document search")
    a("")
    a("The first question does not need the supplier PDFs, so the model correctly")
    a("never reaches for them. This one does, which puts all five tools on the")
    a("record as genuinely invoked rather than merely registered.")
    a("")
    a("```text")
    a(block_after("### A second question, to exercise the document-search tool", 0))
    a("```")
    a("")

    # 5. trace
    a("## 5. Trace: span tree, timings, token counts")
    a("")
    a("`ResponsesAgent.predict` is auto-traced by MLflow; the LLM and tool spans are")
    a("created explicitly inside the loop. Decorating `predict` with `@mlflow.trace`")
    a("as well produced a duplicated root span and the trace then failed to load")
    a("with *\"span data is corrupted\"*, so that decorator was removed — MLflow")
    a("warns about exactly this.")
    a("")
    a("```text")
    a(block_after("## 5. Trace: span tree, timings and token counts", 0))
    a("```")
    a("")
    a("### Tool spans: the arguments the model chose, and what came back")
    a("")
    a("```text")
    a(block_after("## 5. Trace: span tree, timings and token counts", 1))
    a("```")
    a("")

    # 6. eval
    a("## 6. Evaluation")
    a("")
    a("22 rows: 5 straightforward lookups, 5 multi-tool reasoning, 5 on the")
    a("constrained allocation decision, 3 on the supplier documents, and **4")
    a("adversarial** rows that ask for things the data does not support — a SKU that")
    a("does not exist, a retailer that does not exist, a margin figure the column")
    a("mask hides, and a forecast beyond the horizon. An eval set without those is")
    a("only testing the happy path.")
    a("")
    a("### Scorers")
    a("")
    a("| Scorer | Kind | What it checks |")
    a("|---|---|---|")
    a("| `Correctness` | built-in LLM judge | answer against `expected_facts` |")
    a("| `allocation_guidelines` | built-in LLM judge | the five guidelines, which "
      "are the same rules the system prompt states |")
    a("| `numeric_grounding` | **custom, mechanical** | every number in the answer "
      "appears in the actual tool output |")
    a("")
    a("The custom scorer is the one that matters. `Correctness` and `Guidelines` are")
    a("themselves LLM judges, so a confident, well-structured answer containing an")
    a("invented figure can pass them. `numeric_grounding` is not a judge at all: it")
    a("walks the trace's TOOL spans, extracts every number the tools actually")
    a("returned, extracts every number in the final answer, and checks membership")
    a("mechanically. That is the one check a fluent hallucination cannot talk its")
    a("way through.")
    a("")
    a("### Aggregate metrics")
    a("")
    a("```text")
    a(block_after("# metrics in their own cell", 0))
    a("```")
    a("")
    a("### Per-row results")
    a("")
    a("```text")
    a(block_after("## 9. Per-row results", 0, max_chars=20000))
    a("```")
    a("")
    a("### Pass rate per scorer and the grounding distribution")
    a("")
    a("```text")
    a(block_after("PASS RATE PER SCORER", 0))
    a("```")
    a("")
    a("### Failing rows, in full")
    a("")
    a("```text")
    a(block_after("# full text of the lowest-scoring rows", 0, max_chars=9000))
    a("```")
    a("")

    # 7. serving
    a("## 7. Registered in Unity Catalog and deployed to Model Serving")
    a("")
    a("```text")
    a(block_after("## 11. Log and register the agent", 0))
    a("```")
    a("")
    a("Live registry state:")
    a("")
    a("```text")
    try:
        rm = w.registered_models.get(AGENT_MODEL, include_aliases=True)
        amap: dict[int, list[str]] = {}
        for al in (rm.aliases or []):
            amap.setdefault(int(al.version_num), []).append(str(al.alias_name))
        lines = [f"{'version':>8}  {'aliases':<16} {'status':<10}"]
        lines.append(f"{'-' * 8}  {'-' * 16} {'-' * 10}")
        for v in sorted(w.model_versions.list(AGENT_MODEL), key=lambda x: int(x.version)):
            al = ",".join(f"@{x}" for x in sorted(amap.get(int(v.version), []))) or "-"
            lines.append(
                f"{int(v.version):>8}  {al:<16} "
                f"{str(v.status).replace('ModelVersionInfoStatus.', ''):<10}"
            )
        a("\n".join(lines))
    except Exception as e:  # noqa: BLE001
        a(f"(registry query failed: {type(e).__name__}: {e})")
    a("```")
    a("")
    a("Live serving endpoint state:")
    a("")
    a("```text")
    try:
        ep = w.serving_endpoints.get(AGENT_ENDPOINT)
        a(f"name          : {ep.name}")
        a(f"ready         : {getattr(ep.state, 'ready', None)}")
        a(f"config_update : {getattr(ep.state, 'config_update', None)}")
        for se in ((ep.config.served_entities or []) if ep.config else []):
            a(f"served_entity : {se.name} "
              f"(model {se.entity_name} v{se.entity_version}, "
              f"scale_to_zero={se.scale_to_zero_enabled})")
    except Exception as e:  # noqa: BLE001
        a(f"(endpoint query failed: {type(e).__name__}: {e})")
    a("```")
    a("")
    a("### A real request and response against the live endpoint")
    a("")
    a("Posted to the raw `/invocations` route. `WorkspaceClient.query()` wraps the")
    a("body into the legacy `{\"inputs\": ...}` scoring format, which a")
    a("ResponsesAgent endpoint rejects with *'Invalid input. One of \"instances\"")
    a("and \"inputs\" must be specified'* — the Responses contract wants")
    a("`{\"input\": [...]}` verbatim.")
    a("")
    a("```text")
    a(block_after("## 12. Deploy to Model Serving and query the live endpoint", 2,
                  max_chars=5000))
    a("```")
    a("")
    a("```text")
    a(block_after("LIVE ENDPOINT ANSWER", 0, max_chars=9000))
    a("```")
    a("")
    a("### The prompt fallback firing inside Model Serving")
    a("")
    a("Worth reading the `custom_outputs` above closely. In the notebook the agent")
    a("loads its prompt from the Unity Catalog prompt store; on the **deployed")
    a("endpoint** it reports")
    a("`source-file fallback (mlflow: MlflowException, uc: RuntimeError)`.")
    a("")
    a("That is the three-tier fallback in `load_system_prompt` doing its job. The")
    a("serving identity cannot reach the SQL warehouse the prompt store queries, so")
    a("the UC tier fails and the agent uses the prompt baked into the logged model")
    a("artifact. The answer is identical, because the artifact and the registered")
    a("version are the same text — but the agent **says which path it took** rather")
    a("than implying registry provenance it did not have.")
    a("")
    a("For production this is the thing to close: grant the endpoint's service")
    a("principal `USE SCHEMA` and `SELECT` on `ml.prompt_versions` plus `CAN USE`")
    a("on the warehouse, so prompt changes take effect by registry version without")
    a("relogging the model. That is a grant, not a code change.")
    a("")
    a("### Two deployment failures worth recording")
    a("")
    a("Both were real and both are fixed in the committed notebook:")
    a("")
    a("1. **`AttributeError: module 'lib' has no attribute 'GEN_EMAIL'`** during")
    a("   `log_model`. The MLflow 3 install pulls cryptography 50.x, which the")
    a("   runtime's pyOpenSSL 24.0.0 cannot load. Pinning cryptography *down* fails")
    a("   outright because mlflow and databricks-agents both need a newer one, so")
    a("   the fix is to move pyOpenSSL up instead.")
    a("2. **`No module named 'httpx'`** at inference time on the deployed endpoint.")
    a("   `httpx` is a transitive dependency of `openai`, but the resolved serving")
    a("   environment omitted it, so it is now declared explicitly in")
    a("   `pip_requirements`. The endpoint deployed READY and still failed every")
    a("   request until that was fixed — which is the argument for querying a live")
    a("   endpoint as evidence rather than trusting a successful deploy.")
    a("")

    dest = pathlib.Path("evidence/04_genai_agent.md")
    dest.write_text("\n".join(out))
    print(f"wrote {dest} ({len('\n'.join(out))} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
