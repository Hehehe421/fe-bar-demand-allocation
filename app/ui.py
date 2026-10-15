"""Shared components, formatting and charts for the Allocation Command Center.

Colour follows the dataviz skill: the two series colours were validated with
``validate_palette.js`` (adjacent CVD dE 24.7, normal-vision dE 33.6); status colours are the
reserved good / warning / serious / critical steps and always travel with an icon and a label;
sequential magnitude uses one blue ramp.
"""

from __future__ import annotations

from typing import Any

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import html
from narrative import money

SURFACE, GRID = "#ffffff", "#e8e7e3"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#7a7973"
SERIES1, SERIES2 = "#2a78d6", "#eb6834"
NAVY = "#0b1f3a"
STATUS = {"CRITICAL": "#d03b3b", "HIGH": "#ec835a", "MEDIUM": "#fab219", "LOW": "#0ca30c"}
GLYPH = {"CRITICAL": "▲", "HIGH": "◆", "MEDIUM": "●", "LOW": "○"}
LEVEL = {  # icon + label always shown with the colour
    "crit": ("#d03b3b", "✖"), "warn": ("#b45309", "▲"), "info": ("#2a78d6", "●"), "ok": ("#0a7d0a", "✔"),
}
BLUES = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]  # ramp steps


def num(v: Any, d: int = 0) -> str:
    return "—" if v is None else f"{float(v):,.{d}f}"


def pct(v: float | None, d: int = 1) -> str:
    return "—" if v is None else f"{v:.{d}f}%"


def tier_badge(t: str) -> html.Span:
    return html.Span([html.Span(GLYPH.get(t, "·"), className="g"), t], className=f"tier t-{t}")


def chip(level: str, text: str) -> html.Span:
    color, icon = LEVEL.get(level, LEVEL["info"])
    return html.Span([html.Span(icon, style={"color": color, "marginRight": "5px"}), text],
                     className="chip-s", style={"borderColor": color})


def alert(msg: Any, color: str = "secondary") -> dbc.Alert:
    return dbc.Alert(msg, color=color, className="py-2 mb-2", style={"fontSize": "13px"})


def kpi_card(label: str, value: str, sub: Any = None, status: tuple[str, str] | None = None,
             foot: Any = None) -> html.Div:
    return html.Div([
        html.Div(label, className="k-label"),
        html.Div(value, className="k-value"),
        html.Div(chip(*status), className="k-chip") if status else None,
        html.Div(sub, className="k-sub") if sub else None,
        html.Div(foot, className="k-foot") if foot else None,
    ], className="k-card")


def section(title: str, hint: Any = None, body: Any = None, class_name: str = "") -> html.Div:
    return html.Div([html.H2(title), html.Div(hint, className="hint") if hint else None, body],
                    className=f"card-s {class_name}")


# --------------------------------------------------------------------------
# charts
# --------------------------------------------------------------------------

def _style(fig: go.Figure, h: int, margin: dict | None = None) -> go.Figure:
    fig.update_layout(
        height=h, margin=margin or dict(l=8, r=12, t=8, b=8), paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font=dict(family="-apple-system, Segoe UI, Roboto, Arial", size=12, color=INK2),
        hoverlabel=dict(bgcolor=SURFACE, font_size=12, font_color=INK, bordercolor=GRID),
    )
    return fig


def empty_fig(msg: str = "No data", h: int = 200) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=msg, showarrow=False, font=dict(size=13, color=MUTED))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return _style(fig, h)


def bar_h(labels: list[str], values: list[float], texts: list[str], colors: str | list[str] = SERIES1,
          h: int = 170, hover: list[str] | None = None) -> go.Figure:
    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h", marker=dict(color=colors), text=texts, textposition="outside",
        textfont=dict(color=INK, size=12), cliponaxis=False,
        hovertext=hover, hoverinfo="text" if hover else "y+x"))
    fig.update_xaxes(visible=False, range=[0, max(values or [1]) * 1.3])
    fig.update_yaxes(showgrid=False, tickfont=dict(color=INK), autorange="reversed", automargin=True)
    fig.update_layout(bargap=0.4)
    return _style(fig, h, dict(l=8, r=24, t=8, b=8))


def trend_fig(weeks: list, fill: list[float], otif: list[float]) -> go.Figure:
    x = [str(w) for w in weeks]
    fig = go.Figure()
    for name, ys, color in (("Fill rate", fill, SERIES1), ("OTIF", otif, SERIES2)):
        fig.add_trace(go.Scatter(
            x=x, y=ys, name=name, mode="lines", line=dict(color=color, width=2.4),
            hovertemplate=f"{name} %{{y:.1f}}%<extra></extra>"))
        fig.add_trace(go.Scatter(x=[x[-1]], y=[ys[-1]], mode="markers", showlegend=False,
                                 marker=dict(size=9, color=color, line=dict(color=SURFACE, width=2)),
                                 hoverinfo="skip"))
        fig.add_annotation(x=x[-1], y=ys[-1], text=f"{ys[-1]:.1f}%", showarrow=False, xanchor="left",
                           xshift=10, font=dict(color=INK, size=12))
    fig.update_xaxes(showgrid=False, tickformat="%b %Y", linecolor=GRID, nticks=7)
    fig.update_yaxes(gridcolor=GRID, zeroline=False, ticksuffix="%", range=[min(otif + fill) - 3, 100])
    fig.update_layout(hovermode="x unified", legend=dict(orientation="h", y=1.12, x=0))
    return _style(fig, 290, dict(l=8, r=52, t=8, b=8))


def dumbbell_fig(accounts: list[dict[str, Any]]) -> go.Figure:
    """Delivered fill rate against the contractual commitment, per account."""
    rows = [a for a in accounts if a.get("commit") is not None]
    if not rows:
        return empty_fig("No commitment data")
    names = [a["account"] for a in rows]
    fig = go.Figure()
    for a in rows:
        fig.add_shape(type="line", y0=a["account"], y1=a["account"], x0=a["commit"], x1=a["fill"],
                      line=dict(color=GRID, width=5))
    fig.add_trace(go.Scatter(
        x=[a["commit"] for a in rows], y=names, mode="markers", name="Commitment",
        marker=dict(size=13, symbol="diamond-open", color=INK2, line=dict(width=2)),
        hovertemplate="%{y} commitment %{x:.0f}%<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=[a["fill"] for a in rows], y=names, mode="markers+text", name="Delivered",
        marker=dict(size=13, color=SERIES1, line=dict(color=SURFACE, width=2)),
        text=[f"{a['fill']:.1f}%" for a in rows], textposition="top center", textfont=dict(color=INK),
        hovertemplate="%{y} delivered %{x:.2f}%<extra></extra>"))
    lo = min(min(a["fill"], a["commit"]) for a in rows) - 1.5
    hi = max(max(a["fill"], a["commit"]) for a in rows) + 1.5
    fig.update_xaxes(range=[lo, hi], gridcolor=GRID, ticksuffix="%", zeroline=False)
    # pad the category axis so the value label above the first marker is not clipped
    fig.update_yaxes(showgrid=False, range=[len(names) - 0.45, -0.8], automargin=True)
    fig.update_layout(legend=dict(orientation="h", y=-0.25, x=0))
    return _style(fig, 250, dict(l=8, r=16, t=8, b=8))


def dc_map(dcs: list[dict[str, Any]]) -> go.Figure:
    """Exposure by distribution centre. Bubble area ~ revenue; colour = the same value on one ramp."""
    dcs = [d for d in dcs if d.get("lat") is not None]
    if not dcs:
        return empty_fig("No distribution-centre exposure", 360)
    mx = max(d["revenue"] for d in dcs) or 1.0
    fig = go.Figure(go.Scattergeo(
        lon=[d["lon"] for d in dcs], lat=[d["lat"] for d in dcs], mode="markers+text",
        text=[f"<b>{d['label'].replace(' DC', '')}</b><br>{money(d['revenue'])}" for d in dcs],
        textposition="bottom center", textfont=dict(color=INK, size=12),
        marker=dict(size=[22 + 46 * (d["revenue"] / mx) ** 0.5 for d in dcs],
                    color=[d["revenue"] for d in dcs], colorscale=[[0, BLUES[1]], [0.5, BLUES[3]], [1, BLUES[6]]],
                    cmin=0, cmax=mx, opacity=0.88, line=dict(color="#fff", width=2.5),
                    colorbar=dict(title=dict(text="Revenue<br>at risk", font=dict(size=11)), thickness=10,
                                  len=0.6, tickprefix="$", tickformat=",.0s")),
        customdata=[[d["city"], d["units"], d["critical"], d["skus"]] for d in dcs],
        hovertemplate="<b>%{text}</b><br>%{customdata[0]}<br>Units at risk %{customdata[1]:,.0f}<br>"
                      "Critical positions %{customdata[2]:,.0f}<br>%{customdata[3]:,} SKUs<extra></extra>"))
    # Albers USA (the scope default). Explicit lon/lat ranges and other projections were tried
    # and rendered blank in this plotly build; this one shows the whole continental US.
    fig.update_geos(scope="usa", projection_type="albers usa", showland=True, landcolor="#eef0f3",
                    showlakes=True, lakecolor="#ffffff", showsubunits=True, subunitcolor="#ffffff",
                    subunitwidth=1, bgcolor=SURFACE, showframe=False, showcoastlines=False, resolution=50)
    return _style(fig, 400, dict(l=0, r=0, t=0, b=0))


def forecast_fig(fc: list[dict[str, Any]], stockout_week: Any) -> go.Figure:
    x = [str(r["week_end_date"]) for r in fc]
    fig = go.Figure()
    for name, key, color in (("p90 (high case)", "forecast_p90", SERIES2),
                             ("p50 (expected)", "forecast_p50", SERIES1)):
        fig.add_trace(go.Scatter(
            x=x, y=[float(r[key]) for r in fc], name=name, mode="lines+markers",
            line=dict(color=color, width=2),
            marker=dict(size=8, color=color, line=dict(color=SURFACE, width=2)),
            hovertemplate=f"{name.split()[0]} %{{y:,.0f}} units<extra></extra>"))
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
    fig.update_layout(hovermode="x unified", legend=dict(orientation="h", y=-0.16, x=0))
    return _style(fig, 300, dict(l=8, r=60, t=34, b=8))
