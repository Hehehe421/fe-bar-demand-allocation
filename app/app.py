"""Allocation Command Center.

Three tabs for two audiences who use it daily:

* **Executive summary** (C-level): a computed headline, what needs attention, the biggest
  exposures with product art, and Genie for plain-English questions.
* **Performance & risk** (C-level and operations): governed KPIs, trends, forecast accuracy,
  and a map of where the at-risk demand is served from.
* **Forecast & decide** (operations): the ranked worklist, 13-week forecast, real-time check,
  the agent, and the decision that is written to Lakebase so it can be measured later.

    DATABRICKS_CONFIG_PROFILE=vm-summer-classic python app.py
"""

from __future__ import annotations

import csv
import io
import os
import threading
from typing import Any

import art
import backend as b
import dash
import dash_bootstrap_components as dbc
import narrative as nv
import settings as S
import ui
from dash import ALL, Input, Output, State, ctx, dash_table, dcc, html, no_update
from dash.dash_table.Format import Format, Group, Scheme
from dash.exceptions import PreventUpdate
from flask import request
from ui import GLYPH, GRID, INK, SERIES1, STATUS, SURFACE, alert, chip, money, num, pct, tier_badge

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP, dbc.icons.FONT_AWESOME],
    title="Allocation Command Center",
    suppress_callback_exceptions=True,
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
)
server = app.server

BRAND_CATEGORY = {"Snugglewick": "PLUSH", "Glowberry Pets": "PLUSH", "Titan Squad": "ACTION_FIGURE"}
STAGE_LABEL = {"EOL": "End of life", "LAUNCH": "Launch", "GROWTH": "Growth", "MATURE": "Mature",
               "DECLINE": "Decline"}

#: Questions proven against reference SQL (evidence/06_genie.md). Chips send these verbatim.
EXEC_QUESTIONS = [
    "Which licence family has the most revenue at risk?",
    "How many critical positions are there for each account?",
    "What is the revenue at risk by risk tier?",
    "How often does the P90 forecast cover actual demand for SOCIAL_TREND items?",
    "What is the total 13-week p50 forecast demand by brand?",
    "How many allocation decisions did planners accept, override and defer?",
]


@server.route("/healthz")
def healthz() -> tuple[str, int]:
    return "ok", 200


def planner_id() -> str:
    try:
        return request.headers.get("x-forwarded-email") or os.getenv("PLANNER_ID", "local-dev")
    except RuntimeError:
        return os.getenv("PLANNER_ID", "local-dev")


def _scope(v: str | None) -> str | None:
    return None if v in (None, "ALL") else v


def sku_card_info(row: dict[str, Any]) -> dict[str, Any]:
    """Catalog attributes for artwork; falls back to the risk row if the warehouse is cold."""
    try:
        info = b.sku_info([row["sku_id"]]).get(row["sku_id"])
    except Exception:  # noqa: BLE001
        info = None
    brand = (info or row).get("brand")
    return {
        "sku_id": row["sku_id"], "brand": brand,
        "category": (info or {}).get("category") or BRAND_CATEGORY.get(brand or "", "PLUSH"),
        "license_family": (info or row).get("license_family"),
        "lifecycle_stage": (info or row).get("lifecycle_stage"),
        "product_name": (info or {}).get("product_name"),
    }


def sku_image(card: dict[str, Any]) -> html.Img:
    svg = art.sku_svg(card["sku_id"], card["brand"], card["category"], card["license_family"],
                      card["lifecycle_stage"], card.get("product_name"))
    return html.Img(src=art.data_uri(svg), alt=f"{card['brand']} {card['category']} illustration",
                    title=card.get("product_name") or card["sku_id"])


def sku_title(card: dict[str, Any]) -> str:
    return art.display_name(card["brand"], card["category"], card.get("product_name"), card["sku_id"])


# --------------------------------------------------------------------------
# layout: header
# --------------------------------------------------------------------------

def header() -> html.Div:
    return html.Div(dbc.Row([
        dbc.Col([html.H1("Allocation Command Center", className="app-title"),
                 html.P("Know what will run out, what it is worth, and what to do about it.",
                        className="app-sub")], lg=5),
        dbc.Col(html.Div(id="asof", className="asof"), lg=3, className="d-flex align-items-center"),
        dbc.Col([
            html.Div(id="status", className="dots mb-1"),
            html.Div([
                dcc.Dropdown(id="scope", clearable=False, value="ALL", style={"minWidth": "170px"},
                             options=[{"label": "All accounts", "value": "ALL"}]
                             + [{"label": r, "value": r} for r in S.RETAILERS]),
                dbc.Button("Refresh data", id="refresh", size="sm", color="light", outline=True,
                           className="ms-2 no-print"),
            ], className="d-flex align-items-center justify-content-end"),
        ], lg=4),
    ], align="center"), className="app-header")


# --------------------------------------------------------------------------
# layout: executive tab
# --------------------------------------------------------------------------

def exec_tab() -> html.Div:
    return html.Div([
        dcc.Loading(html.Div(id="exec-hero"), type="circle", color="#2a78d6"),
        dbc.Row([
            dbc.Col(ui.section("What needs your attention",
                               "Computed from live data on every load: no language model writes these sentences.",
                               dcc.Loading(html.Div(id="exec-attn"), type="dot")), lg=7),
            dbc.Col(ui.section(
                "Ask the data, in plain English",
                "Governed Gold tables and metric views via Genie. Every answer shows the SQL it ran.",
                html.Div([
                    html.Div([html.Button(q, id={"type": "qchip", "i": i}, n_clicks=0, className="qchip")
                              for i, q in enumerate(EXEC_QUESTIONS)]),
                    dbc.InputGroup([
                        dbc.Input(id="genie-q", type="text", size="sm", n_submit=0,
                                  placeholder="Or type your own question"),
                        dbc.Button("Ask", id="genie-btn", color="primary", size="sm"),
                    ]),
                    dcc.Loading(html.Div(id="genie-out", className="mt-3"), type="circle"),
                ])), lg=5),
        ]),
        ui.section("Biggest exposures",
                   "The five positions with the most revenue at risk. Open one to decide.",
                   dcc.Loading(html.Div(id="exec-top"), type="dot")),
    ])


# --------------------------------------------------------------------------
# layout: performance tab
# --------------------------------------------------------------------------

def perf_tab() -> html.Div:
    return html.Div([
        dcc.Loading(dbc.Row(id="perf-kpis", className="g-3 mb-3"), type="circle"),
        dbc.Row([
            dbc.Col(ui.section("Service level, last 52 weeks",
                               "Fill rate (units shipped ÷ requested) and OTIF (on time and in full).",
                               dcc.Graph(id="perf-trend", config={"displayModeBar": False})), lg=8),
            dbc.Col(ui.section("Delivered vs. contractual commitment", "Per account, full history.",
                               dcc.Graph(id="perf-accts", config={"displayModeBar": False})), lg=4),
        ]),
        dbc.Row([
            dbc.Col(ui.section("Forecast accuracy by licence family",
                               "Weighted absolute % error on the 13-week holdout; lower is better. "
                               "The least accurate family is highlighted.",
                               dcc.Graph(id="perf-fam", config={"displayModeBar": False})), lg=6),
            dbc.Col(ui.section("Revenue at risk by tier", "Every at-risk position in scope.",
                               dcc.Graph(id="perf-tier", config={"displayModeBar": False})), lg=6),
        ]),
        dbc.Row([
            dbc.Col(ui.section(
                "Where the at-risk demand is served from",
                "Each at-risk position is spread across the distribution centres that served it over "
                "the last 13 weeks, in proportion to demand. DC locations are illustrative regional hubs.",
                dcc.Loading(dcc.Graph(id="perf-map", config={"displayModeBar": False}), type="circle")),
                lg=7),
            dbc.Col(ui.section("Distribution-centre exposure", None, html.Div(id="perf-dc")), lg=5),
        ]),
        ui.section("What stockouts have already cost",
                   "Business baseline measured over the 3-year history (synthetic data).",
                   html.Div(id="perf-base")),
    ])


# --------------------------------------------------------------------------
# layout: decide tab
# --------------------------------------------------------------------------

WORKLIST_COLS = [
    dict(name="Tier", id="tier_label"), dict(name="SKU", id="sku_id"),
    dict(name="Account", id="retailer_code"), dict(name="Family", id="license_family"),
    dict(name="Stockout", id="projected_stockout_week"),
    dict(name="Wks", id="weeks_to_stockout", type="numeric"),
    dict(name="Units", id="units_at_risk", type="numeric",
         format=Format(group=Group.yes, precision=0, scheme=Scheme.fixed)),
    dict(name="Revenue", id="revenue_at_risk_usd", type="numeric",
         format=Format(group=Group.yes, precision=0, scheme=Scheme.fixed).symbol_prefix("$")),
    dict(name="Sev.", id="severity_score", type="numeric", format=Format(precision=1, scheme=Scheme.fixed)),
    dict(name="Decision", id="decision"),
]


def decide_tab() -> html.Div:
    worklist_body = html.Div([
        dbc.Row([
            dbc.Col(dbc.Checklist(id="tiers", inline=True, value=["CRITICAL", "HIGH"],
                                  options=[{"label": f"{GLYPH[t]} {t}", "value": t} for t in S.TIERS],
                                  style={"fontSize": "13px"}), md=8),
            dbc.Col(dcc.Dropdown(id="fam", value="ALL", clearable=False, options=[
                {"label": "All licence families", "value": "ALL"},
                *[{"label": f.replace("_", " ").title(), "value": f}
                  for f in ("EVERGREEN", "FILM_TIE_IN", "SOCIAL_TREND")]]), md=4),
        ], className="mb-2 g-2"),
        dcc.Loading(dash_table.DataTable(
            id="worklist", columns=WORKLIST_COLS, data=[], row_selectable="single", selected_rows=[0],
            sort_action="native", page_action="native", page_size=12, style_table={"overflowX": "auto"},
            style_header={"backgroundColor": "#f3f5f9", "fontWeight": 600, "fontSize": "12px",
                          "fontFamily": "inherit", "whiteSpace": "nowrap", "border": "none",
                          "borderBottom": f"1px solid {GRID}"},
            style_cell={"fontSize": "13px", "padding": "6px 8px", "border": "none",
                        "borderBottom": f"1px solid {GRID}", "textAlign": "left", "backgroundColor": SURFACE,
                        "color": INK, "whiteSpace": "nowrap", "fontFamily": "inherit"},
            style_cell_conditional=[{"if": {"column_type": "numeric"}, "textAlign": "right"}],
            style_data_conditional=[
                {"if": {"filter_query": f'{{risk_tier}} = "{t}"', "column_id": "tier_label"},
                 "borderLeft": f"5px solid {c}", "fontWeight": 600} for t, c in STATUS.items()
            ] + [{"if": {"state": "selected"}, "backgroundColor": "#eaf2fc", "border": "none"}],
        ), type="circle"),
        html.Div([html.Span(id="worklist-note", className="small-note"),
                  dbc.Button("Export CSV", id="export-btn", size="sm", color="secondary", outline=True,
                             className="float-end no-print"), dcc.Download(id="export-dl")], className="mt-2"),
    ])
    left = [
        ui.section("At-risk worklist",
                   "Ranked by severity from Lakebase. Select a row for the detail. Account scoping is "
                   "applied in this query: Unity Catalog will not put a row filter on a Lakebase copy.",
                   worklist_body),
        ui.section("Decision log", "Most recent decisions recorded in Lakebase.", html.Div(id="dec-log")),
    ]
    right = [
        ui.section("", None, html.Div([
            html.Div(id="detail-head"), dcc.Graph(id="forecast-graph", config={"displayModeBar": False}),
            html.Div(id="forecast-note", className="small-note")])),
        ui.section("Real-time check",
                   "Re-forecast this series live on the real-time endpoint and compare it with the batch "
                   "forecast above. Same model, two serving paths.",
                   html.Div([
                       dbc.Button("Re-forecast now", id="rt-btn", color="primary", outline=True, size="sm"),
                       html.Span("  The first call after idle can take 1–2 minutes while the endpoint scales "
                                 "up from zero (measured: 52 s and 138 s).", className="small-note ms-2"),
                       dcc.Loading(html.Div(id="rt-out", className="mt-2"), type="circle")])),
        ui.section("Agent",
                   "Calls the live allocation agent. It reads Unity Catalog through tools and quotes the "
                   "deterministic allocator; the request id links a decision back to the brief. After "
                   "idle the first call can take 3–4 minutes while the endpoint scales up (measured: 217 s).",
                   html.Div([
                       dcc.Textarea(id="agent-q", style={"width": "100%", "height": "70px", "fontSize": "13px"}),
                       dbc.Button("Ask the agent", id="agent-btn", color="primary", size="sm", className="mt-1"),
                       dcc.Loading(html.Div(id="agent-out", className="mt-2"), type="circle")])),
        ui.section("Decide", None, html.Div([
            html.Div(id="dec-reco", className="hint"),
            dbc.RadioItems(id="dec-action", inline=True, value="accept", className="mb-2",
                           options=[{"label": "Accept", "value": "accept"},
                                    {"label": "Override", "value": "override"},
                                    {"label": "Defer", "value": "defer"}], style={"fontSize": "13px"}),
            dbc.Row([
                dbc.Col([html.Label("Units to ship", className="small-note"),
                         dbc.Input(id="dec-units", type="number", min=0, step=1, size="sm")], width=4),
                dbc.Col([html.Label("Rationale", className="small-note"),
                         dbc.Input(id="dec-why", type="text", size="sm",
                                   placeholder="why (required for an override)")], width=8),
            ], className="mb-2"),
            dbc.Button("Record decision", id="dec-save", color="success", size="sm", disabled=True),
            html.Div(id="dec-out", className="mt-2"), html.Div(id="dec-history", className="mt-2")])),
    ]
    return html.Div(dbc.Row([dbc.Col(left, lg=7), dbc.Col(right, lg=5)]))


app.layout = html.Div([
    dcc.Store(id="sel"), dcc.Store(id="agent-store"), dcc.Store(id="saved", data=0),
    dcc.Interval(id="tick", interval=60_000),
    header(),
    dbc.Container([
        dbc.Tabs([
            dbc.Tab(exec_tab(), label="Executive summary", tab_id="tab-exec"),
            dbc.Tab(perf_tab(), label="Performance & risk", tab_id="tab-perf"),
            dbc.Tab(decide_tab(), label="Forecast & decide", tab_id="tab-decide"),
        ], id="tabs", active_tab="tab-exec", class_name="main-tabs"),
        html.P("All data is synthetic. Forecast: ml.demand_forecaster (batch) and ml.demand_forecaster_rt "
               "(real-time). Product images are generated illustrations, not photographs. Decisions are "
               "written to Lakebase and synced back to Unity Catalog.", className="small-note mb-4"),
    ], fluid=True),
])


# --------------------------------------------------------------------------
# header callbacks
# --------------------------------------------------------------------------

@app.callback(Output("status", "children"), Input("tick", "n_intervals"), Input("refresh", "n_clicks"))
def render_status(_n: int, _r: int):
    st = b.service_status()
    return [html.Span([html.Span(className=f"dot {v['state']}"), f"{k}: {v['note']}"], className="dot-item")
            for k, v in st.items()]


@app.callback(Output("saved", "data", allow_duplicate=True), Input("refresh", "n_clicks"),
              State("saved", "data"), prevent_initial_call=True)
def refresh(_n: int, saved: int):
    b.clear_caches()
    return (saved or 0) + 1


# --------------------------------------------------------------------------
# executive tab callbacks
# --------------------------------------------------------------------------

def _hero(ex: dict[str, Any]) -> html.Div:
    headline, sub = nv.headline(ex)
    t = nv.totals(ex)
    cov = ex["coverage"]
    crit, decided = int(cov["critical"] or 0), int(cov["decided"] or 0)
    first = cov.get("first_stockout")
    metrics = [
        ("Revenue at risk", money(t["rev"]), f"{num(t['units'])} units"),
        ("Critical positions", f"{crit:,}", f"{money(t['critical_rev'])} of the exposure"),
        ("Earliest stockout", first.strftime("%d %b") if first else "—", "week ending"),
        ("Decision coverage", f"{(100 * decided / crit if crit else 0):.0f}%",
         f"{decided} of {crit} critical positions"),
    ]
    return html.Div([
        html.Div("Executive summary", className="eyebrow"),
        html.H1(headline), html.Div(sub, className="lede"),
        html.Div([html.Div([html.Div(label, className="l"), html.Div(v, className="v"), html.Div(s, className="s")],
                           className="hm") for label, v, s in metrics], className="hero-metrics"),
    ], className="hero")


def _insights(items: list[dict[str, str]]) -> Any:
    if not items:
        return alert("Nothing needs attention.", "success")
    out = []
    for i in items:
        color, icon = ui.LEVEL[i["level"]]
        out.append(html.Div([html.Div(icon, className="ic", style={"color": color}),
                             html.Div([html.Span(i["lead"] + " ", className="ins-lead"),
                                       html.Span(i["text"], className="tx")])], className="ins"))
    return out


def _top_cards(rows: list[dict[str, Any]]) -> Any:
    if rows:
        try:
            b.sku_info([r["sku_id"] for r in rows])      # one query warms the cache for all five
        except Exception:  # noqa: BLE001
            pass
    cards = []
    for r in rows:
        card = sku_card_info(r)
        cards.append(html.Div([
            sku_image(card),
            html.Div(sku_title(card), className="nm"),
            html.Div(f"{r['retailer_code']} · {r['sku_id']}", className="sub"),
            html.Div(money(r["rev"]), className="rev"),
            html.Div([tier_badge(r["risk_tier"]),
                      html.Span(f" · stockout in {r['weeks_to_stockout']} wk", className="sub")]),
            html.Div(chip("ok", f"Decided: {r['last_action']}") if r.get("last_action")
                     else chip("crit", "No decision yet")),
            dbc.Button("Review →", id={"type": "open", "sku": r["sku_id"], "ret": r["retailer_code"]},
                       size="sm", color="primary", outline=True, className="mt-1 no-print"),
        ], className="pcard"))
    return html.Div(cards, className="top-grid")


@app.callback(
    Output("exec-hero", "children"), Output("exec-attn", "children"), Output("exec-top", "children"),
    Output("asof", "children"), Input("scope", "value"), Input("saved", "data"),
)
def render_exec(scope: str, _saved: int):
    sc = _scope(scope)
    try:
        ex = b.exec_data(sc)
    except Exception as e:  # noqa: BLE001
        return alert(f"Lakebase unavailable: {str(e)[:200]}", "danger"), "", "", ""
    perf = dc = None
    try:
        perf, dc = b.performance(sc), b.dc_exposure(sc)
    except Exception:  # noqa: BLE001 - insights degrade gracefully without the warehouse
        pass
    fc = ex["forecast"]
    asof = (f"Forecast origin {fc['origin']:%d %b %Y} · model v{fc['model_version']} ({fc['algorithm']})"
            if fc and fc.get("origin") else "")
    return _hero(ex), _insights(nv.insights(ex, perf, dc)), _top_cards(ex["top"]), asof


@app.callback(
    Output("tabs", "active_tab"), Output("sel", "data", allow_duplicate=True),
    Output("worklist", "selected_rows", allow_duplicate=True),
    Input({"type": "open", "sku": ALL, "ret": ALL}, "n_clicks"), prevent_initial_call=True,
)
def open_sku(clicks: list[int]):
    tid = ctx.triggered_id
    if not isinstance(tid, dict) or not any(clicks):
        raise PreventUpdate
    # clear the worklist radio so it cannot point at a different row than the panel shows
    return "tab-decide", {"sku": tid["sku"], "retailer": tid["ret"]}, []


@app.callback(
    Output("genie-out", "children"), Output("genie-q", "value"),
    Input({"type": "qchip", "i": ALL}, "n_clicks"), Input("genie-btn", "n_clicks"),
    Input("genie-q", "n_submit"), State("genie-q", "value"), prevent_initial_call=True,
)
def genie(_chips: list[int], _btn: int, _sub: int, typed: str | None):
    tid = ctx.triggered_id
    if isinstance(tid, dict):
        if not (ctx.triggered and ctx.triggered[0]["value"]):
            raise PreventUpdate
        q = EXEC_QUESTIONS[tid["i"]]
    else:
        q = (typed or "").strip()
    if not q:
        raise PreventUpdate
    try:
        r = b.ask_genie(q[:500])
    except Exception as e:  # noqa: BLE001
        return alert(f"Genie call failed: {str(e)[:260]}", "danger"), q
    out: list[Any] = [html.Div([html.Span("You asked: ", className="small-note"), html.B(q)], className="mb-2")]
    if r["text"]:
        out.append(dcc.Markdown(r["text"], className="answer"))
    if r["rows"]:
        out.append(dash_table.DataTable(
            columns=[{"name": c, "id": c} for c in r["columns"]],
            data=[dict(zip(r["columns"], row, strict=False)) for row in r["rows"]],
            page_size=8, style_table={"overflowX": "auto"}, style_header={"fontWeight": 600},
            style_cell={"fontSize": "12.5px", "padding": "5px 8px", "textAlign": "left"}))
    if r["sql"]:
        out.append(html.Details([html.Summary("SQL Genie wrote", className="small-note"),
                                 html.Pre(r["sql"], className="sql")]))
    out.append(html.Div(f"{r['seconds']}s · governed data, answered by Genie", className="meta"))
    return out, q


# --------------------------------------------------------------------------
# performance tab callbacks
# --------------------------------------------------------------------------

@app.callback(
    Output("perf-kpis", "children"), Output("perf-trend", "figure"), Output("perf-accts", "figure"),
    Output("perf-fam", "figure"), Output("perf-tier", "figure"), Output("perf-map", "figure"),
    Output("perf-dc", "children"), Output("perf-base", "children"),
    Input("tabs", "active_tab"), Input("scope", "value"), Input("saved", "data"),
)
def render_perf(tab: str, scope: str, _saved: int):
    if tab != "tab-perf":
        raise PreventUpdate
    sc = _scope(scope)
    err = None
    try:
        perf, k = b.performance(sc), b.kpis(sc)
    except Exception as e:  # noqa: BLE001
        perf, k, err = None, {}, str(e)[:200]
    ex = b.exec_data(sc)
    try:
        dc = b.dc_exposure(sc)
    except Exception:  # noqa: BLE001
        dc = []

    accts_perf = (perf or {}).get("accounts", [])
    commits = {a["account"]: a.get("commit") for a in accts_perf}
    known = sorted(v for v in commits.values() if v)
    trend_cov = k.get("p90_cov_trend")
    if sc is None:
        commit_text = (f"Contractual commitments {known[0]:.0f}–{known[-1]:.0f}% by account"
                       if known else None)
    else:
        commit_text = f"Contractual commitment {commits[sc]:.0f}%" if commits.get(sc) else None
    cards = [
        dbc.Col(ui.kpi_card("Fill rate", pct(k.get("fill_rate"), 2), commit_text,
                            nv.fill_status(k.get("fill_rate"), accts_perf, sc), "Units shipped ÷ units requested"),
                md=6, xl=3),
        dbc.Col(ui.kpi_card("OTIF", pct(k.get("otif"), 2), "Lines on time and in full", ("info", "Measured"),
                            "On time 92.7% · in full 93.0% (baseline)"), md=6, xl=3),
        dbc.Col(ui.kpi_card("Forecast error (WAPE)", pct(k.get("wape")),
                            "vs 95.3% for a naive 'same as last week' forecast",
                            nv.kpi_status("wape_vs_naive", k.get("wape"), 95.3),
                            f"Trend-driven items: {pct(k.get('wape_trend'))}"), md=6, xl=3),
        dbc.Col(ui.kpi_card("High-case forecast coverage", pct(k.get("p90_cov")),
                            "Share of actual demand under the p90 forecast (target 90%)",
                            nv.kpi_status("coverage", k.get("p90_cov")),
                            html.Span(["Trend-driven items: ", html.B(pct(trend_cov)),
                                       " — exposure there is likely understated"])), md=6, xl=3),
    ]
    if err:
        cards.insert(0, dbc.Col(alert(f"Warehouse KPIs unavailable: {err}", "warning"), width=12))

    trend = (ui.trend_fig(perf["weeks"], perf["fill"], perf["otif"]) if perf and perf["weeks"]
             else ui.empty_fig("Trend unavailable", 290))
    accts = ui.dumbbell_fig(perf["accounts"]) if perf else ui.empty_fig("Unavailable", 220)

    fams = (perf or {}).get("families", [])
    if fams:
        worst = max(fams, key=lambda f: f["wape"])
        fam_fig = ui.bar_h(
            [f"{f['family'].replace('_', ' ').title()}<br><span style='font-size:11px;color:#7a7973'>"
             f"high-case coverage {f['p90_cov']:.0f}%</span>" for f in fams], [f["wape"] for f in fams],
            [f"{f['wape']:.1f}%" for f in fams],
            [ui.SERIES2 if f is worst else SERIES1 for f in fams], 220,
            [f"{f['family']}: WAPE {f['wape']:.1f}%, bias {f['bias']:+.1f}%, p90 coverage {f['p90_cov']:.1f}%"
             for f in fams])
    else:
        fam_fig = ui.empty_fig("Unavailable", 200)

    tiers = {t["risk_tier"]: t for t in ex["tiers"]}
    order = [t for t in S.TIERS if t in tiers]
    tier_fig = (ui.bar_h([f"{GLYPH[t]} {t}" for t in order], [float(tiers[t]["rev"]) for t in order],
                         [f"{money(float(tiers[t]['rev']))}  ·  {int(tiers[t]['n']):,} positions" for t in order],
                         [STATUS[t] for t in order], 220)
                if order else ui.empty_fig("No risk in scope", 220))

    total = sum(d["revenue"] for d in dc) or 1.0
    dc_tbl = (dbc.Table([
        html.Thead(html.Tr([html.Th(c) for c in ("Distribution centre", "Revenue at risk", "Share",
                                                 "Critical", "SKUs")])),
        html.Tbody([html.Tr([html.Td([html.B(d["label"]), html.Div(d["city"], className="small-note")]),
                             html.Td(money(d["revenue"])), html.Td(f"{100 * d['revenue'] / total:.0f}%"),
                             html.Td(num(d["critical"])), html.Td(f"{d['skus']:,}")]) for d in dc]),
    ], size="sm", style={"fontSize": "13px"}) if dc else alert("Distribution-centre view unavailable.", "warning"))

    bl = b.baseline()

    def bv(key: str, fmt: str = "money") -> str:
        v = float(bl[key]["value"]) if key in bl else None
        return "—" if v is None else (money(v) if fmt == "money" else f"{v:,.2f}")

    def bp(key: str) -> str:
        return pct(float(bl[key]["value"])) if key in bl else "—"

    base = dbc.Row([
        dbc.Col(ui.kpi_card("Sales lost to stockouts", bv("lost_revenue_usd"),
                            f"{bp('lost_units_pct_of_demand')} of demand"), md=6, xl=3),
        dbc.Col(ui.kpi_card("Markdown loss on excess", bv("markdown_loss_usd"),
                            f"{bp('markdown_loss_pct_of_invoiced')} of invoiced"), md=6, xl=3),
        dbc.Col(ui.kpi_card("Air-freight expedites", bv("air_freight_usd"),
                            f"{bv('air_vs_ocean_freight_premium_x', 'x')}× the ocean rate per unit"), md=6, xl=3),
        dbc.Col(ui.kpi_card("Inventory turns", bv("inventory_turns_annual", "x"),
                            f"{bv('weeks_of_supply', 'x')} weeks of supply"), md=6, xl=3),
    ], className="g-3")
    return cards, trend, accts, fam_fig, tier_fig, ui.dc_map(dc), dc_tbl, base


# --------------------------------------------------------------------------
# decide tab callbacks
# --------------------------------------------------------------------------

@app.callback(
    Output("worklist", "data"), Output("worklist", "selected_rows"), Output("worklist-note", "children"),
    Input("scope", "value"), Input("tiers", "value"), Input("fam", "value"), Input("saved", "data"),
)
def render_worklist(scope: str, tiers: list[str], fam: str, _saved: int):
    rows = b.worklist(_scope(scope), tiers or [], family=None if fam == "ALL" else fam)
    data = [{**r, "tier_label": f"{GLYPH[r['risk_tier']]} {r['risk_tier']}",
             "projected_stockout_week": str(r["projected_stockout_week"]),
             "decision": r["last_action"] or "—"} for r in rows]
    note = f"Showing the top {len(data)} by severity." if len(data) >= 250 else f"{len(data)} positions."
    keep = no_update if ctx.triggered_id == "saved" else ([0] if data else [])
    return data, keep, note


@app.callback(Output("export-dl", "data"), Input("export-btn", "n_clicks"), State("scope", "value"),
              State("tiers", "value"), State("fam", "value"), prevent_initial_call=True)
def export_csv(_n: int, scope: str, tiers: list[str], fam: str):
    rows = b.worklist(_scope(scope), tiers or [], limit=5000, family=None if fam == "ALL" else fam)
    buf = io.StringIO()
    cols = ["sku_id", "retailer_code", "brand", "license_family", "lifecycle_stage", "risk_tier",
            "severity_score", "projected_stockout_week", "weeks_to_stockout", "units_at_risk",
            "revenue_at_risk_usd", "last_action"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return dcc.send_string(buf.getvalue(), "at_risk_worklist.csv")


@app.callback(Output("sel", "data"), Input("worklist", "selected_rows"), State("worklist", "data"))
def select_row(selected: list[int], data: list[dict]):
    if not selected or not data or selected[0] >= len(data):
        raise PreventUpdate
    r = data[selected[0]]
    return {"sku": r["sku_id"], "retailer": r["retailer_code"]}


@app.callback(
    Output("detail-head", "children"), Output("forecast-graph", "figure"), Output("forecast-note", "children"),
    Output("dec-history", "children"), Input("sel", "data"), Input("saved", "data"),
)
def render_detail(sel: dict | None, _saved: int):
    if not sel:
        return html.Div("Select a position from the worklist."), ui.empty_fig("No selection", 300), "", ""
    sku, ret = sel["sku"], sel["retailer"]
    risk, fc, hist = b.sku_risk(sku, ret), b.sku_forecast(sku, ret), b.sku_decisions(sku, ret)
    if not risk:
        return alert(f"{sku} at {ret} is no longer at risk."), ui.empty_fig("", 300), "", ""
    card = sku_card_info(risk)
    head = html.Div([
        sku_image(card),
        html.Div([
            html.Div(sku_title(card), className="nm"),
            html.Div(f"{sku} · {ret} · {(card['license_family'] or '').replace('_', ' ').title()} · "
                     f"{STAGE_LABEL.get(card['lifecycle_stage'] or '', card['lifecycle_stage'] or '')}", className="fact"),
            html.Div([tier_badge(risk["risk_tier"]), html.Span("   Severity ", className="fact"),
                      html.B(num(risk["severity_score"], 1)), html.Span("  ·  stockout in ", className="fact"),
                      html.B(f"{risk['weeks_to_stockout']} wk"),
                      html.Span(f" ({risk['projected_stockout_week']})", className="fact")]),
            html.Div([html.Span("Units at risk ", className="fact"), html.B(num(risk["units_at_risk"])),
                      html.Span("  ·  Revenue ", className="fact"), html.B(money(risk["revenue_at_risk_usd"])),
                      html.Span("  ·  On hand ", className="fact"), html.B(num(risk["sku_on_hand_units"])),
                      html.Span("  ·  Inbound ", className="fact"), html.B(num(risk["sku_inbound_horizon_units"]))]),
        ]),
    ], className="sku-head")
    note = (f"Batch forecast: {fc[0]['algorithm']} · ml.demand_forecaster v{fc[0]['model_version']} "
            f"@champion · scored with fe.score_batch." if fc else "No forecast rows.")
    rows = [html.Div(f"{h['decided_at']:%d %b %H:%M} · {h['action']} · {h['planner_units']} units "
                     f"(recommended {h['recommended_units']}) · {h['planner_id']}", className="meta") for h in hist]
    history = html.Div([html.Div("Earlier decisions on this series", className="small-note"), *rows]) if rows else ""
    fig = ui.forecast_fig(fc, risk["projected_stockout_week"]) if fc else ui.empty_fig("No forecast", 300)
    return head, fig, note, history


@app.callback(
    Output("agent-q", "value"), Output("agent-out", "children"), Output("agent-store", "data"),
    Input("sel", "data"), Input("agent-btn", "n_clicks"), State("agent-q", "value"),
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
        b.save_interaction(sku=sku, retailer=ret, planner_id=planner_id(), question=q, answer=r["answer"],
                           tools=r["tools"], trace_id=r["request_id"], llm_endpoint=r["llm_endpoint"],
                           prompt=r["prompt"], in_tokens=r["input_tokens"], out_tokens=r["output_tokens"],
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
            html.Thead(html.Tr([html.Th(c) for c in ("Account", "Needed", "Allocated", "Unmet", "Revenue protected")])),
            html.Tbody([html.Tr([html.Td(c["retailer_code"]), html.Td(num(c["units_needed"])),
                                 html.Td(num(c["units_allocated"])), html.Td(num(c["units_unmet"])),
                                 html.Td(money(c["revenue_protected_usd"]))]) for c in alloc["claims"]])],
            size="sm", className="mt-2 mb-1", style={"fontSize": "12.5px"}))
        out.append(html.Div(f"Allocator: {num(alloc.get('total_units_allocated'))} of "
                            f"{num(alloc.get('supply_available_units'))} available units placed"
                            + (" (supply constrained)" if alloc.get("is_constrained") else ""),
                            className="small-note"))
    else:
        out.append(html.Div("This answer did not include an allocation. Ask for a recommendation to "
                            "record a decision.", className="small-note"))
    out.append(html.Div(f"request id {r['request_id']} · {r['llm_endpoint']} · "
                        f"{num(r['input_tokens'])} in / {num(r['output_tokens'])} out tokens · "
                        f"{r['latency_ms'] / 1000:.1f}s", className="meta mono mt-1"))
    if r.get("log_error"):
        out.append(alert(f"Interaction not logged: {r['log_error']}", "warning"))
    return q, out, {"reco": reco, "request_id": r["request_id"], "sku": sku, "retailer": ret}


@app.callback(
    Output("dec-reco", "children"), Output("dec-units", "value"), Output("dec-save", "disabled"),
    Output("dec-action", "value"), Input("agent-store", "data"),
)
def decision_defaults(store: dict | None):
    if not store or store.get("reco") is None:
        return ("Ask the agent for a recommendation first: the decision records what the allocator "
                "proposed.", None, True, "accept")
    return f"Allocator recommends {store['reco']:,} units for this account.", store["reco"], False, "accept"


@app.callback(
    Output("dec-out", "children"), Output("saved", "data"),
    Input("dec-save", "n_clicks"),
    State("sel", "data"), State("agent-store", "data"), State("dec-action", "value"),
    State("dec-units", "value"), State("dec-why", "value"), State("saved", "data"),
    prevent_initial_call=True,
)
def save(_n: int, sel: dict | None, store: dict | None, action: str, units: Any, why: str | None, saved: int):
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
            sku=sel["sku"], retailer=sel["retailer"], week_end=str(fc[0]["week_end_date"]), recommended=reco,
            planner_units=planner_units, action=action, planner_id=planner_id(), rationale=why or None,
            trace_id=store.get("request_id"), model_version=int(fc[0]["model_version"]),
            severity=float(risk["severity_score"]), units_at_risk=int(risk["units_at_risk"] or 0),
            revenue_at_risk=float(risk["revenue_at_risk_usd"] or 0))
    except Exception as e:  # noqa: BLE001
        return alert(f"Could not save: {str(e)[:240]}", "danger"), no_update
    return alert([html.B(f"Decision #{did} recorded"),
                  f" — {action}, {planner_units:,} units ({planner_units - reco:+,} vs recommended). "
                  "Written to Lakebase; the writeback job syncs it to gold.fct_allocation_decisions."],
                 "success"), (saved or 0) + 1


@app.callback(Output("dec-log", "children"), Input("tabs", "active_tab"), Input("saved", "data"))
def render_log(tab: str, _saved: int):
    if tab != "tab-decide":
        raise PreventUpdate
    try:
        st, log = b.decision_stats_full(), b.decision_log(8)
    except Exception as e:  # noqa: BLE001
        return alert(f"Decision log unavailable: {str(e)[:160]}", "warning")
    n = int(st["n"]) or 1
    summary = html.Div([
        chip("info", f"{int(st['n'])} decisions"),
        chip("ok", f"{int(st['accepted'])} accepted ({100 * int(st['accepted']) / n:.0f}%)"),
        chip("warn", f"{int(st['overridden'])} overridden ({100 * int(st['overridden']) / n:.0f}%)"),
        chip("info", f"{int(st['deferred'])} deferred"),
        chip("info", f"net {int(st['net_units']):+,} units from overrides"),
    ], className="mb-2")
    nw = {"whiteSpace": "nowrap"}
    rows = [html.Tr([html.Td(f"{d['decided_at']:%d %b %H:%M}", style=nw),
                     html.Td(f"{d['sku_id']} · {d['retailer_code']}", style=nw),
                     html.Td(d["action"]), html.Td(f"{d['planner_units']:,} / {d['recommended_units']:,}"),
                     html.Td(d["planner_id"]), html.Td(d["why"] or "—")]) for d in log]
    table = dbc.Table([html.Thead(html.Tr([html.Th(c) for c in
                                           ("When", "Position", "Action", "Shipped / recommended", "By", "Why")])),
                       html.Tbody(rows)], size="sm", style={"fontSize": "12.5px"})
    return [summary, table]


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
                             html.Td(num(bt["forecast_p50"], 1) if bt else "—"), html.Td(num(p["forecast_p50"], 1)),
                             html.Td(num(bt["forecast_p90"], 1) if bt else "—"), html.Td(num(p["forecast_p90"], 1))]))
    return [alert([html.B(f"{rt['latency_ms'] / 1000:.1f}s"), f" · {rt['endpoint']} · origin {rt['origin']} · "
                   f"max |p50 difference| vs batch = {worst:.2f} units"], "success" if worst < 0.05 else "warning"),
            dbc.Table([html.Thead(html.Tr([html.Th(c) for c in
                       ("Wk", "Week ending", "p50 batch", "p50 live", "p90 batch", "p90 live")])),
                       html.Tbody(rows)], size="sm", style={"fontSize": "12px"})]


# --------------------------------------------------------------------------
# warm-up: caches and the scale-to-zero forecaster
# --------------------------------------------------------------------------

def _warm() -> None:
    def safe(fn, *a):
        try:
            fn(*a)
        except Exception:  # noqa: BLE001 - warming is best effort
            pass
    safe(b.exec_data, None)
    safe(b.performance, None)
    safe(b.dc_exposure, None)
    safe(b.kpis, None)
    safe(b.service_status)
    # wake the scale-to-zero agent with a question that needs no tools (cheap), in parallel
    threading.Thread(target=safe, args=(b.ask_agent, "Reply with the single word: ready."), daemon=True).start()
    try:
        top = b.worklist(None, ["CRITICAL"], limit=1)[0]
        b.realtime_forecast(top["sku_id"], top["retailer_code"])
    except Exception:  # noqa: BLE001
        pass


if os.getenv("WARMUP", "1") == "1":
    threading.Thread(target=_warm, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("DATABRICKS_APP_PORT", "8080")), debug=False)
