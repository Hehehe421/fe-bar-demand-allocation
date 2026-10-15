"""Allocation Command Center.

A planner's Monday view: what is about to run out, what it is worth, what the agent
recommends, and a place to record the decision so it can be measured later.

    worklist (Lakebase)  ->  SKU detail + 13-week forecast (Lakebase)
                         ->  agent (Model Serving)  ->  decision (written to Lakebase)
    KPIs (governed metric views, warehouse)      Ask the data (Genie)

Run locally against the live workspace:

    DATABRICKS_CONFIG_PROFILE=vm-summer-classic python app.py
"""

from __future__ import annotations

import os
import threading
from typing import Any

import backend as b
import dash
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import settings as S
from dash import Input, Output, State, ctx, dash_table, dcc, html, no_update
from dash.dash_table.Format import Format, Group, Scheme
from flask import request

# Palette: validated with the dataviz skill's validate_palette.js (light surface).
# Series 1/2 pass every categorical gate (adjacent CVD dE 24.7, normal-vision dE 33.6).
SURFACE, GRID = "#fcfcfb", "#e8e7e3"
INK, INK2 = "#0b0b0b", "#52514e"
SERIES1, SERIES2 = "#2a78d6", "#eb6834"
STATUS = {"CRITICAL": "#d03b3b", "HIGH": "#ec835a", "MEDIUM": "#fab219", "LOW": "#0ca30c"}
GLYPH = {"CRITICAL": "▲", "HIGH": "◆", "MEDIUM": "●", "LOW": "○"}  # icon + label, never colour alone

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP, dbc.icons.FONT_AWESOME],
    title="Allocation Command Center",
    suppress_callback_exceptions=True,
)
server = app.server


# --------------------------------------------------------------------------
# formatting helpers
# --------------------------------------------------------------------------

def money(v: float | None) -> str:
    if v is None:
        return "—"
    return f"${v / 1e6:.2f}M" if abs(v) >= 1e6 else f"${v / 1e3:,.0f}K" if abs(v) >= 1e4 else f"${v:,.0f}"


def num(v: Any, d: int = 0) -> str:
    return "—" if v is None else f"{float(v):,.{d}f}"


def pct(v: float | None, d: int = 1) -> str:
    return "—" if v is None else f"{v:.{d}f}%"


def tier_badge(t: str) -> html.Span:
    return html.Span([html.Span(GLYPH.get(t, "·"), className="g"), t], className=f"tier t-{t}")


def planner_id() -> str:
    try:
        return request.headers.get("x-forwarded-email") or os.getenv("PLANNER_ID", "local-dev")
    except RuntimeError:
        return os.getenv("PLANNER_ID", "local-dev")


def alert(msg: Any, color: str = "secondary") -> dbc.Alert:
    return dbc.Alert(msg, color=color, className="py-2 mb-2", style={"fontSize": "13px"})


# --------------------------------------------------------------------------
# charts
# --------------------------------------------------------------------------

def _style(fig: go.Figure, h: int) -> go.Figure:
    fig.update_layout(
        height=h, margin=dict(l=8, r=12, t=8, b=8), paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font=dict(family="-apple-system, Segoe UI, Roboto, Arial", size=12, color=INK2),
        hoverlabel=dict(bgcolor=SURFACE, font_size=12, font_color=INK, bordercolor=GRID),
    )
    return fig


def family_fig(rows: list[dict[str, Any]]) -> go.Figure:
    rows = sorted(rows, key=lambda r: r["revenue_at_risk"])
    fig = go.Figure(go.Bar(
        x=[r["revenue_at_risk"] for r in rows],
        y=[r["license_family"] for r in rows], orientation="h",
        marker=dict(color=SERIES1),
        text=[money(r["revenue_at_risk"]) for r in rows], textposition="outside",
        textfont=dict(color=INK, size=12), cliponaxis=False,
        customdata=[[r["units_at_risk"], r["rows"]] for r in rows],
        hovertemplate="<b>%{y}</b><br>Revenue at risk %{x:$,.0f}<br>"
                      "Units at risk %{customdata[0]:,.0f}<br>%{customdata[1]:,} SKU × account rows"
                      "<extra></extra>",
    ))
    fig.update_xaxes(visible=False, range=[0, max([r["revenue_at_risk"] for r in rows] or [1]) * 1.25])
    fig.update_yaxes(showgrid=False, tickfont=dict(color=INK))
    fig.update_layout(bargap=0.45)
    return _style(fig, 170)


def forecast_fig(fc: list[dict[str, Any]], stockout_week: Any) -> go.Figure:
    x = [str(r["week_end_date"]) for r in fc]
    fig = go.Figure()
    for name, key, color in (("p90 (high case)", "forecast_p90", SERIES2),
                             ("p50 (expected)", "forecast_p50", SERIES1)):
        fig.add_trace(go.Scatter(
            x=x, y=[float(r[key]) for r in fc], name=name, mode="lines+markers",
            line=dict(color=color, width=2),
            marker=dict(size=8, color=color, line=dict(color=SURFACE, width=2)),
            hovertemplate=f"{name.split()[0]} %{{y:,.0f}} units<extra></extra>",
        ))
        fig.add_annotation(x=x[-1], y=float(fc[-1][key]), text=name.split()[0], showarrow=False,
                           xanchor="left", xshift=10, font=dict(color=INK2, size=12))
    if stockout_week is not None and str(stockout_week) in x:
        sw = str(stockout_week)
        fig.add_shape(type="line", x0=sw, x1=sw, yref="paper", y0=0, y1=1,
                      line=dict(color=INK2, width=1.5, dash="dot"))
        fig.add_annotation(x=sw, yref="paper", y=1.0, text="projected stockout", showarrow=False,
                           yanchor="bottom", font=dict(color=INK2, size=11))
    fig.update_xaxes(showgrid=False, tickformat="%d %b", linecolor=GRID, tickfont=dict(color=INK2))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, tickformat=",", title_text="units / week",
                     title_font=dict(size=11))
    fig.update_layout(hovermode="x unified", margin=dict(l=8, r=60, t=34, b=8),
                      legend=dict(orientation="h", y=-0.16, x=0))
    return _style(fig, 300)


# --------------------------------------------------------------------------
# layout
# --------------------------------------------------------------------------

def kpi(label: str, value: str, sub: Any = None) -> dbc.Col:
    return dbc.Col(html.Div([html.Div(label, className="label"),
                             html.Div(value, className="value"),
                             html.Div(sub or "", className="sub")], className="kpi"),
                   xs=6, md=4, xl=2, className="mb-3")


WORKLIST_COLS = [
    dict(name="Tier", id="tier_label"),
    dict(name="SKU", id="sku_id"),
    dict(name="Account", id="retailer_code"),
    dict(name="Family", id="license_family"),
    dict(name="Stockout", id="projected_stockout_week"),
    dict(name="Wks", id="weeks_to_stockout", type="numeric"),
    dict(name="Units", id="units_at_risk", type="numeric",
         format=Format(group=Group.yes, precision=0, scheme=Scheme.fixed)),
    dict(name="Revenue", id="revenue_at_risk_usd", type="numeric",
         format=Format(group=Group.yes, precision=0, scheme=Scheme.fixed).symbol_prefix("$")),
    dict(name="Sev.", id="severity_score", type="numeric",
         format=Format(precision=1, scheme=Scheme.fixed)),
    dict(name="Decision", id="decision"),
]


def header() -> html.Div:
    return html.Div(dbc.Row([
        dbc.Col([html.H1("Allocation Command Center", className="app-title"),
                 html.P("Forecast → risk → agent → decision. All data is synthetic.",
                        className="app-sub")], md=6),
        dbc.Col([
            html.Label("View as planner for", className="small-note me-2"),
            dcc.Dropdown(id="scope", clearable=False, value="ALL", style={"minWidth": "190px"},
                         options=[{"label": "All accounts", "value": "ALL"}]
                         + [{"label": r, "value": r} for r in S.RETAILERS]),
        ], md=6, className="d-flex align-items-center justify-content-md-end"),
    ], align="center"), className="app-header")


def worklist_card() -> html.Div:
    return html.Div([
        html.H2("At-risk worklist"),
        html.Div("Ranked by severity from Lakebase (point lookups, not a warehouse scan). "
                 "Select a row for the detail. Account scoping is applied in this query: Unity "
                 "Catalog will not put a row filter on a Lakebase copy.", className="hint"),
        dbc.Checklist(id="tiers", inline=True, value=["CRITICAL", "HIGH"],
                      options=[{"label": f"{GLYPH[t]} {t}", "value": t} for t in S.TIERS],
                      className="mb-2", style={"fontSize": "13px"}),
        dcc.Loading(dash_table.DataTable(
            id="worklist", columns=WORKLIST_COLS, data=[], row_selectable="single",
            selected_rows=[0], sort_action="native", page_action="native", page_size=14,
            style_table={"overflowX": "auto"},
            style_header={"backgroundColor": "#f4f3f0", "fontWeight": 600, "fontSize": "12px",
                          "fontFamily": "inherit", "whiteSpace": "nowrap",
                          "border": "none", "borderBottom": f"1px solid {GRID}"},
            style_cell={"fontSize": "13px", "padding": "6px 8px", "border": "none",
                        "borderBottom": f"1px solid {GRID}", "textAlign": "left",
                        "backgroundColor": SURFACE, "color": INK, "whiteSpace": "nowrap",
                        "fontFamily": "inherit"},
            style_cell_conditional=[{"if": {"column_type": "numeric"}, "textAlign": "right"}],
            style_data_conditional=[
                {"if": {"filter_query": f'{{risk_tier}} = "{t}"', "column_id": "tier_label"},
                 "borderLeft": f"5px solid {c}", "fontWeight": 600} for t, c in STATUS.items()
            ] + [{"if": {"state": "selected"}, "backgroundColor": "#eaf2fc", "border": "none"}],
        ), type="circle"),
        html.Div(id="worklist-note", className="small-note mt-2"),
    ], className="card-s")


def right_column() -> html.Div:
    return html.Div([
        html.Div([html.H2(id="detail-title"), html.Div(id="detail-facts", className="mb-2"),
                  dcc.Graph(id="forecast-graph", config={"displayModeBar": False}),
                  html.Div(id="forecast-note", className="small-note")], className="card-s"),
        html.Div([
            html.H2("Real-time check"),
            html.Div("Re-forecast this series live on the real-time endpoint and compare it with "
                     "the batch forecast above. Same model, two serving paths.", className="hint"),
            dbc.Button("Re-forecast now", id="rt-btn", color="primary", outline=True, size="sm"),
            html.Span("  First call after idle can take 2–3 minutes while the endpoint scales up "
                      "from zero (measured: 52 s and 138 s on two occasions).", className="small-note ms-2"),
            dcc.Loading(html.Div(id="rt-out", className="mt-2"), type="circle"),
        ], className="card-s"),
        html.Div([
            html.H2("Agent"),
            html.Div("Calls the live allocation agent. It reads Unity Catalog through tools and "
                     "quotes the deterministic allocator; the request id links a decision back "
                     "to the brief that informed it.", className="hint"),
            dcc.Textarea(id="agent-q", style={"width": "100%", "height": "70px", "fontSize": "13px"}),
            dbc.Button("Ask the agent", id="agent-btn", color="primary", size="sm", className="mt-1"),
            dcc.Loading(html.Div(id="agent-out", className="mt-2"), type="circle"),
        ], className="card-s"),
        html.Div([
            html.H2("Decide"),
            html.Div(id="dec-reco", className="hint"),
            dbc.RadioItems(id="dec-action", inline=True, value="accept",
                           options=[{"label": "Accept", "value": "accept"},
                                    {"label": "Override", "value": "override"},
                                    {"label": "Defer", "value": "defer"}],
                           className="mb-2", style={"fontSize": "13px"}),
            dbc.Row([
                dbc.Col([html.Label("Units to ship", className="small-note"),
                         dbc.Input(id="dec-units", type="number", min=0, step=1, size="sm")], width=4),
                dbc.Col([html.Label("Rationale", className="small-note"),
                         dbc.Input(id="dec-why", type="text", size="sm",
                                   placeholder="why (required for an override)")], width=8),
            ], className="mb-2"),
            dbc.Button("Record decision", id="dec-save", color="success", size="sm", disabled=True),
            html.Div(id="dec-out", className="mt-2"),
            html.Div(id="dec-history", className="mt-2"),
        ], className="card-s"),
    ])


def workspace_url() -> str:
    """The injected DATABRICKS_HOST has no scheme in the Apps runtime; locally it is unset."""
    h = os.getenv("DATABRICKS_HOST") or b.w().config.host
    return h if h.startswith("http") else f"https://{h}"


def genie_card() -> html.Div:
    url = f"{workspace_url()}/genie/rooms/{S.GENIE_SPACE_ID}"
    return html.Div([
        html.H2("Ask the data (Genie)"),
        html.Div(["Natural-language questions over the governed Gold tables and metric views. "
                  "Answers show the SQL Genie wrote. ",
                  html.A("Open the space ↗", href=url, target="_blank", id="genie-link")],
                 className="hint"),
        dbc.InputGroup([
            dbc.Input(id="genie-q", type="text", size="sm", n_submit=0,
                      placeholder="e.g. Which licence family has the most revenue at risk?"),
            dbc.Button("Ask", id="genie-btn", color="primary", size="sm"),
        ]),
        dcc.Loading(html.Div(id="genie-out", className="mt-2"), type="circle"),
    ], className="card-s")


app.layout = html.Div([
    header(),
    dbc.Container([
        dcc.Store(id="sel"), dcc.Store(id="agent-store"), dcc.Store(id="saved", data=0),
        dbc.Row(id="kpis", className="mt-3"),
        dbc.Row([
            dbc.Col([worklist_card(),
                     html.Div([html.H2("Revenue at risk by licence family"),
                               html.Div("Across every at-risk row in scope, all tiers.",
                                        className="hint"),
                               dcc.Graph(id="family-graph", config={"displayModeBar": False})],
                              className="card-s"),
                     genie_card()], lg=7),
            dbc.Col(right_column(), lg=5),
        ]),
        html.P("Synthetic data. Forecast: ml.demand_forecaster (batch) and ml.demand_forecaster_rt "
               "(real-time). Decisions are written to Lakebase and synced back to Unity Catalog.",
               className="small-note mb-4"),
    ], fluid=True),
])


# --------------------------------------------------------------------------
# callbacks
# --------------------------------------------------------------------------

def _scope(v: str | None) -> str | None:
    return None if v in (None, "ALL") else v


@app.callback(Output("kpis", "children"), Input("scope", "value"), Input("saved", "data"))
def render_kpis(scope: str, _saved: int):
    sc = _scope(scope)
    try:
        t = b.risk_totals(sc)
        d = b.decision_stats()
    except Exception as e:  # noqa: BLE001
        return dbc.Col(alert(f"Lakebase unavailable: {e}", "danger"))
    try:
        k = b.kpis(sc)
    except Exception as e:  # noqa: BLE001
        k = {}
        warn = alert(f"Warehouse KPIs unavailable: {str(e)[:160]}", "warning")
    else:
        warn = None
    tiles = [
        kpi("Fill rate", pct(k.get("fill_rate"), 2), "governed metric view"),
        kpi("OTIF", pct(k.get("otif"), 2), "governed metric view"),
        kpi("Forecast WAPE", pct(k.get("wape")),
            html.Span(["trend items: ", html.B(pct(k.get("wape_trend")))])),
        kpi("P90 coverage", pct(k.get("p90_cov")),
            html.Span(["trend items: ", html.B(pct(k.get("p90_cov_trend"))), " vs 90% target"])),
        kpi("Revenue at risk", money(t["revenue_at_risk"]),
            html.Span([html.B(f"{t['critical_rows']:,}"), " critical of ",
                       f"{t['at_risk_rows']:,}", " rows"])),
        kpi("Decisions logged", f"{d['n']:,}",
            f"{d['accepted']} accepted · {d['overridden']} overridden · {d['deferred']} deferred"),
    ]
    return ([dbc.Col(warn, width=12)] if warn else []) + tiles


@app.callback(
    Output("worklist", "data"), Output("worklist", "selected_rows"), Output("family-graph", "figure"),
    Output("worklist-note", "children"),
    Input("scope", "value"), Input("tiers", "value"), Input("saved", "data"),
)
def render_worklist(scope: str, tiers: list[str], _saved: int):
    sc = _scope(scope)
    rows = b.worklist(sc, tiers or [])
    data = [{**r, "tier_label": f"{GLYPH[r['risk_tier']]} {r['risk_tier']}",
             "projected_stockout_week": str(r["projected_stockout_week"]),
             "decision": r["last_action"] or "—"} for r in rows]
    fam = family_fig(b.risk_by_family(sc))
    note = f"Showing the top {len(data)} by severity." if len(data) >= 250 else f"{len(data)} rows."
    return data, [0] if data else [], fam, note


@app.callback(Output("sel", "data"), Input("worklist", "selected_rows"), State("worklist", "data"))
def select_row(selected: list[int], data: list[dict]):
    if not selected or not data or selected[0] >= len(data):
        return None
    r = data[selected[0]]
    return {"sku": r["sku_id"], "retailer": r["retailer_code"]}


@app.callback(
    Output("detail-title", "children"), Output("detail-facts", "children"),
    Output("forecast-graph", "figure"), Output("forecast-note", "children"),
    Output("dec-history", "children"),
    Input("sel", "data"), Input("saved", "data"),
)
def render_detail(sel: dict | None, _saved: int):
    if not sel:
        return "Select a row", "", go.Figure(), "", ""
    sku, ret = sel["sku"], sel["retailer"]
    risk, fc, hist = b.sku_risk(sku, ret), b.sku_forecast(sku, ret), b.sku_decisions(sku, ret)
    if not risk:
        return f"{sku} · {ret}", alert("No longer at risk."), go.Figure(), "", ""
    facts = html.Div([
        html.Div([tier_badge(risk["risk_tier"]), html.Span(f"  {risk['brand']} · "
                  f"{risk['license_family']} · {risk['lifecycle_stage']}", className="fact")]),
        html.Div([html.Span("Severity ", className="fact"), html.B(num(risk["severity_score"], 1)),
                  html.Span("  ·  Stockout in ", className="fact"),
                  html.B(f"{risk['weeks_to_stockout']} wks"),
                  html.Span(f" ({risk['projected_stockout_week']})", className="fact")]),
        html.Div([html.Span("Units at risk ", className="fact"), html.B(num(risk["units_at_risk"])),
                  html.Span("  ·  Revenue ", className="fact"), html.B(money(risk["revenue_at_risk_usd"])),
                  html.Span("  ·  On hand ", className="fact"), html.B(num(risk["sku_on_hand_units"])),
                  html.Span("  ·  Inbound ", className="fact"),
                  html.B(num(risk["sku_inbound_horizon_units"]))]),
    ])
    note = (f"Batch forecast: {fc[0]['algorithm']} · ml.demand_forecaster v{fc[0]['model_version']} "
            f"@champion · scored with fe.score_batch." if fc else "No forecast rows.")
    rows = [html.Div(f"{h['decided_at']:%d %b %H:%M} · {h['action']} · {h['planner_units']} units "
                     f"(recommended {h['recommended_units']}) · {h['planner_id']}", className="meta")
            for h in hist]
    history = html.Div([html.Div("Earlier decisions on this series", className="small-note"),
                        *rows]) if rows else ""
    return (f"{sku} at {ret}", facts,
            forecast_fig(fc, risk["projected_stockout_week"]) if fc else go.Figure(), note, history)


@app.callback(
    Output("agent-q", "value"), Output("agent-out", "children"), Output("agent-store", "data"),
    Input("sel", "data"), Input("agent-btn", "n_clicks"),
    State("agent-q", "value"), prevent_initial_call=False,
)
def agent(sel: dict | None, _n: int, question: str | None):
    if not sel:
        return "", "", None
    sku, ret = b.clean_id(sel["sku"]), b.clean_id(sel["retailer"])
    default_q = (f"{sku} is short at {ret} and possibly other accounts. Recommend how to split the "
                 f"available supply and name which account absorbs the shortfall.")
    if ctx.triggered_id != "agent-btn":          # a new selection resets the panel
        return default_q, "", None
    q = (question or default_q).strip()[:600]
    try:
        r = b.ask_agent(q)
    except Exception as e:  # noqa: BLE001
        return q, alert(f"Agent call failed: {str(e)[:240]}", "danger"), None
    try:
        b.save_interaction(sku=sku, retailer=ret, planner_id=planner_id(), question=q,
                           answer=r["answer"], tools=r["tools"], trace_id=r["request_id"],
                           llm_endpoint=r["llm_endpoint"], prompt=r["prompt"],
                           in_tokens=r["input_tokens"], out_tokens=r["output_tokens"],
                           latency_ms=r["latency_ms"])
    except Exception as e:  # noqa: BLE001
        r["log_error"] = str(e)[:160]
    reco = None if r["tool_errors"] else b.recommended_units(r["allocation"], ret)
    out: list[Any] = []
    if r["tool_errors"]:
        out.append(alert([html.B(f"{len(r['tool_errors'])} tool call(s) failed. "),
                          "The answer below is not based on data. ", html.Br(),
                          html.Span(r["tool_errors"][0], className="mono")], "danger"))
    out += [dcc.Markdown(r["answer"] or "(no answer)", className="answer"),
                      html.Div([html.Span(t, className="chip") for t in r["tools"]], className="mt-2")]
    alloc = r["allocation"]
    if alloc and alloc.get("claims"):
        out.append(dbc.Table([
            html.Thead(html.Tr([html.Th(c) for c in
                                ("Account", "Needed", "Allocated", "Unmet", "Revenue protected")])),
            html.Tbody([html.Tr([html.Td(c["retailer_code"]), html.Td(num(c["units_needed"])),
                                 html.Td(num(c["units_allocated"])), html.Td(num(c["units_unmet"])),
                                 html.Td(money(c["revenue_protected_usd"]))])
                        for c in alloc["claims"]])],
            size="sm", className="mt-2 mb-1", style={"fontSize": "12.5px"}))
        out.append(html.Div(f"Allocator: {num(alloc.get('total_units_allocated'))} of "
                            f"{num(alloc.get('supply_available_units'))} available units placed"
                            + (" (supply constrained)" if alloc.get("is_constrained") else ""),
                            className="small-note"))
    else:
        out.append(html.Div("This answer did not include an allocation. Ask for a recommendation "
                            "to record a decision.", className="small-note"))
    out.append(html.Div(f"request id {r['request_id']} · {r['llm_endpoint']} · "
                        f"{num(r['input_tokens'])} in / {num(r['output_tokens'])} out tokens · "
                        f"{r['latency_ms'] / 1000:.1f}s", className="meta mono mt-1"))
    if r.get("log_error"):
        out.append(alert(f"Interaction not logged: {r['log_error']}", "warning"))
    return q, out, {"reco": reco, "request_id": r["request_id"], "sku": sku, "retailer": ret}


@app.callback(
    Output("dec-reco", "children"), Output("dec-units", "value"), Output("dec-save", "disabled"),
    Output("dec-action", "value"),
    Input("agent-store", "data"),
)
def decision_defaults(store: dict | None):
    if not store or store.get("reco") is None:
        return "Ask the agent for a recommendation first: the decision records what the allocator proposed.", None, True, "accept"
    return (f"Allocator recommends {store['reco']:,} units for this account.", store["reco"],
            False, "accept")


@app.callback(
    Output("dec-out", "children"), Output("saved", "data"),
    Input("dec-save", "n_clicks"),
    State("sel", "data"), State("agent-store", "data"), State("dec-action", "value"),
    State("dec-units", "value"), State("dec-why", "value"), State("saved", "data"),
    prevent_initial_call=True,
)
def save(_n: int, sel: dict | None, store: dict | None, action: str, units: Any, why: str | None,
         saved: int):
    if not sel or not store or store.get("reco") is None:
        return alert("Get an agent recommendation first.", "warning"), no_update
    if (store["sku"], store["retailer"]) != (sel["sku"], sel["retailer"]):
        return alert("The selection changed since the recommendation. Ask again.", "warning"), no_update
    reco = int(store["reco"])
    why = (why or "").strip()
    if action == "accept":
        planner_units = reco
    elif action == "defer":
        planner_units = 0                       # deferred: nothing committed this week
    else:
        if units is None or int(units) < 0:
            return alert("Enter the units to ship.", "warning"), no_update
        planner_units = int(units)
        if planner_units == reco:
            return alert("That equals the recommendation: record it as Accept.", "warning"), no_update
        if not why:
            return alert("An override needs a rationale: it is the data the next model learns from.",
                         "warning"), no_update
    risk, fc = b.sku_risk(sel["sku"], sel["retailer"]), b.sku_forecast(sel["sku"], sel["retailer"])
    if not risk or not fc:
        return alert("This series is no longer at risk.", "warning"), no_update
    try:
        did = b.save_decision(
            sku=sel["sku"], retailer=sel["retailer"], week_end=str(fc[0]["week_end_date"]),
            recommended=reco, planner_units=planner_units, action=action, planner_id=planner_id(),
            rationale=why or None, trace_id=store.get("request_id"),
            model_version=int(fc[0]["model_version"]), severity=float(risk["severity_score"]),
            units_at_risk=int(risk["units_at_risk"] or 0),
            revenue_at_risk=float(risk["revenue_at_risk_usd"] or 0))
    except Exception as e:  # noqa: BLE001
        return alert(f"Could not save: {str(e)[:240]}", "danger"), no_update
    delta = planner_units - reco
    return alert([html.B(f"Decision #{did} recorded"),
                  f" — {action}, {planner_units:,} units ({delta:+,} vs recommended). "
                  "Written to Lakebase; the writeback job syncs it to gold.fct_allocation_decisions."],
                 "success"), (saved or 0) + 1


@app.callback(Output("rt-out", "children"), Input("rt-btn", "n_clicks"), State("sel", "data"),
              prevent_initial_call=True)
def realtime(_n: int, sel: dict | None):
    if not sel:
        return ""
    try:
        rt = b.realtime_forecast(sel["sku"], sel["retailer"])
    except Exception as e:  # noqa: BLE001
        return alert(f"Real-time call failed: {str(e)[:260]}", "danger")
    batch = {r["horizon"]: r for r in b.sku_forecast(sel["sku"], sel["retailer"])}
    rows, worst = [], 0.0
    for p in rt["predictions"]:
        bt = batch.get(p["horizon"])
        d50 = abs(float(p["forecast_p50"]) - float(bt["forecast_p50"])) if bt else float("nan")
        worst = max(worst, d50) if d50 == d50 else worst
        rows.append(html.Tr([html.Td(p["horizon"]), html.Td(str(bt["week_end_date"]) if bt else "—"),
                             html.Td(num(bt["forecast_p50"], 1) if bt else "—"),
                             html.Td(num(p["forecast_p50"], 1)),
                             html.Td(num(bt["forecast_p90"], 1) if bt else "—"),
                             html.Td(num(p["forecast_p90"], 1))]))
    return [alert([html.B(f"{rt['latency_ms'] / 1000:.1f}s"), f" · {rt['endpoint']} · origin "
                   f"{rt['origin']} · max |p50 difference| vs batch = {worst:.2f} units"],
                  "success" if worst < 0.05 else "warning"),
            dbc.Table([html.Thead(html.Tr([html.Th(c) for c in
                       ("Wk", "Week ending", "p50 batch", "p50 live", "p90 batch", "p90 live")])),
                       html.Tbody(rows)], size="sm", style={"fontSize": "12px"})]


@app.callback(Output("genie-out", "children"), Input("genie-btn", "n_clicks"),
              Input("genie-q", "n_submit"), State("genie-q", "value"), prevent_initial_call=True)
def genie(_n: int, _s: int, q: str | None):
    if not (q or "").strip():
        return ""
    try:
        r = b.ask_genie(q.strip()[:500])
    except Exception as e:  # noqa: BLE001
        return alert(f"Genie call failed: {str(e)[:260]}", "danger")
    out: list[Any] = []
    if r["text"]:
        out.append(dcc.Markdown(r["text"], className="answer"))
    if r["rows"]:
        out.append(dash_table.DataTable(
            columns=[{"name": c, "id": c} for c in r["columns"]],
            data=[dict(zip(r["columns"], row, strict=False)) for row in r["rows"]],
            page_size=10, style_table={"overflowX": "auto"}, style_header={"fontWeight": 600},
            style_cell={"fontSize": "12.5px", "padding": "5px 8px", "textAlign": "left"}))
    if r["sql"]:
        out.append(html.Details([html.Summary("SQL Genie wrote", className="small-note"),
                                 html.Pre(r["sql"], className="sql")]))
    out.append(html.Div(f"{r['seconds']}s · Genie space {S.GENIE_SPACE_ID[:8]}…", className="meta"))
    return out


# --------------------------------------------------------------------------
# warm-up: the real-time endpoint scales to zero and takes minutes to wake
# --------------------------------------------------------------------------

def _warm() -> None:
    try:
        top = b.worklist(None, ["CRITICAL"], limit=1)[0]
        b.realtime_forecast(top["sku_id"], top["retailer_code"])
    except Exception:  # noqa: BLE001 - warming is best effort
        pass


if os.getenv("WARMUP", "1") == "1":
    threading.Thread(target=_warm, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("DATABRICKS_APP_PORT", "8080")), debug=False)
