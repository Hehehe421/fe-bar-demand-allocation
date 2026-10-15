"""Illustrative product art, generated from SKU attributes. No files, no image model.

Every SKU gets a consistent picture because the picture is a pure function of the
SKU's own attributes:

* ``category``  decides the toy: PLUSH -> a plush animal, ACTION_FIGURE -> a figure.
* ``brand``     decides the palette (Snugglewick warm, Titan Squad steel, Glowberry berry).
* ``sku_id``    decides the variant (which animal, which figure, which accent).
* ``license_family`` adds a badge: FILM_TIE_IN star, SOCIAL_TREND sparkle, EVERGREEN heart.
* ``lifecycle_stage`` adds a ribbon for LAUNCH and clearance for DECLINE / EOL.
* the variant word in the source name (Deluxe, Mini, Set, Pack) changes the presentation.

This is illustrative artwork, not product photography. A production deployment would
swap :func:`sku_svg` for the real asset URL; the rest of the app only sees a data URI.

The source ``product_name`` is NOT used to pick the toy: in the synthetic catalog its
"Plushie"/"Figure" word is independent of the category (about half disagree), so using
it would put a robot beside the caption "Plushie".
"""

from __future__ import annotations

import hashlib
from urllib.parse import quote

PALETTES: dict[str, dict[str, str]] = {
    # body, shade, accent, glow
    "Snugglewick": {"body": "#F2C6A0", "shade": "#E0A06F", "accent": "#C9724B", "glow": "#FFF1E3"},
    "Titan Squad": {"body": "#4F7CC4", "shade": "#2F5597", "accent": "#EB6834", "glow": "#E7F0FD"},
    "Glowberry Pets": {"body": "#D86BA8", "shade": "#B44A89", "accent": "#7A4FD1", "glow": "#FCE9F4"},
}
DEFAULT_PALETTE = {"body": "#9AA7B8", "shade": "#6F7D90", "accent": "#2A78D6", "glow": "#EEF2F7"}
FAMILY_BADGE = {"FILM_TIE_IN": "star", "SOCIAL_TREND": "spark", "EVERGREEN": "heart"}
INK = "#1F2937"

ANIMALS = ("bear", "bunny", "cat", "pup")
FIGURES = ("robot", "hero", "knight")


def _h(sku_id: str, salt: str = "") -> int:
    return int(hashlib.sha1(f"{sku_id}|{salt}".encode()).hexdigest()[:8], 16)


def display_name(brand: str | None, category: str | None, product_name: str | None,
                 sku_id: str) -> str:
    """A caption that agrees with the picture.

    The variant word (Deluxe / Mini / Set / Pack) is kept from the source name; the noun
    comes from ``category``. The source name stays available as a tooltip.
    """
    noun = "Plush" if (category or "").upper() == "PLUSH" else "Action Figure"
    variant = ""
    for w in (product_name or "").split():
        if w in ("Deluxe", "Mini", "Set", "Pack"):
            variant = w
    num = "".join(ch for ch in (product_name or sku_id) if ch.isdigit()).lstrip("0") or "0"
    parts = [brand or "", noun, variant]
    return f"{' '.join(p for p in parts if p)} #{num}"


def variant_word(product_name: str | None) -> str:
    for w in (product_name or "").split():
        if w in ("Deluxe", "Mini", "Set", "Pack"):
            return w
    return ""


# ---------------------------------------------------------------------------
# shapes
# ---------------------------------------------------------------------------

def _eyes(cx: float, cy: float, gap: float, r: float = 4.2) -> str:
    out = ""
    for dx in (-gap, gap):
        out += (f'<circle cx="{cx + dx}" cy="{cy}" r="{r}" fill="{INK}"/>'
                f'<circle cx="{cx + dx - r * 0.35}" cy="{cy - r * 0.4}" r="{r * 0.38}" fill="#fff"/>')
    return out


def _plush(sku: str, p: dict[str, str], variant: str) -> str:
    kind = ANIMALS[_h(sku, "animal") % len(ANIMALS)]
    b, s, a = p["body"], p["shade"], p["accent"]
    ears = ""
    if kind == "bear":
        ears = (f'<circle cx="58" cy="52" r="15" fill="{b}"/><circle cx="58" cy="52" r="8" fill="{s}"/>'
                f'<circle cx="102" cy="52" r="15" fill="{b}"/><circle cx="102" cy="52" r="8" fill="{s}"/>')
    elif kind == "bunny":
        ears = (f'<ellipse cx="62" cy="34" rx="9" ry="24" fill="{b}"/><ellipse cx="62" cy="36" rx="4.5" ry="17" fill="{s}"/>'
                f'<ellipse cx="98" cy="34" rx="9" ry="24" fill="{b}"/><ellipse cx="98" cy="36" rx="4.5" ry="17" fill="{s}"/>')
    elif kind == "cat":
        ears = (f'<polygon points="50,62 56,34 76,52" fill="{b}"/><polygon points="55,56 58,42 69,52" fill="{s}"/>'
                f'<polygon points="110,62 104,34 84,52" fill="{b}"/><polygon points="105,56 102,42 91,52" fill="{s}"/>')
    else:  # pup: floppy ears
        ears = (f'<ellipse cx="50" cy="66" rx="11" ry="22" fill="{s}" transform="rotate(14 50 66)"/>'
                f'<ellipse cx="110" cy="66" rx="11" ry="22" fill="{s}" transform="rotate(-14 110 66)"/>')
    face = ""
    if kind == "cat":
        face = (f'<path d="M80 74 l-3 -3 h6z" fill="{a}"/>'
                f'<path d="M66 76 h-14 M66 80 l-13 4 M94 76 h14 M94 80 l13 4" stroke="{INK}" stroke-width="1.3" opacity=".55"/>')
    else:
        face = f'<ellipse cx="80" cy="76" rx="5" ry="3.6" fill="{INK}"/>'
    smile = f'<path d="M72 83 q8 7 16 0" stroke="{INK}" stroke-width="2" fill="none" stroke-linecap="round"/>'
    body = (f'<ellipse cx="80" cy="118" rx="29" ry="31" fill="{b}"/>'
            f'<ellipse cx="80" cy="123" rx="17" ry="21" fill="{p["glow"]}" opacity=".85"/>'
            f'<ellipse cx="52" cy="112" rx="9" ry="16" fill="{s}" transform="rotate(18 52 112)"/>'
            f'<ellipse cx="108" cy="112" rx="9" ry="16" fill="{s}" transform="rotate(-18 108 112)"/>'
            f'<ellipse cx="66" cy="146" rx="13" ry="8" fill="{s}"/><ellipse cx="94" cy="146" rx="13" ry="8" fill="{s}"/>')
    head = f'<circle cx="80" cy="70" r="29" fill="{b}"/><ellipse cx="80" cy="79" rx="14" ry="10" fill="{p["glow"]}"/>'
    bow = (f'<path d="M80 98 l-12 -7 v14z M80 98 l12 -7 v14z" fill="{a}"/><circle cx="80" cy="98" r="3.2" fill="{a}"/>')
    art = body + ears + head + _eyes(80, 66, 12) + face + smile + bow
    return _pair(art) if variant in ("Set", "Pack") else art


def _pair(art: str) -> str:
    """A set or pack reads as two smaller toys side by side."""
    return (f'<g transform="translate(-6 40) scale(.66)">{art}</g>'
            f'<g transform="translate(60 40) scale(.66)">{art}</g>')


def _figure(sku: str, p: dict[str, str], variant: str) -> str:
    kind = FIGURES[_h(sku, "figure") % len(FIGURES)]
    b, s, a = p["body"], p["shade"], p["accent"]
    cape = (f'<path d="M62 78 Q80 100 98 78 L108 138 Q80 150 52 138Z" fill="{a}" opacity=".95"/>'
            if kind == "hero" else "")
    legs = (f'<rect x="64" y="118" width="13" height="32" rx="5" fill="{s}"/>'
            f'<rect x="83" y="118" width="13" height="32" rx="5" fill="{s}"/>'
            f'<rect x="60" y="145" width="19" height="9" rx="4" fill="{INK}"/><rect x="81" y="145" width="19" height="9" rx="4" fill="{INK}"/>')
    torso = (f'<rect x="56" y="76" width="48" height="46" rx="11" fill="{b}"/>'
             f'<rect x="64" y="84" width="32" height="16" rx="5" fill="{p["glow"]}" opacity=".9"/>'
             f'<circle cx="80" cy="92" r="6" fill="{a}"/>'
             f'<rect x="38" y="80" width="15" height="38" rx="7" fill="{s}"/><rect x="107" y="80" width="15" height="38" rx="7" fill="{s}"/>')
    if kind == "robot":
        head = (f'<line x1="80" y1="30" x2="80" y2="18" stroke="{INK}" stroke-width="3"/><circle cx="80" cy="16" r="4.5" fill="{a}"/>'
                f'<rect x="56" y="30" width="48" height="42" rx="9" fill="{b}"/><rect x="62" y="40" width="36" height="14" rx="6" fill="{INK}"/>'
                f'<circle cx="72" cy="47" r="3.6" fill="{a}"/><circle cx="88" cy="47" r="3.6" fill="{a}"/>'
                f'<rect x="68" y="60" width="24" height="5" rx="2.5" fill="{s}"/>')
    elif kind == "knight":
        head = (f'<path d="M54 56 a26 26 0 0 1 52 0 v14 h-52z" fill="{b}"/><rect x="52" y="52" width="56" height="8" rx="3" fill="{s}"/>'
                f'<rect x="70" y="28" width="20" height="10" rx="4" fill="{a}"/><rect x="62" y="62" width="36" height="8" rx="3" fill="{INK}"/>')
    else:  # hero
        head = (f'<circle cx="80" cy="52" r="24" fill="#F6D2B0"/><path d="M54 50 a26 26 0 0 1 52 0 q-26 -12 -52 0z" fill="{s}"/>'
                f'<rect x="58" y="46" width="44" height="12" rx="6" fill="{a}"/>{_eyes(80, 52, 9, 3.4)}'
                f'<path d="M73 64 q7 5 14 0" stroke="{INK}" stroke-width="2" fill="none" stroke-linecap="round"/>')
    art = cape + legs + torso + head
    return _pair(art) if variant in ("Set", "Pack") else art


def _badge(kind: str, p: dict[str, str]) -> str:
    a = p["accent"]
    if kind == "star":
        pts = "134,18 138,29 150,29 140,36 144,48 134,41 124,48 128,36 118,29 130,29"
        return f'<polygon points="{pts}" fill="#F5B301" stroke="#fff" stroke-width="2"/>'
    if kind == "spark":
        return (f'<path d="M134 14 l4 12 12 4 -12 4 -4 12 -4 -12 -12 -4 12 -4z" fill="{a}" stroke="#fff" stroke-width="2"/>'
                f'<circle cx="120" cy="46" r="3" fill="{a}"/>')
    return ('<path d="M134 44 C118 32 118 18 128 18 C132 18 134 21 134 21 C134 21 136 18 140 18 C150 18 150 32 134 44z" '
            'fill="#E25563" stroke="#fff" stroke-width="2"/>')


def _ribbon(stage: str | None) -> str:
    if stage == "LAUNCH":
        txt, col = "NEW", "#0E9F6E"
    elif stage in ("DECLINE", "EOL"):
        txt, col = ("SALE" if stage == "DECLINE" else "LAST"), "#B45309"
    else:
        return ""
    return (f'<g><rect x="6" y="10" width="44" height="17" rx="8.5" fill="{col}"/>'
            f'<text x="28" y="22.5" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" '
            f'font-size="10" font-weight="700" fill="#fff" letter-spacing=".5">{txt}</text></g>')


def sku_svg(sku_id: str, brand: str | None, category: str | None, license_family: str | None,
            lifecycle_stage: str | None, product_name: str | None = None) -> str:
    p = PALETTES.get(brand or "", DEFAULT_PALETTE)
    variant = variant_word(product_name)
    toy = _plush(sku_id, p, variant) if (category or "").upper() == "PLUSH" else _figure(sku_id, p, variant)
    scale = 0.78 if variant == "Mini" else 1.0
    if scale != 1.0:
        toy = f'<g transform="translate({80 * (1 - scale):.1f} {100 * (1 - scale) + 18:.1f}) scale({scale})">{toy}</g>'
    crown = ""
    if variant == "Deluxe":
        crown = ('<path d="M62 14 l8 10 10 -12 10 12 8 -10 4 18 h-44z" fill="#F5B301" stroke="#fff" stroke-width="1.6"/>')
    badge = _badge(FAMILY_BADGE.get(license_family or "", "heart"), p) if license_family else ""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 160" width="160" height="160" '
        f'role="img" aria-label="{brand or "Toy"} {category or ""} illustration">'
        f'<defs><radialGradient id="g" cx="50%" cy="42%" r="62%"><stop offset="0" stop-color="{p["glow"]}"/>'
        f'<stop offset="1" stop-color="#ffffff"/></radialGradient></defs>'
        f'<rect width="160" height="160" rx="22" fill="url(#g)"/>'
        f'<ellipse cx="80" cy="152" rx="40" ry="6" fill="#000" opacity=".08"/>'
        f'{toy}{crown}{badge}{_ribbon(lifecycle_stage)}</svg>'
    )


def data_uri(svg: str) -> str:
    return "data:image/svg+xml;utf8," + quote(svg, safe="/:=,;()' ")
