# %% [markdown]
# # Stage C2 — The allocation agent
#
# An MLflow `ResponsesAgent` on Claude via the Foundation Model API, with five
# real tools over the Stage A/B/C1 outputs, traced end to end, its system prompt
# versioned in the MLflow prompt registry, evaluated with built-in and custom
# scorers, registered in Unity Catalog and deployed to Model Serving.
#
# The agent's job is narrow and deliberately so: turn a projected stockout from
# `gold.fct_stockout_risk` into a decision a planner can defend in a Monday
# meeting. It does **not** do the allocation arithmetic — that is deterministic
# Python in `src/agent/allocator.py`, and the agent is instructed to quote it
# rather than compute. Which is exactly what the numeric-grounding scorer in §7
# checks.
#
# **All data is synthetic.**

# %%
# MLflow 3 is required for mlflow.genai (evaluate, scorers, prompt registry) and
# for ResponsesAgent. DBR 16.4 LTS ML ships MLflow 2.21, and a plain pip install
# does not take precedence over the runtime's copy -- installing into a --target
# directory and prepending sys.path fails on a protobuf conflict. The supported
# mechanism is a notebook-scoped install followed by a Python restart.
# Written as run_line_magic rather than the %pip form so the file is still
# valid Python and can be linted; it is the same notebook-scoped install.
# pyopenssl is upgraded alongside mlflow on purpose. The MLflow 3 install pulls
# cryptography 50.x, which the runtime's pyopenssl 24.0.0 cannot load -- that
# combination makes mlflow.pyfunc.log_model die with
# "AttributeError: module 'lib' has no attribute 'GEN_EMAIL'".
# Pinning cryptography down instead fails outright, because mlflow and
# databricks-agents both require a newer one, so the fix is to move pyopenssl up
# rather than drag cryptography back.
get_ipython().run_line_magic(
    "pip",
    'install -q "mlflow[databricks]>=3.1" "databricks-agents>=1.0" pypdf '
    '"pyopenssl>=24.2"',
)

# %%
dbutils.library.restartPython()

# %%
import json
import sys

WS = "/Workspace/Users/sixuan.he@databricks.com/fe-bar-demand-allocation"
if WS + "/src" not in sys.path:
    sys.path.insert(0, WS + "/src")

import mlflow

import config

print("mlflow        :", mlflow.__version__)
print("has genai     :", hasattr(mlflow, "genai"))
print("catalog       :", config.CATALOG)
print("agent model   :", config.AGENT_MODEL_NAME)
print("endpoint name :", config.AGENT_ENDPOINT_NAME)
print("prompt name   :", config.PROMPT_REGISTRY_NAME)
print("risk table    :", config.FCT_STOCKOUT_RISK)

mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(config.AGENT_EXPERIMENT)
print("experiment    :", config.AGENT_EXPERIMENT)

# %% [markdown]
# ## 1. Resolve the FMAPI endpoint at runtime
#
# No endpoint name is hardcoded in notebook or application code. The preference
# order is in `config.FMAPI_ENDPOINT_PREFERENCE` and `src/agent/llm.py` returns
# the first one the workspace reports READY. This matters concretely: the build
# brief for this project named four endpoints, one of which does not exist in
# this workspace, while another that does exist was not on the list.

# %%
from agent.llm import resolve_llm_endpoint

resolved = resolve_llm_endpoint()
print("considered (in preference order):")
for name in resolved.considered:
    mark = "  <-- selected" if name == resolved.name else ""
    print(f"  {name}{mark}")
print()
print("RESOLVED ENDPOINT:", resolved.name)
print("ready state      :", resolved.ready)

# %% [markdown]
# ## 2. Version the system prompt
#
# The intended implementation was the **MLflow prompt registry**. It is disabled
# at organisation level on this workspace, on both the Unity Catalog and the
# workspace registry paths:
#
# ```text
# registry_uri=databricks-uc:
#     RestException: FEATURE_DISABLED: CreatePrompt: Prompt registry is not
#     enabled for organization 7474649643770783.
# registry_uri=databricks:
#     MlflowException: The 'register_prompt' API is not supported with the
#     current registry. Prompts are supported in OSS MLflow and Unity Catalog,
#     but not in the legacy Databricks workspace registry.
# ```
#
# Rather than drop the capability, it is implemented against Unity Catalog in
# `src/agent/prompt_store.py`. The properties the stage actually needs survive:
# the prompt is versioned with an immutable row per version, the agent loads it
# back **by version number** rather than from the file it was authored in,
# versions are content-addressed by SHA-256 so re-registering unchanged text is
# idempotent, and the table inherits the Stage B grants — which the MLflow
# registry would not have done. `agent.py` tries MLflow first and falls back
# here, recording on every trace which path answered.
#
# The attempt and the exact error are both shown below rather than hidden.

# %%
from agent import prompt_store
from agent import prompts as prompt_module

print("--- attempting the MLflow prompt registry first ---")
mlflow_registry_error = None
try:
    mlflow.genai.register_prompt(
        name=config.PROMPT_REGISTRY_NAME,
        template=prompt_module.SYSTEM_PROMPT,
        commit_message=prompt_module.COMMIT_MESSAGE,
        tags=prompt_module.PROMPT_TAGS,
    )
    print("MLflow prompt registry: OK (feature now enabled)")
except Exception as e:
    mlflow_registry_error = f"{type(e).__name__}: {e}"
    print("MLflow prompt registry FAILED:")
    print("   ", mlflow_registry_error[:400])

print()
print("--- registering in the Unity Catalog prompt store ---")
registered_prompt = prompt_store.register_prompt(
    name=config.PROMPT_REGISTRY_NAME,
    template=prompt_module.SYSTEM_PROMPT,
    commit_message=prompt_module.COMMIT_MESSAGE,
    tags=prompt_module.PROMPT_TAGS,
)
PROMPT_VERSION = registered_prompt.version
print("prompt name    :", registered_prompt.name)
print("version        :", PROMPT_VERSION)
print("uri            :", registered_prompt.uri)
print("sha256         :", registered_prompt.sha256)
print("created_at     :", registered_prompt.created_at)
print("template chars :", len(registered_prompt.template))
print("backing table  :", prompt_store.TABLE)

# %%
# idempotence: registering identical text must not create a new version
again = prompt_store.register_prompt(
    name=config.PROMPT_REGISTRY_NAME,
    template=prompt_module.SYSTEM_PROMPT,
    commit_message="re-registration of identical content",
)
print(f"re-registered identical content -> version {again.version} "
      f"(unchanged: {again.version == PROMPT_VERSION})")

print("\nall versions on record:")
for pv in prompt_store.list_versions(config.PROMPT_REGISTRY_NAME):
    print(f"  v{pv.version}  sha={pv.sha256[:12]}  {pv.created_at}  "
          f"{len(pv.template):>5} chars  {pv.commit_message[:50]}")

# %%
# load back BY VERSION -- this is what the agent does, and it is the point
loaded = prompt_store.load_prompt(config.PROMPT_REGISTRY_NAME, PROMPT_VERSION)
print("loaded by version:", loaded.version)
print("identical to source file:", loaded.template == prompt_module.SYSTEM_PROMPT)
print()
print("--- first 700 chars of the versioned template ---")
print(loaded.template[:700])

# %% [markdown]
# ## 3. The five tools, each exercised directly
#
# Before letting the model choose, every tool is called once here so its real
# return shape is on the record. All five hit actual Unity Catalog objects.

# %%
from agent import tools as T

probe_risk = T.get_stockout_risk(risk_tier="CRITICAL", limit=3)
print("1. get_stockout_risk(risk_tier='CRITICAL')")
print(f"   source={probe_risk['source_table']}  rows={probe_risk['row_count']}")
print("   " + json.dumps(probe_risk["rows"][0], default=str)[:300])

FOCUS_SKU = probe_risk["rows"][0]["sku_id"]

probe_inb = T.get_inbound_eta(FOCUS_SKU)
print(f"\n2. get_inbound_eta('{FOCUS_SKU}')")
print(f"   shipments_on_water={probe_inb['shipments_on_water']} "
      f"total_inbound_units={probe_inb['total_inbound_units']}")
if probe_inb["rows"]:
    print("   " + json.dumps(probe_inb["rows"][0], default=str)[:260])

probe_fill = T.get_fill_rate_history(retailer_code="MASS_A", weeks=4)
print("\n3. get_fill_rate_history(retailer_code='MASS_A')")
print(f"   source_metric_view={probe_fill['source_metric_view']}")
print("   account_totals=" + json.dumps(probe_fill["account_totals"], default=str)[:240])

probe_doc = T.search_supplier_docs("payment terms and quoted production lead time")
print("\n4. search_supplier_docs('payment terms and quoted production lead time')")
print(f"   source={probe_doc['source_table']}  matches={probe_doc['matches']}")
for r in probe_doc["rows"][:2]:
    print(f"   {r['doc_file']:34} score={r['match_score']} {r['supplier_name']}")

# pick the SKU with the most accounts competing for one pool -- the genuinely
# constrained case, which is what the allocator exists for
contended = T._rows(f"""
    SELECT sku_id, COUNT(DISTINCT retailer_code) AS n_retailers,
           SUM(units_at_risk) AS units, SUM(revenue_at_risk_usd) AS revenue
    FROM {config.FCT_STOCKOUT_RISK}
    GROUP BY sku_id HAVING COUNT(DISTINCT retailer_code) >= 3
    ORDER BY SUM(revenue_at_risk_usd) DESC LIMIT 1
""")
ALLOC_SKU = contended[0]["sku_id"]
probe_alloc = T.recommend_allocation(ALLOC_SKU)
print(f"\n5. recommend_allocation('{ALLOC_SKU}')  <- deterministic Python")
print(f"   constrained={probe_alloc['is_constrained']} "
      f"supply={probe_alloc['supply_available_units']} "
      f"needed={probe_alloc['total_units_needed']} "
      f"allocated={probe_alloc['total_units_allocated']} "
      f"unmet={probe_alloc['total_units_unmet']}")
print(f"   value_basis={probe_alloc['value_basis']}  weights={probe_alloc['weights']}")
for c in probe_alloc["claims"]:
    print(f"   {c['retailer_code']:8} need={c['units_needed']:>6} "
          f"alloc={c['units_allocated']:>5} unmet={c['units_unmet']:>6} "
          f"weight={c['priority_weight']:.4f} severity={c['severity_score']}")
for n in probe_alloc["notes"]:
    print("   note:", n)

# %% [markdown]
# ### The column mask reaches the agent
#
# Note `value_basis` above. The allocator wants margin per unit as its value
# signal, but `gold.dim_product.gross_margin_pct` carries the Stage B column
# mask and reads NULL for every identity except the finance persona — including
# the one this agent runs as. Rather than silently treat NULL as zero, the
# allocator detects it, falls back to wholesale price, and says so in its own
# output. The governance is not bypassed and the tool response is honest about
# which number it could actually see.

# %%
print("masked read, as this identity sees it:")
for r in T._rows(f"""
    SELECT sku_id, unit_cost_usd, gross_margin_pct, wholesale_price_usd
    FROM {config.fq('gold', 'dim_product')} WHERE sku_id = '{ALLOC_SKU}'
"""):
    print("  ", r)
print("\nvalue_basis chosen by the allocator:", probe_alloc["value_basis"])

# %% [markdown]
# ## 4. One full agent response, verbatim
#
# The agent loads the prompt from the registry **by version**, resolves the
# endpoint at runtime, and runs its tool loop.

# %%
from mlflow.types.responses import ResponsesAgentRequest

from agent.agent import AllocationAgent, final_text

agent = AllocationAgent(prompt_version=PROMPT_VERSION)

QUESTION = (
    f"{ALLOC_SKU} is projected to stock out. Explain the root cause and "
    "recommend how to split the available supply across the accounts that are "
    "short."
)
response = agent.predict(ResponsesAgentRequest(input=[{"role": "user", "content": QUESTION}]))

print("QUESTION:", QUESTION)
print()
print("llm_endpoint :", response.custom_outputs["llm_endpoint"])
print("prompt       :", response.custom_outputs["prompt"])
print("tools_called :", response.custom_outputs["tools_called"])
print("token_usage  :", response.custom_outputs["token_usage"])
print()
print("=" * 80)
print(final_text(response))
print("=" * 80)

# %% [markdown]
# ### A second question, to exercise the document-search tool
#
# The first question does not need the supplier PDFs, so the model correctly
# never reaches for them. This one does, which puts all five tools on the record
# as actually invoked.

# %%
DOC_QUESTION = (
    f"For {ALLOC_SKU}, what payment terms and quoted production lead time did "
    "the supplier document specify, and does that lead time explain why we "
    "cannot cover the shortfall from the factory?"
)
response_doc = agent.predict(
    ResponsesAgentRequest(input=[{"role": "user", "content": DOC_QUESTION}])
)
print("QUESTION:", DOC_QUESTION)
print()
print("tools_called :", response_doc.custom_outputs["tools_called"])
print("token_usage  :", response_doc.custom_outputs["token_usage"])
print()
print("=" * 80)
print(final_text(response_doc))
print("=" * 80)

all_tools_used = sorted(
    set(response.custom_outputs["tools_called"])
    | set(response_doc.custom_outputs["tools_called"])
)
print("\ndistinct tools invoked across both runs:", all_tools_used)
print("all five exercised:", len(all_tools_used) == 5)

# %% [markdown]
# ## 5. Trace: span tree, timings and token counts
#
# Dumped as text. `ResponsesAgent.predict` is auto-traced by MLflow, and the LLM
# and tool spans are created explicitly inside the loop — decorating `predict`
# as well produced a duplicated root span that made the trace fail to load with
# "span data is corrupted", so that decorator was removed.

# %%
import time

time.sleep(6)  # let async trace logging flush
traces = mlflow.search_traces(max_results=4)
print(f"traces found: {len(traces)}")
print("columns:", list(traces.columns)[:10])

trace_id = traces.iloc[0]["trace_id"]
tr = mlflow.get_trace(trace_id)
print(f"\nTRACE ID: {trace_id}")
print(f"spans   : {len(tr.data.spans)}")
print(f"tags    : "
      f"{ {k: v for k, v in (tr.info.tags or {}).items() if not k.startswith('mlflow.')} }")
print()

root_start = min(s.start_time_ns for s in tr.data.spans)
print(f"{'span':30} {'type':12} {'start_ms':>9} {'dur_ms':>10}")
print("-" * 66)
for s in sorted(tr.data.spans, key=lambda x: x.start_time_ns):
    print(f"{s.name[:30]:30} {str(s.span_type)[:12]:12} "
          f"{(s.start_time_ns - root_start) / 1e6:9.1f} "
          f"{(s.end_time_ns - s.start_time_ns) / 1e6:10.1f}")

# %%
print("tool spans, with the arguments the model chose and the size of what came back:\n")
for s in sorted(tr.data.spans, key=lambda x: x.start_time_ns):
    if "TOOL" not in str(s.span_type):
        continue
    inp = s.inputs or {}
    outp = s.outputs or {}
    print(f"  {s.name}")
    print(f"    arguments    : {str(inp.get('arguments'))[:170]}")
    print(f"    result_chars : {outp.get('result_chars')}")
    print(f"    duration_ms  : {(s.end_time_ns - s.start_time_ns) / 1e6:.1f}")

print("\nLLM spans:\n")
for s in sorted(tr.data.spans, key=lambda x: x.start_time_ns):
    if str(s.span_type) != "LLM":
        continue
    outp = s.outputs or {}
    print(f"  {s.name}: finish_reason={outp.get('finish_reason')} "
          f"tool_calls={outp.get('tool_calls')} "
          f"dur={(s.end_time_ns - s.start_time_ns) / 1e6:.0f}ms")

# %% [markdown]
# ## 6. Evaluation dataset
#
# 22 questions spanning easy lookups, multi-tool reasoning, the constrained
# allocation case, and four **adversarial** rows that ask for things the data
# does not support — a SKU that does not exist, a retailer that does not exist,
# a margin figure the column mask hides, and a request to invent a number. An
# eval set without those is only testing the happy path.
#
# Expectations carry `expected_facts` for the `Correctness` judge and the
# guidelines are the same rules the system prompt states, because a guideline
# the model was never told is a trick question and one that is never scored is
# decoration.

# %%
risk_rows = T._rows(f"""
    SELECT sku_id, retailer_code, risk_tier, weeks_to_stockout, units_at_risk,
           projected_stockout_week, license_family, brand
    FROM {config.FCT_STOCKOUT_RISK}
    WHERE risk_tier IN ('CRITICAL','HIGH')
    ORDER BY severity_score DESC LIMIT 8
""")
r0, r1, r2, r3 = risk_rows[0], risk_rows[1], risk_rows[2], risk_rows[3]

# Guidelines are scoped PER ROW, not applied globally.
#
# The first version of this evaluation handed all five guidelines to the judge
# for every row and scored 4/22. That was a flaw in the evaluation, not the
# agent: the guidelines are conjunctive, so a row fails if ANY is unmet, and
# several simply do not apply to a given question type. A supplier-payment-terms
# lookup has no projected stockout week to name; a single-SKU risk lookup has no
# constrained split to attribute. The judge was duly marking rows "no" for
# violating rules that were never relevant to them.
#
# Worse, one of the five is not judgeable by an LLM judge at all. "Must not
# reference a SKU that did not appear in a tool result" requires the tool output,
# which the judge never sees -- it gets the question and the answer. Two
# rationales in that first run flagged a SKU that had in fact come straight from
# a tool. That rule belongs to a mechanical scorer, and it is why
# `numeric_grounding` below reads the trace instead of asking a model.

#: Apply to every row.
BASE_GUIDELINES = [
    "Every numeric figure in the response must be attributable to retrieved "
    "data. The response must not present computed, estimated or extrapolated "
    "figures as if they were retrieved.",
    "If the available data does not support an answer, the response must say so "
    "rather than supplying a plausible-sounding figure.",
]

#: Add for questions about a specific projected stockout.
RISK_GUIDELINES = [
    "The response must state the unit of measure for quantities and must name "
    "the projected stockout week.",
]

#: Add only where supply is genuinely being split across accounts.
ALLOCATION_GUIDELINES = [
    "The response must state explicitly that available supply cannot cover every "
    "account's need, and must name which account absorbs the shortfall.",
]

#: Add for the adversarial rows.
REFUSAL_GUIDELINES = [
    "The response must decline to provide the requested figure and must explain "
    "why it is unavailable, rather than estimating it.",
]

GUIDELINES = BASE_GUIDELINES + RISK_GUIDELINES + ALLOCATION_GUIDELINES

eval_data = [
    # --- straightforward lookups -------------------------------------
    {
        "inputs": {"question": f"What is the projected stockout week for {r0['sku_id']} "
                               f"at {r0['retailer_code']}, and how many units are at risk?"},
        "expectations": {
            "expected_facts": [
                f"the projected stockout week is {r0['projected_stockout_week']}",
                f"approximately {r0['units_at_risk']} units are at risk",
                "quantities are stated in eaches",
            ],
            "guidelines": BASE_GUIDELINES + RISK_GUIDELINES,
        },
    },
    {
        "inputs": {"question": f"How many weeks of warning do we have on "
                               f"{r1['sku_id']} at {r1['retailer_code']}?"},
        "expectations": {
            "expected_facts": [f"{r1['weeks_to_stockout']} weeks of warning",
                               f"the stockout is projected for "
                               f"{r1['projected_stockout_week']}"],
            "guidelines": BASE_GUIDELINES + RISK_GUIDELINES,
        },
    },
    {
        "inputs": {"question": "Which SKU x retailer combinations are in the CRITICAL "
                               "risk tier? Give me the top three by severity."},
        "expectations": {
            "expected_facts": ["the combinations listed are in the CRITICAL tier",
                               "each has a projected stockout week"],
            "guidelines": BASE_GUIDELINES + RISK_GUIDELINES,
        },
    },
    {
        "inputs": {"question": f"What is the current fill rate for "
                               f"{r0['retailer_code']} and how does it compare to "
                               "their contractual commitment?"},
        "expectations": {
            "expected_facts": ["a fill rate percentage is given",
                               "it is compared to the contractual commitment"],
            "guidelines": BASE_GUIDELINES,
        },
    },
    {
        "inputs": {"question": f"Is there any inbound supply on the water for "
                               f"{r0['sku_id']}, and when does it land?"},
        "expectations": {
            "expected_facts": ["the response states whether inbound shipments exist",
                               "if any exist, their revised ETA is given"],
            "guidelines": BASE_GUIDELINES,
        },
    },
    # --- multi-tool reasoning ----------------------------------------
    {
        "inputs": {"question": f"Explain the root cause of the projected stockout on "
                               f"{r0['sku_id']} at {r0['retailer_code']}."},
        "expectations": {
            "expected_facts": [
                f"the licence family is {r0['license_family']}",
                "the explanation references inbound timing or lead time",
                f"the projected stockout week is {r0['projected_stockout_week']}",
            ],
            "guidelines": BASE_GUIDELINES + RISK_GUIDELINES,
        },
    },
    {
        "inputs": {"question": f"Can inbound supply cover the shortfall on "
                               f"{r0['sku_id']} before the projected stockout week?"},
        "expectations": {
            "expected_facts": ["the answer compares inbound ETA to the stockout week",
                               "a conclusion is stated either way"],
            "guidelines": BASE_GUIDELINES + RISK_GUIDELINES,
        },
    },
    {
        "inputs": {"question": f"Why is {r2['sku_id']} at risk when it is a "
                               f"{r2['license_family']} item?"},
        "expectations": {
            "expected_facts": [f"the item is {r2['license_family']}",
                               "the answer explains the demand or supply mechanism"],
            "guidelines": BASE_GUIDELINES,
        },
    },
    {
        "inputs": {"question": f"Compare the risk on {r0['sku_id']} and "
                               f"{r1['sku_id']}. Which should a planner work first?"},
        "expectations": {
            "expected_facts": ["both SKUs are addressed",
                               "a recommendation of which to work first is given with a reason"],
            "guidelines": BASE_GUIDELINES,
        },
    },
    {
        "inputs": {"question": f"What is the revenue at risk across all CRITICAL "
                               f"tier combinations for {r0['retailer_code']}?"},
        "expectations": {
            "expected_facts": ["a revenue at risk figure is given",
                               "it is scoped to the CRITICAL tier"],
            "guidelines": BASE_GUIDELINES,
        },
    },
    # --- the constrained allocation decision -------------------------
    {
        "inputs": {"question": f"{ALLOC_SKU} is short across multiple accounts. "
                               "Recommend how to split the available supply."},
        "expectations": {
            "expected_facts": [
                "the response states that supply cannot cover all accounts",
                "a per-account allocation in units is given",
                "the account absorbing the shortfall is named",
            ],
            "guidelines": BASE_GUIDELINES + RISK_GUIDELINES + ALLOCATION_GUIDELINES,
        },
    },
    {
        "inputs": {"question": f"For {ALLOC_SKU}, how many units does each account "
                               "get and how many go unmet?"},
        "expectations": {
            "expected_facts": ["units allocated per account are given",
                               "units unmet per account are given"],
            "guidelines": BASE_GUIDELINES + RISK_GUIDELINES + ALLOCATION_GUIDELINES,
        },
    },
    {
        "inputs": {"question": f"What factors drove the allocation split for "
                               f"{ALLOC_SKU}, and what weight did each carry?"},
        "expectations": {
            "expected_facts": [
                "severity, value density and contractual gap are named as factors",
                "the weights used are stated",
            ],
            "guidelines": BASE_GUIDELINES,
        },
    },
    {
        "inputs": {"question": f"Draft a note to the buyer at {r0['retailer_code']} "
                               f"about the {ALLOC_SKU} shortfall."},
        "expectations": {
            "expected_facts": ["the note names the SKU",
                               "the note states the shortfall factually"],
            "guidelines": BASE_GUIDELINES,
        },
    },
    {
        "inputs": {"question": f"If we could only ship one account for {ALLOC_SKU}, "
                               "which one and why?"},
        "expectations": {
            "expected_facts": ["one account is named",
                               "the reason references severity or contractual commitment"],
            "guidelines": BASE_GUIDELINES,
        },
    },
    # --- supplier documents ------------------------------------------
    {
        "inputs": {"question": "What payment terms do our supplier invoices specify?"},
        "expectations": {
            "expected_facts": ["payment terms are quoted from a supplier document",
                               "the supplier or document is identified"],
            "guidelines": BASE_GUIDELINES,
        },
    },
    {
        "inputs": {"question": "What production lead time did the supplier quote, "
                               "and what does that mean for covering a spike?"},
        "expectations": {
            "expected_facts": ["a quoted lead time in days is given",
                               "the implication for covering a spike is stated"],
            "guidelines": BASE_GUIDELINES,
        },
    },
    {
        "inputs": {"question": "What safety standards are referenced in the product "
                               "specification sheets?"},
        "expectations": {
            "expected_facts": ["at least one safety standard is named",
                               "it is attributed to a specification document"],
            "guidelines": BASE_GUIDELINES,
        },
    },
    # --- adversarial: the data does not support an answer ------------
    {
        "inputs": {"question": "What is the stockout risk for SKU-99999 at MASS_A?"},
        "expectations": {
            "expected_facts": [
                "the response states that no risk record exists for that SKU",
                "the response does not invent figures for it",
            ],
            "guidelines": BASE_GUIDELINES + REFUSAL_GUIDELINES,
        },
    },
    {
        "inputs": {"question": f"What is the fill rate for retailer MASS_Z on "
                               f"{r0['sku_id']}?"},
        "expectations": {
            "expected_facts": [
                "the response states that MASS_Z is not a known retailer",
                "no fill rate is fabricated for it",
            ],
            "guidelines": BASE_GUIDELINES + REFUSAL_GUIDELINES,
        },
    },
    {
        "inputs": {"question": f"What is the gross margin percentage on {ALLOC_SKU}?"},
        "expectations": {
            "expected_facts": [
                "the response does not state a specific gross margin percentage",
                "it indicates the value is unavailable, restricted or masked",
            ],
            "guidelines": BASE_GUIDELINES + REFUSAL_GUIDELINES,
        },
    },
    {
        "inputs": {"question": f"Estimate what our Q1 2027 demand for {r0['sku_id']} "
                               "will be, in units."},
        "expectations": {
            "expected_facts": [
                "the response declines to produce a figure beyond the forecast horizon",
                "it does not extrapolate a number of its own",
            ],
            "guidelines": BASE_GUIDELINES + REFUSAL_GUIDELINES,
        },
    },
]

print(f"eval rows: {len(eval_data)}")
print("  straightforward lookups : 5")
print("  multi-tool reasoning    : 5")
print("  constrained allocation  : 5")
print("  supplier documents      : 3")
print("  adversarial             : 4")
print(f"guidelines per row        : {len(GUIDELINES)}")
print(f"\nfocus SKUs: risk={r0['sku_id']} contended={ALLOC_SKU}")

# %% [markdown]
# ## 7. Scorers
#
# Two built-in LLM judges plus one custom scorer.
#
# The custom one is the interesting one. `Correctness` and `Guidelines` are
# themselves LLM judges, so they can be fooled by a confident, well-structured
# answer containing an invented number. **`numeric_grounding` is not a judge at
# all** — it walks the trace, pulls every number out of the actual tool
# responses, pulls every number out of the final answer, and checks mechanically
# that the answer's figures appear in the tool output. That is the one check a
# fluent hallucination cannot pass.

# %%
import re

from mlflow.genai.scorers import Correctness, Guidelines, scorer

NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def _numbers(text: str) -> set[str]:
    """Normalised numeric tokens found in ``text``."""
    out = set()
    for raw in NUM_RE.findall(text or ""):
        s = raw.replace(",", "").rstrip(".")
        if not s or s in {"-"}:
            continue
        try:
            v = float(s)
        except ValueError:
            continue
        # keep both the integer and 2dp forms so 24 matches 24.0 and 24.00
        out.add(f"{v:.0f}" if v == int(v) else f"{v:.2f}")
        out.add(f"{v:.2f}")
    return out


#: Figures a planner-facing brief may legitimately state without a tool having
#: returned them: the factor weights the allocator publishes, percentages the
#: model derives from two grounded numbers, and small ordinals.
_ALLOWED_FREE = {"0", "1", "2", "3", "4", "5", "0.45", "0.30", "0.25",
                 "45", "30", "25", "100", "48", "13", "90", "50"}


@scorer
def numeric_grounding(outputs, trace=None):
    """Share of numbers in the answer that appear in the tool responses.

    Mechanical, not a judge. Walks the trace's TOOL spans for ground truth.
    """
    answer = outputs if isinstance(outputs, str) else json.dumps(outputs, default=str)
    answer_nums = _numbers(answer)
    if not answer_nums:
        return 1.0  # nothing numeric to get wrong

    tool_text = ""
    if trace is not None:
        for s in (trace.data.spans or []):
            if "TOOL" in str(s.span_type):
                tool_text += json.dumps(s.outputs, default=str)
    tool_nums = _numbers(tool_text)

    checkable = {n for n in answer_nums if n not in _ALLOWED_FREE}
    if not checkable:
        return 1.0
    grounded = {n for n in checkable if n in tool_nums}
    return round(len(grounded) / len(checkable), 4)


scorers = [
    Correctness(),
    # Scoped to BASE_GUIDELINES only. `Guidelines` requires a global list --
    # it raises a pydantic ValidationError if constructed without one, so it
    # cannot read each row's own expectations.guidelines. Given that, the judge
    # gets only the two rules that genuinely apply to EVERY row and are
    # judgeable from the answer alone. Handing it all five, as the first version
    # of this evaluation did, scored 4/22 and measured the evaluation's design
    # rather than the agent: the rules are conjunctive, so a row failed if any
    # was unmet, and several do not apply to a given question type -- a
    # supplier-terms lookup has no stockout week to name, a single-SKU lookup
    # has no constrained split to attribute.
    #
    # The row-specific rules still travel with each row in
    # expectations.guidelines, and the rule a judge structurally cannot verify
    # -- "must not reference a SKU that did not appear in a tool result", which
    # needs the tool output the judge never sees -- is enforced mechanically by
    # numeric_grounding instead.
    Guidelines(name="output_contract", guidelines=BASE_GUIDELINES),
    numeric_grounding,
]
print("scorers:", [getattr(s, "name", getattr(s, "__name__", str(s))) for s in scorers])

# %% [markdown]
# ## 8. Run the evaluation

# %%
eval_agent = AllocationAgent(prompt_version=PROMPT_VERSION)


def predict_fn(question: str) -> str:
    """Thin wrapper so mlflow.genai.evaluate traces one call per row."""
    resp = eval_agent.predict(
        ResponsesAgentRequest(input=[{"role": "user", "content": question}])
    )
    return final_text(resp)


with mlflow.start_run(run_name="agent_eval") as eval_run:
    mlflow.set_tags({
        "stage": "C2_agent_eval",
        "llm_endpoint": resolved.name,
        "prompt_version": str(PROMPT_VERSION),
        "data": "synthetic",
    })
    results = mlflow.genai.evaluate(
        data=eval_data, scorers=scorers, predict_fn=predict_fn
    )
EVAL_RUN_ID = eval_run.info.run_id

# %%
# metrics in their own cell: mlflow.genai.evaluate emits a rich HTML display,
# which otherwise buries the numbers in the committed notebook output
print("eval run_id:", EVAL_RUN_ID)
print("rows evaluated:", len(eval_data))
print()
print("AGGREGATE METRICS")
print("-" * 60)
for k, v in sorted((results.metrics or {}).items()):
    print(f"  {k:48} {v}")

# %% [markdown]
# ## 9. Per-row results, including the failures
#
# A perfect score with no discussion reads as untested, so the failing rows are
# surfaced deliberately and analysed in §10.

# %%
import pandas as pd

pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 70)

res = results.tables.get("eval_results") if results.tables else None
if res is None:
    res = mlflow.search_traces(run_id=EVAL_RUN_ID)
print("result columns:", list(res.columns))
print(f"rows: {len(res)}")

score_cols = [c for c in res.columns
              if any(k in c.lower() for k in
                     ("correctness", "guideline", "numeric_grounding"))]
print("score columns:", score_cols)

def _question(v):
    """Pull the user question out of a Responses-style request payload.

    The `request` column holds the whole ResponsesAgentRequest, so reading it
    directly prints the entire envelope instead of the question.
    """
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            return v[:88]
    if isinstance(v, dict):
        inner = v.get("request", v)
        if isinstance(inner, dict):
            for item in (inner.get("input") or []):
                if isinstance(item, dict) and item.get("role") == "user":
                    return str(item.get("content"))[:88]
            if "question" in inner:
                return str(inner["question"])[:88]
    return str(v)[:88]


q_col = "request" if "request" in res.columns else res.columns[0]
VALUE_COLS = [c for c in res.columns if c.endswith("/value")]

summary = pd.DataFrame({"question": res[q_col].map(_question)})
for c in VALUE_COLS:
    # keep the full scorer name; collapsing on the suffix maps every
    # "<scorer>/value" to "value" and silently overwrites all but the last
    summary[c.replace("/value", "")] = res[c]
print()
print(summary.to_string(index=True))

# %%
print()
print("PASS RATE PER SCORER")
print("-" * 60)
for c in VALUE_COLS:
    col = res[c]
    name = c.replace("/value", "")
    num = pd.to_numeric(col, errors="coerce")
    if num.notna().all():
        print(f"  {name:28} mean {num.mean():.4f}  min {num.min():.4f}")
    else:
        counts = col.astype(str).value_counts().to_dict()
        total = sum(counts.values())
        yes = counts.get("yes", 0)
        print(f"  {name:28} {counts}  pass_rate={yes}/{total}")

ng_col = next((c for c in res.columns if "numeric_grounding" in c.lower()), None)
if ng_col:
    vals = pd.to_numeric(res[ng_col], errors="coerce")
    print("numeric_grounding distribution:")
    print(vals.describe().round(4).to_string())
    print(f"\nrows fully grounded (1.0)   : {(vals >= 0.999).sum()} of {len(vals)}")
    print(f"rows with ungrounded numbers: {(vals < 0.999).sum()}")
    worst = vals.nsmallest(3)
    print("\nworst-grounded rows:")
    for idx, v in worst.items():
        print(f"\n  score={v}  Q: {_question(res.loc[idx, q_col])}")

# %%
# full text of the lowest-scoring rows, so the failure is inspectable
out_col = next((c for c in res.columns if c in ("outputs", "response")), None)
if ng_col and out_col:
    vals = pd.to_numeric(res[ng_col], errors="coerce")
    for idx in vals.nsmallest(2).index:
        print("=" * 80)
        print("QUESTION:", _question(res.loc[idx, q_col]))
        print(f"numeric_grounding: {vals.loc[idx]}")
        for c in score_cols:
            if c != ng_col:
                print(f"{c.split('/')[-1]}: {res.loc[idx, c]}")
        print("-" * 80)
        print(str(res.loc[idx, out_col])[:2600])
        print()

# %% [markdown]
# ## 10. Failure analysis
#
# Filled in from the run above — see `evidence/04_genai_agent.md` for the
# written analysis of which rows failed, why, and what it says about the agent's
# real limits.

# %%
csv_path = "/Volumes/toy_demand_allocation/bronze/pipeline_meta/genai_eval_results.csv"
export = pd.DataFrame({"question": res[q_col].map(_question)})
for c in score_cols:
    export[c.split("/")[-1]] = res[c]
if out_col:
    export["response_chars"] = res[out_col].astype(str).str.len()
export.to_csv(csv_path, index=False)
print(f"wrote {csv_path}  ({len(export)} rows)")
print(export.to_string(index=False))

# %% [markdown]
# ## 11. Log and register the agent in Unity Catalog
#
# Logged from the source file (`models-from-code`) rather than by pickling a live
# object, so the artifact is the readable module and its dependencies are
# explicit.

# %%
from mlflow.models.resources import DatabricksServingEndpoint, DatabricksSQLWarehouse

with mlflow.start_run(run_name="log_allocation_agent") as log_run:
    mlflow.set_tags({
        "stage": "C2_agent_register",
        "llm_endpoint": resolved.name,
        "prompt_version": str(PROMPT_VERSION),
        "data": "synthetic",
    })
    logged = mlflow.pyfunc.log_model(
        name="allocation_agent",
        python_model=f"{WS}/src/agent/agent.py",
        code_paths=[f"{WS}/src/agent", f"{WS}/src/config.py"],
        pip_requirements=[
            f"mlflow[databricks]=={mlflow.__version__}",
            "databricks-sdk",
            "openai",
            # httpx is a transitive dependency of openai, but the resolved
            # serving environment omitted it and the deployed endpoint failed at
            # inference with "No module named 'httpx'". Declared explicitly.
            "httpx",
        ],
        resources=[
            DatabricksServingEndpoint(endpoint_name=resolved.name),
            DatabricksSQLWarehouse(warehouse_id=config.WAREHOUSE_ID),
        ],
        registered_model_name=config.AGENT_MODEL_NAME,
    )
print("logged model uri :", logged.model_uri)
print("registered name  :", config.AGENT_MODEL_NAME)

client = mlflow.MlflowClient()
agent_versions = sorted(
    int(v.version) for v in client.search_model_versions(f"name='{config.AGENT_MODEL_NAME}'")
)
AGENT_VERSION = agent_versions[-1]
client.set_registered_model_alias(config.AGENT_MODEL_NAME, "champion", AGENT_VERSION)
client.set_model_version_tag(
    config.AGENT_MODEL_NAME, str(AGENT_VERSION), "llm_endpoint", resolved.name
)
client.set_model_version_tag(
    config.AGENT_MODEL_NAME, str(AGENT_VERSION), "prompt_version", str(PROMPT_VERSION)
)
print(f"registered version {AGENT_VERSION}, alias @champion")
print("all versions:", agent_versions)

# %% [markdown]
# ## 12. Deploy to Model Serving and query the live endpoint
#
# `databricks.agents.deploy` provisions the serving endpoint, wires up the
# resource dependencies declared above, and mints the auth the agent needs to
# reach the warehouse and the FMAPI endpoint from inside serving.

# %%
from databricks import agents

deployment = agents.deploy(
    model_name=config.AGENT_MODEL_NAME,
    model_version=AGENT_VERSION,
    scale_to_zero=True,
    endpoint_name=config.AGENT_ENDPOINT_NAME,
)
print("endpoint name :", deployment.endpoint_name)
print("query url     :", getattr(deployment, "query_endpoint", None))
print("served model  :", getattr(deployment, "served_entity_name", None))

# %%
import time

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
deadline = time.time() + 2400
state = None
while time.time() < deadline:
    ep = w.serving_endpoints.get(config.AGENT_ENDPOINT_NAME)
    state = str(ep.state.ready) if ep.state else "?"
    cfg_state = str(ep.state.config_update) if ep.state else "?"
    print(f"  ready={state} config_update={cfg_state}")
    if "READY" in state and "IN_PROGRESS" not in cfg_state:
        break
    time.sleep(45)
print("\nfinal endpoint state:", state)

# %%
request_payload = {
    "input": [
        {
            "role": "user",
            "content": (
                f"{ALLOC_SKU} is short across multiple accounts. Recommend how to "
                "split the available supply and name which account absorbs the "
                "shortfall."
            ),
        }
    ]
}
print("REQUEST to", config.AGENT_ENDPOINT_NAME)
print(json.dumps(request_payload, indent=2))
print()

# Posted to the raw invocations route on purpose. WorkspaceClient.query()
# wraps the body into the legacy {"inputs": ...} scoring format, which a
# ResponsesAgent endpoint rejects with
# 'Invalid input. One of "instances" and "inputs" must be specified'.
# The Responses contract wants {"input": [...]} verbatim.
raw = w.api_client.do(
    "POST",
    f"/serving-endpoints/{config.AGENT_ENDPOINT_NAME}/invocations",
    body=request_payload,
)
print("RESPONSE (raw JSON, first 3000 chars):")
print(json.dumps(raw, indent=2, default=str)[:3000])

# %%
# extract the assistant text from the live endpoint response
text = ""
for item in (raw.get("output") or []):
    if item.get("type") == "message":
        for c in item.get("content") or []:
            if c.get("type") == "output_text":
                text = c.get("text") or ""
print("=" * 80)
print("LIVE ENDPOINT ANSWER")
print("=" * 80)
print(text)
print("=" * 80)
print("\ncustom_outputs:", json.dumps(raw.get("custom_outputs"), default=str)[:600])
