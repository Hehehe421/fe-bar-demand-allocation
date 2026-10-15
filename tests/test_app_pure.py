"""Unit tests for the app's pure logic. No network, no workspace.

    python tests/test_app_pure.py        (also collected by pytest)

Covers what must not silently change: the computed executive narrative (a wrong sentence in
front of a C-level is worse than a missing one), the KPI status rules, and the generated
SKU art (valid SVG, deterministic, and consistent with the caption).
"""

from __future__ import annotations

import pathlib
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))

import art  # noqa: E402
import narrative as nv  # noqa: E402

EX = {
    "tiers": [{"risk_tier": "CRITICAL", "n": 434, "rev": 509958.0, "units": 90000.0},
              {"risk_tier": "LOW", "n": 2582, "rev": 871434.0, "units": 60252.0}],
    "families": [{"license_family": "SOCIAL_TREND", "n": 645, "rev": 579004.0, "critical": 200}],
    "accounts": [{"retailer_code": "ETAIL_C", "n": 1, "rev": 549000.0, "critical": 147},
                 {"retailer_code": "MASS_A", "n": 1, "rev": 400000.0, "critical": 140}],
    "coverage": {"critical": 434, "soon": 434, "decided": 24},
}
PERF = {"families": [{"family": "SOCIAL_TREND", "wape": 109.9, "bias": -12.0, "p90_cov": 74.1, "units": 1.0}],  # noqa: E501
        "accounts": [{"account": "ETAIL_C", "fill": 96.46, "commit": 93.0},
                     {"account": "MASS_A", "fill": 96.34, "commit": 97.0}]}
DC = [{"dc": "DC_WEST", "label": "West Coast DC", "revenue": 506000.0},
      {"dc": "DC_EAST", "label": "East Coast DC", "revenue": 483000.0}]


def test_money_formats():
    assert nv.money(1_381_392) == "$1.38M"
    assert nv.money(509_958) == "$510K"
    assert nv.money(950) == "$950"
    assert nv.money(None) == "—"


def test_totals_and_headline():
    t = nv.totals(EX)
    assert t["rows"] == 3016 and t["critical"] == 434
    assert abs(t["rev"] - 1_381_392) < 1
    h, sub = nv.headline(EX)
    assert "$1.38M" in h and "3,016" in h
    assert "434 are critical" in sub and "all of them" in sub      # soon == critical


def test_headline_partial_timing():
    ex = {**EX, "coverage": {"critical": 434, "soon": 100, "decided": 24}}
    assert "100 of them" in nv.headline(ex)[1]


def test_headline_empty():
    h, _ = nv.headline({"tiers": [], "coverage": {"critical": 0, "soon": 0, "decided": 0}})
    assert h.startswith("No stockout risk")


def test_insights_flag_the_real_problems():
    items = nv.insights(EX, PERF, DC)
    text = " ".join(i["lead"] + " " + i["text"] for i in items)
    assert "24 of 434" in text and "6%" in text                      # decision coverage
    assert "74%" in text and "understated" in text                   # forecast caution
    assert "MASS_A is below its fill-rate commitment" in text        # only the account that is below  # noqa: E501
    assert "ETAIL_C is below" not in text
    assert "West Coast DC" in text
    assert items[0]["level"] == "crit"                               # decisions behind risk leads


def test_insights_degrade_without_warehouse():
    items = nv.insights(EX, None, None)
    assert items and all("Where it hurts" not in i["lead"] for i in items)


def test_fill_status_checks_every_account():
    accts = PERF["accounts"]
    assert nv.fill_status(96.26, accts, None) == ("warn", "Below commitment: MASS_A")
    assert nv.fill_status(96.46, accts, "ETAIL_C")[0] == "ok"
    assert nv.fill_status(None, accts, None)[0] == "info"


def test_kpi_status():
    assert nv.kpi_status("coverage", 89.9)[0] == "ok"
    assert nv.kpi_status("coverage", 74.1) == ("warn", "16 pts under target")
    assert nv.kpi_status("wape_vs_naive", 67.4, 95.3)[0] == "ok"
    assert nv.kpi_status("wape_vs_naive", 99.0, 95.3)[0] == "warn"


def test_art_is_valid_deterministic_svg():
    a = art.sku_svg("SKU-01723", "Titan Squad", "ACTION_FIGURE", "SOCIAL_TREND", "MATURE", "Titan Squad Plushie 147")  # noqa: E501
    b = art.sku_svg("SKU-01723", "Titan Squad", "ACTION_FIGURE", "SOCIAL_TREND", "MATURE", "Titan Squad Plushie 147")  # noqa: E501
    assert a == b
    ET.fromstring(a)                                                  # well-formed XML
    assert a.startswith("<svg") and "viewBox" in a


def test_art_follows_category_not_the_misleading_name():
    plush = art.sku_svg("SKU-00001", "Snugglewick", "PLUSH", "EVERGREEN", "MATURE", "Snugglewick Figure 5")  # noqa: E501
    figure = art.sku_svg("SKU-00001", "Titan Squad", "ACTION_FIGURE", "EVERGREEN", "MATURE", "Titan Squad Plushie 5")  # noqa: E501
    assert plush != figure
    torso = 'width="48" height="46"'                                  # the action figure's torso rectangle  # noqa: E501
    assert torso in figure and torso not in plush


def test_caption_agrees_with_picture():
    # source name says "Plushie" but the category is an action figure: the caption must not
    assert art.display_name("Titan Squad", "ACTION_FIGURE", "Titan Squad Plushie 147", "SKU-01723") \
        == "Titan Squad Action Figure #147"  # noqa: E501
    assert art.display_name("Snugglewick", "PLUSH", "Snugglewick Figure 341", "SKU-00135") \
        == "Snugglewick Plush #341"
    assert art.display_name("Titan Squad", "ACTION_FIGURE", "Titan Squad Carry Pack 304", "SKU-01815") \
        == "Titan Squad Action Figure Pack #304"  # noqa: E501


def test_art_badges_and_ribbons():
    new = art.sku_svg("SKU-1", "Snugglewick", "PLUSH", "FILM_TIE_IN", "LAUNCH", "Snugglewick Deluxe 1")  # noqa: E501
    assert ">NEW<" in new and "polygon" in new                        # launch ribbon + star badge
    sale = art.sku_svg("SKU-2", "Glowberry Pets", "PLUSH", "EVERGREEN", "DECLINE", "Glowberry Pets Mini 2")  # noqa: E501
    assert ">SALE<" in sale


def test_art_unknown_brand_still_renders():
    ET.fromstring(art.sku_svg("SKU-9", None, None, None, None, None))
    assert art.data_uri("<svg/>").startswith("data:image/svg+xml;utf8,")


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
