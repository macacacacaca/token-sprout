#!/usr/bin/env python3
"""Generate Token Sprout's illustrations — stdlib only, deterministic output.

Art direction: flat geometric forms with two-tone facets (after Monument
Valley, Apple Design Award 2014) set in calm, layered, atmospheric
landscapes (after Alto's Odyssey, Apple Design Award 2018). Every shape here
is original; the references set principles, not pixels.

Usage (from the repository root):
    python scripts/build_art.py           # (re)write every generated SVG
    python scripts/build_art.py --check   # exit 1 if a generated SVG is stale
    python scripts/build_art.py --png     # also rasterize PNGs (headless Chrome)
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGES = ("seed", "sprout", "leaf", "bud", "bloom")

# ---------------------------------------------------------------------------
# Palette. The plant keeps one palette in every theme; scenes switch between
# dawn (light) and night (dark).

PLANT = {
    "pot_rim_lit": "#EF9E74",
    "pot_rim_shade": "#CF7550",
    "pot_lit": "#DE8259",
    "pot_shade": "#B9603F",
    "pot_band": "#A9553A",
    "soil": "#6B4535",
    "soil_lit": "#8A5C45",
    "seed_lit": "#C9844C",
    "seed_shade": "#965630",
    "seed_base": "#EBCB9E",
    "seed_shine": "#EBAE78",
    "stem": "#4E9659",
    "leaf_top_lit": "#9BD27C",
    "leaf_top_shade": "#69B264",
    "leaf_mid_lit": "#6DB56A",
    "leaf_mid_shade": "#468F52",
    "leaf_low_lit": "#4C9A5C",
    "leaf_low_shade": "#2F6E46",
    "bud_lit": "#F7A5BA",
    "bud_mid": "#EC7E9B",
    "bud_shade": "#D2587A",
    "petal_lit": "#FFD3DE",
    "petal_shade": "#F7A4BC",
    "petal_core": "#EC7C9D",
    "pistil": "#FFD46E",
    "pistil_shade": "#F0A63A",
    "spark": "#F6C350",
    "shadow": "#1F3A33",
}

LIGHT_DIR = (-0.6, -0.8)  # light falls from the upper left


def _f(value: float) -> str:
    """Compact, stable number formatting for path data."""
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def _pt(x: float, y: float) -> str:
    return f"{_f(x)},{_f(y)}"


def facet_leaf(
    base: tuple[float, float],
    tip: tuple[float, float],
    width: float,
    lit: str,
    shade: str,
    *,
    bend: float = 0.0,
    round_tip: float = 0.0,
) -> str:
    """A two-tone leaf from ``base`` to ``tip``, split along its midrib.

    The half facing the light gets ``lit``; the other half ``shade``.
    ``bend`` curves the leaf sideways; ``round_tip`` (0..1) blunts the point.
    """
    bx, by = base
    tx, ty = tip
    dx, dy = tx - bx, ty - by
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux

    def at(along: float, side: float) -> tuple[float, float]:
        return (bx + ux * length * along + nx * side, by + uy * length * along + ny * side)

    r = round_tip
    up1 = at(0.22, width + bend)
    up2 = at(0.78 + 0.3 * r, width * (0.85 - 0.15 * r) + bend)
    lo2 = at(0.78 + 0.3 * r, -width * (0.85 - 0.15 * r) + bend)
    lo1 = at(0.22, -width + bend)
    mid1 = at(0.35, bend * 0.9)
    mid2 = at(0.7, bend * 0.9)

    plus_side = f"M{_pt(bx, by)}C{_pt(*up1)} {_pt(*up2)} {_pt(tx, ty)}C{_pt(*mid2)} {_pt(*mid1)} {_pt(bx, by)}Z"
    minus_side = f"M{_pt(bx, by)}C{_pt(*mid1)} {_pt(*mid2)} {_pt(tx, ty)}C{_pt(*lo2)} {_pt(*lo1)} {_pt(bx, by)}Z"
    plus_is_lit = nx * LIGHT_DIR[0] + ny * LIGHT_DIR[1] > 0
    lit_path, shade_path = (plus_side, minus_side) if plus_is_lit else (minus_side, plus_side)
    return f'<path d="{lit_path}" fill="{lit}"/><path d="{shade_path}" fill="{shade}"/>'


def sparkle(x: float, y: float, size: float, color: str, opacity: float = 1.0) -> str:
    """A four-point star."""
    s, w = size, size * 0.28
    d = (
        f"M{_pt(x, y - s)}Q{_pt(x + w, y - w)} {_pt(x + s, y)}"
        f"Q{_pt(x + w, y + w)} {_pt(x, y + s)}Q{_pt(x - w, y + w)} {_pt(x - s, y)}"
        f"Q{_pt(x - w, y - w)} {_pt(x, y - s)}Z"
    )
    return f'<path d="{d}" fill="{color}" opacity="{_f(opacity)}"/>'


# ---------------------------------------------------------------------------
# Plants. Each draws in a 240 × 240 box; every stage shares the same pot.

def pot() -> str:
    p = PLANT
    return "".join(
        [
            f'<ellipse cx="120" cy="226" rx="62" ry="7" fill="{p["shadow"]}" opacity="0.14"/>',
            # Body: two vertical facets, lit on the left.
            '<path d="M82,182H128V225H95Q91,225 90,221Z" fill="url(#ts-pot-lit)"/>',
            '<path d="M128,182H158L150,221Q149,225 145,225H128Z" fill="url(#ts-pot-shade)"/>',
            f'<path d="M82,182H158L157,187H83Z" fill="{p["pot_band"]}" opacity="0.55"/>',
            # Soil mound peeking over the rim.
            f'<path d="M80,171Q120,158 160,171Z" fill="{p["soil"]}"/>',
            f'<path d="M92,168Q110,162 124,163" stroke="{p["soil_lit"]}" stroke-width="2" '
            'stroke-linecap="round" fill="none"/>',
            # Rim.
            f'<path d="M78,169H128V184H78Q74,184 74,180V173Q74,169 78,169Z" fill="{p["pot_rim_lit"]}"/>',
            f'<path d="M128,169H162Q166,169 166,173V180Q166,184 162,184H128Z" fill="{p["pot_rim_shade"]}"/>',
            '<path d="M80,171.5H124" stroke="#F8C3A2" stroke-width="2" stroke-linecap="round" opacity="0.8"/>',
        ]
    )


def stem(d: str, width: float = 5.0) -> str:
    return (
        f'<path d="{d}" stroke="{PLANT["stem"]}" stroke-width="{_f(width)}" '
        'stroke-linecap="round" fill="none"/>'
    )


def seed_art() -> str:
    p = PLANT
    body = "M120,120C129,129 145,137 145,154C145,167 134,173 120,173C106,173 95,167 95,154C95,137 111,129 120,120Z"
    shade = "M120,120C129,129 145,137 145,154C145,167 134,173 120,173C127,159 128,139 120,120Z"
    base = "M96,160C106,166 134,166 144,160C142,169 133,173 120,173C107,173 98,169 96,160Z"
    return "".join(
        [
            '<circle cx="120" cy="146" r="46" fill="url(#ts-glow)"/>',
            f'<path d="{body}" fill="{p["seed_lit"]}"/>',
            f'<path d="{shade}" fill="{p["seed_shade"]}"/>',
            f'<path d="{base}" fill="{p["seed_base"]}"/>',
            f'<path d="M104,147C105,139 110,133 115,129" stroke="{p["seed_shine"]}" stroke-width="3.5" '
            'stroke-linecap="round" fill="none"/>',
            sparkle(160, 120, 8, p["spark"]),
            sparkle(84, 132, 5, p["spark"], 0.8),
            sparkle(150, 96, 3.5, p["spark"], 0.6),
        ]
    )


def sprout_art() -> str:
    p = PLANT
    return "".join(
        [
            stem("M120,170C120,158 118,147 120,134"),
            facet_leaf((119, 136), (80, 112), 15, p["leaf_top_lit"], p["leaf_top_shade"], bend=-3, round_tip=0.7),
            facet_leaf((121, 133), (164, 104), 17, p["leaf_top_lit"], p["leaf_top_shade"], bend=3, round_tip=0.7),
        ]
    )


def leaf_art() -> str:
    p = PLANT
    return "".join(
        [
            stem("M120,170C121,150 117,128 120,108C122,98 120,92 120,88", 5.5),
            facet_leaf((119, 152), (66, 138), 13, p["leaf_low_lit"], p["leaf_low_shade"], bend=-4),
            facet_leaf((121, 142), (176, 124), 13, p["leaf_low_lit"], p["leaf_low_shade"], bend=4),
            facet_leaf((119, 122), (76, 96), 12, p["leaf_mid_lit"], p["leaf_mid_shade"], bend=-4),
            facet_leaf((121, 110), (164, 82), 12, p["leaf_mid_lit"], p["leaf_mid_shade"], bend=4),
            facet_leaf((120, 92), (102, 62), 9, p["leaf_top_lit"], p["leaf_top_shade"], bend=-2, round_tip=0.3),
            facet_leaf((120, 92), (140, 64), 9, p["leaf_top_lit"], p["leaf_top_shade"], bend=2, round_tip=0.3),
        ]
    )


def _foliage_tall() -> str:
    p = PLANT
    return "".join(
        [
            stem("M120,170C121,146 117,118 120,96C122,84 120,78 120,74", 5.5),
            facet_leaf((119, 158), (64, 140), 13, p["leaf_low_lit"], p["leaf_low_shade"], bend=-4),
            facet_leaf((121, 150), (178, 128), 13, p["leaf_low_lit"], p["leaf_low_shade"], bend=4),
            facet_leaf((119, 128), (74, 104), 12, p["leaf_mid_lit"], p["leaf_mid_shade"], bend=-4),
            facet_leaf((121, 116), (166, 92), 12, p["leaf_mid_lit"], p["leaf_mid_shade"], bend=4),
        ]
    )


def bud_art() -> str:
    p = PLANT
    return "".join(
        [
            _foliage_tall(),
            f'<path d="M108,78C114,88 126,88 132,78C127,84 113,84 108,78Z" fill="{p["leaf_mid_shade"]}"/>',
            f'<path d="M120,30C134,40 139,58 133,74C127,82 113,82 107,74C101,58 106,40 120,30Z" fill="{p["bud_shade"]}"/>',
            f'<path d="M107,74C101,60 103,44 113,33C118,47 120,63 120,82C114,82 109,79 107,74Z" fill="{p["bud_lit"]}"/>',
            f'<path d="M133,74C139,60 137,44 127,33C122,47 120,63 120,82C126,82 131,79 133,74Z" fill="{p["bud_mid"]}"/>',
            f'<path d="M111,62C110,54 111,46 114,40" stroke="#FFD5DF" stroke-width="2" stroke-linecap="round" fill="none"/>',
            f'<path d="M106,80C112,90 128,90 134,80C128,86 112,86 106,80Z" fill="{p["leaf_low_shade"]}"/>',
            sparkle(156, 44, 5, p["spark"], 0.9),
        ]
    )


def _petal(angle: float, scale: float = 1.0) -> str:
    """One notched, obovate sakura petal pointing up from the origin."""
    p = PLANT
    lit = "M0,0C-6,-4 -17,-16 -16,-27C-15,-34 -8,-37 -3,-34L0,-30Z"
    shade = "M0,0C6,-4 17,-16 16,-27C15,-34 8,-37 3,-34L0,-30Z"
    transform = f"rotate({_f(angle)})" + (f" scale({_f(scale)})" if scale != 1 else "")
    return (
        f'<g transform="{transform}">'
        f'<path d="{lit}" fill="{p["petal_lit"]}"/><path d="{shade}" fill="{p["petal_shade"]}"/>'
        "</g>"
    )


def _blossom(cx: float, cy: float, scale: float, tilt: float) -> str:
    p = PLANT
    petals = "".join(_petal(tilt + 72 * i) for i in range(5))
    stamens = "".join(
        f'<circle cx="{_f(10 * math.sin(math.radians(a)))}" cy="{_f(-10 * math.cos(math.radians(a)))}" '
        f'r="2" fill="{p["pistil_shade"]}"/>'
        for a in range(0, 360, 40)
    )
    return (
        f'<g transform="translate({_f(cx)} {_f(cy)}) scale({_f(scale)})">{petals}'
        '<circle r="19" fill="url(#ts-core)"/>'
        f'{stamens}<circle r="7" fill="{p["pistil"]}"/><circle r="3" fill="{p["pistil_shade"]}"/></g>'
    )


def bloom_art() -> str:
    p = PLANT
    return "".join(
        [
            '<circle cx="120" cy="62" r="70" fill="url(#ts-halo)"/>',
            _foliage_tall(),
            _blossom(118, 60, 1.05, -8),
            f'<g transform="translate(178 116) rotate(35)">{_petal(0, 0.42)}</g>',
            f'<g transform="translate(66 96) rotate(-55)">{_petal(0, 0.34)}</g>',
            sparkle(172, 30, 6, p["spark"]),
            sparkle(66, 44, 4, p["spark"], 0.85),
        ]
    )


PLANT_ART = {
    "seed": seed_art,
    "sprout": sprout_art,
    "leaf": leaf_art,
    "bud": bud_art,
    "bloom": bloom_art,
}

PLANT_DEFS = (
    '<radialGradient id="ts-glow"><stop offset="0" stop-color="#FFE3A6" stop-opacity="0.85"/>'
    '<stop offset="1" stop-color="#FFE3A6" stop-opacity="0"/></radialGradient>'
    '<radialGradient id="ts-halo"><stop offset="0" stop-color="#FFE7AE" stop-opacity="0.8"/>'
    '<stop offset="0.55" stop-color="#FFE7AE" stop-opacity="0.2"/>'
    '<stop offset="1" stop-color="#FFE7AE" stop-opacity="0"/></radialGradient>'
    '<radialGradient id="ts-core">'
    f'<stop offset="0" stop-color="{PLANT["petal_core"]}" stop-opacity="0.9"/>'
    f'<stop offset="1" stop-color="{PLANT["petal_core"]}" stop-opacity="0"/></radialGradient>'
    '<linearGradient id="ts-pot-lit" x1="0" y1="0" x2="0" y2="1">'
    f'<stop offset="0" stop-color="{PLANT["pot_lit"]}"/><stop offset="1" stop-color="#CF7049"/></linearGradient>'
    '<linearGradient id="ts-pot-shade" x1="0" y1="0" x2="0" y2="1">'
    f'<stop offset="0" stop-color="{PLANT["pot_shade"]}"/><stop offset="1" stop-color="#A24F33"/></linearGradient>'
)


def plant_group(stage: str) -> str:
    return f"<g>{PLANT_ART[stage]()}{pot()}</g>"


def plant_svg(stage: str) -> str:
    title = {"seed": "Seed", "sprout": "Sprout", "leaf": "Leaf", "bud": "Bud", "bloom": "Bloom"}[stage]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240" width="240" height="240" '
        f'role="img" aria-label="Token Sprout {title.lower()} stage">'
        f"<title>{title}</title><defs>{PLANT_DEFS}</defs>{plant_group(stage)}</svg>\n"
    )


# ---------------------------------------------------------------------------
# Scenes: dawn for light themes, night for dark themes.

SANS = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, "
    "'PingFang TC', 'Noto Sans TC', 'Microsoft JhengHei', sans-serif"
)
MONO = "'SF Mono', SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"

SCENE = {
    "light": {
        "sky": ("#F6D3C4", "#FAE6D8", "#FDF4EA"),
        "orb": "#FFE0AE",
        "orb_glow": "#FFD9A2",
        "hills": ("#EFCDBF", "#E2CDB4", "#BFD2AA", "#94BA8B"),
        "ridge": "#A9C99C",
        "ink": "#1F3A33",
        "muted": "#4D665B",
        "accent": "#C0603F",
        "card": "#FFFDF8",
        "card_line": "#EADFCF",
        "chip": "#17302A",
        "chip_text": "#E7F4EA",
        "chip_muted": "#8FB5A0",
        "bird": "#B98676",
        "cloud": "#FFF6EE",
    },
    "dark": {
        "sky": ("#0D1A28", "#15293D", "#1F3B50"),
        "orb": "#F5EAD0",
        "orb_glow": "#9FB8C9",
        "hills": ("#2A3E55", "#284759", "#22504D", "#1B463C"),
        "ridge": "#2C5E4E",
        "ink": "#F3EEE3",
        "muted": "#AFC2BC",
        "accent": "#F0A07A",
        "card": "#14232F",
        "card_line": "#2A4050",
        "chip": "#0A1620",
        "chip_stroke": ' stroke="#2C4A40" stroke-width="1.5"',
        "chip_text": "#E7F4EA",
        "chip_muted": "#7FA493",
        "bird": "#8FA6B8",
        "cloud": "#2B4459",
    },
}

TEXT = {
    "en": {
        "eyebrow": "FOR CLAUDE CODE",
        "tagline": ("Grow a tiny plant with the tokens", "you spend in Claude Code."),
        "where": "In Claude Code’s status line, the VS Code status bar, and your terminal.",
        "hero_label": "Token Sprout: a potted plant blooming at dawn, watered by token droplets.",
    },
    "zh-TW": {
        "eyebrow": "CLAUDE CODE 的小盆栽",
        "tagline": ("用你在 Claude Code 花掉的 token，", "養大一株小植物。"),
        "where": "出現在 Claude Code 狀態列、VS Code 狀態列與終端機。",
        "hero_label": "Token Sprout：一盆被 token 水滴澆灌、在晨光中開花的小植物。",
    },
}


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def place(stage: str, x: float, ground_y: float, scale: float, extra: str = "") -> str:
    """Draw a plant so its pot stands on (x, ground_y)."""
    tx, ty = x - 120 * scale, ground_y - 226 * scale
    return f'<g transform="translate({_f(tx)} {_f(ty)}) scale({_f(scale)})"{extra}>{plant_group(stage)}</g>'


def droplet(x: float, y: float, size: float, opacity: float = 1.0) -> str:
    s = size / 14
    return (
        f'<g transform="translate({_f(x)} {_f(y)}) scale({_f(s)})" opacity="{_f(opacity)}">'
        '<path d="M0,-14C5,-6 8,-1 8,4A8,8 0 1 1 -8,4C-8,-1 -5,-6 0,-14Z" fill="#6DB6EA"/>'
        '<path d="M0,-14C5,-6 8,-1 8,4A8,8 0 0 1 0,12Z" fill="#4A97D6"/>'
        '<ellipse cx="-3.2" cy="3" rx="2" ry="3.4" fill="#DDF1FF" opacity="0.9"/></g>'
    )


def mini_sprout(x: float, y: float, s: float = 1.0) -> str:
    """A tiny two-leaf sprout glyph for status-bar mockups."""
    return (
        f'<g transform="translate({_f(x)} {_f(y)}) scale({_f(s)})">'
        '<path d="M0,8V0" stroke="#79C37A" stroke-width="2" stroke-linecap="round"/>'
        '<path d="M0,1C-2,-5 -7,-7 -10,-5C-9,-1 -5,1 0,1Z" fill="#8FD07D"/>'
        '<path d="M0,0C2,-6 8,-8 11,-6C10,-1 5,1 0,0Z" fill="#6CB86C"/></g>'
    )


def statusline_chip(x: float, y: float, theme: str) -> str:
    """The Claude Code status line while Claude is thinking, as a vector chip."""
    c = SCENE[theme]
    cells = "".join(
        f'<rect x="{_f(176 + i * 13)}" y="18" width="10" height="16" rx="2" '
        f'fill="{"#8FD07D" if i < 6 else "#2C4A40"}"/>'
        for i in range(10)
    )
    return (
        f'<g transform="translate({_f(x)} {_f(y)})">'
        f'<rect width="528" height="52" rx="14" fill="{c["chip"]}"{c.get("chip_stroke", "")}/>'
        f"{mini_sprout(26, 25)}{mini_sprout(50, 25)}"
        f'<text x="68" y="33" font-family="{MONO}" font-size="18" font-weight="600" fill="{c["chip_text"]}">2/20</text>'
        f'<text x="118" y="33" font-family="{MONO}" font-size="18" fill="{c["chip_muted"]}">·</text>'
        f'{droplet(150, 25, 15)}{cells}'
        f'<text x="312" y="33" font-family="{MONO}" font-size="18" font-weight="600" fill="{c["chip_text"]}">67%</text>'
        f'<text x="352" y="33" font-family="{MONO}" font-size="18" fill="{c["chip_muted"]}">·</text>'
        f'<text x="370" y="33" font-family="{MONO}" font-size="18" font-weight="600" fill="{c["chip_text"]}">+5,000</text>'
        f'<text x="446" y="33" font-family="{MONO}" font-size="18" fill="{c["chip_muted"]}">tokens</text>'
        "</g>"
    )


def _sky(theme: str, width: int, height: int, orb_x: float, orb_y: float, orb_r: float) -> str:
    c = SCENE[theme]
    top, mid, bottom = c["sky"]
    parts = [
        '<linearGradient id="ts-sky" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{top}"/><stop offset="0.5" stop-color="{mid}"/>'
        f'<stop offset="1" stop-color="{bottom}"/></linearGradient>'
        '<radialGradient id="ts-orb-glow">'
        f'<stop offset="0" stop-color="{c["orb_glow"]}" stop-opacity="0.75"/>'
        f'<stop offset="1" stop-color="{c["orb_glow"]}" stop-opacity="0"/></radialGradient>'
    ]
    if theme == "dark":
        # A crescent: the moon disc masked by an offset disc.
        parts.append(
            f'<mask id="ts-crescent"><rect width="{width}" height="{height}" fill="#fff"/>'
            f'<circle cx="{_f(orb_x + orb_r * 0.45)}" cy="{_f(orb_y - orb_r * 0.3)}" '
            f'r="{_f(orb_r * 0.88)}" fill="#000"/></mask>'
        )
    defs = "".join(parts)
    moon_mask = ' mask="url(#ts-crescent)"' if theme == "dark" else ""
    body = [
        f'<rect width="{width}" height="{height}" fill="url(#ts-sky)"/>',
        f'<circle cx="{_f(orb_x)}" cy="{_f(orb_y)}" r="{_f(orb_r * 2.4)}" fill="url(#ts-orb-glow)"/>',
        f'<circle cx="{_f(orb_x)}" cy="{_f(orb_y)}" r="{_f(orb_r)}" fill="{c["orb"]}"{moon_mask}/>',
    ]
    if theme == "dark":
        stars = [
            (110, 70, 1.6), (240, 120, 1.2), (330, 52, 1.8), (470, 96, 1.1), (560, 40, 1.4),
            (690, 84, 1.0), (760, 150, 1.5), (860, 60, 1.2), (1010, 110, 1.7), (1120, 46, 1.3),
            (1190, 140, 1.0), (640, 190, 1.2), (410, 170, 0.9), (180, 200, 1.1), (950, 210, 0.9),
        ]
        for i, (sx, sy, sr) in enumerate(stars):
            if sx < width and sy < height:
                body.append(
                    f'<circle cx="{sx}" cy="{sy}" r="{sr}" fill="#F5EAD0" opacity="{0.45 + (i % 4) * 0.15:.2f}"/>'
                )
    return defs, "".join(body)


def _hills(theme: str, width: int, height: int, lift: float = 0.0) -> str:
    """Four layered ridges with atmospheric perspective (far = pale)."""
    c = SCENE[theme]
    w, h = width, height
    far, mid, near, ground = c["hills"]
    y = lambda v: _f(v - lift)  # noqa: E731
    return "".join(
        [
            f'<path d="M0,{y(h * 0.62)}C{_f(w * 0.12)},{y(h * 0.55)} {_f(w * 0.24)},{y(h * 0.6)} {_f(w * 0.36)},{y(h * 0.56)}'
            f'S{_f(w * 0.62)},{y(h * 0.5)} {_f(w * 0.76)},{y(h * 0.57)}S{_f(w * 0.94)},{y(h * 0.53)} {w},{y(h * 0.56)}'
            f'V{h}H0Z" fill="{far}"/>',
            f'<path d="M0,{y(h * 0.7)}C{_f(w * 0.16)},{y(h * 0.64)} {_f(w * 0.3)},{y(h * 0.71)} {_f(w * 0.46)},{y(h * 0.66)}'
            f'S{_f(w * 0.74)},{y(h * 0.62)} {_f(w * 0.88)},{y(h * 0.68)}S{w},{y(h * 0.66)} {w},{y(h * 0.66)}'
            f'V{h}H0Z" fill="{mid}"/>',
            f'<path d="M0,{y(h * 0.8)}C{_f(w * 0.2)},{y(h * 0.74)} {_f(w * 0.38)},{y(h * 0.82)} {_f(w * 0.56)},{y(h * 0.77)}'
            f'S{_f(w * 0.84)},{y(h * 0.72)} {w},{y(h * 0.76)}V{h}H0Z" fill="{near}"/>',
            f'<path d="M0,{y(h * 0.9)}C{_f(w * 0.24)},{y(h * 0.86)} {_f(w * 0.5)},{y(h * 0.9)} {_f(w * 0.7)},{y(h * 0.87)}'
            f'S{_f(w * 0.92)},{y(h * 0.84)} {w},{y(h * 0.86)}V{h}H0Z" fill="{ground}"/>',
        ]
    )


def _birds(theme: str, points: list[tuple[float, float, float]]) -> str:
    c = SCENE[theme]
    return "".join(
        f'<path d="M{_f(x - 7 * s)},{_f(y)}Q{_f(x - 3 * s)},{_f(y - 4 * s)} {_f(x)},{_f(y)}'
        f'Q{_f(x + 3 * s)},{_f(y - 4 * s)} {_f(x + 7 * s)},{_f(y)}" stroke="{c["bird"]}" '
        f'stroke-width="{_f(1.6 * s)}" fill="none" stroke-linecap="round"/>'
        for x, y, s in points
    )


def _cloud(theme: str, x: float, y: float, w: float) -> str:
    c = SCENE[theme]
    return (
        f'<g opacity="{0.75 if theme == "light" else 0.55}">'
        f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="14" rx="7" fill="{c["cloud"]}"/>'
        f'<rect x="{_f(x + w * 0.2)}" y="{_f(y - 9)}" width="{_f(w * 0.45)}" height="14" rx="7" fill="{c["cloud"]}"/></g>'
    )


def hero_svg(theme: str, lang: str, radius: int = 28) -> str:
    """README banner, 1280 × 640; square-cornered, it is the social preview."""
    W, H = 1280, 640
    c = SCENE[theme]
    t = TEXT[lang]
    orb = (1010, 300, 92) if theme == "light" else (1104, 150, 50)
    sky_defs, sky = _sky(theme, W, H, *orb)
    title_size = 92
    line1, line2 = t["tagline"]
    eyebrow_spacing = 5 if lang == "en" else 3
    body = [
        sky,
        _cloud(theme, 640, 150, 150),
        _cloud(theme, 1120, 214, 110),
        _birds(theme, [(760, 128, 1.0), (790, 142, 0.8), (1180, 96, 0.9)]),
        _hills(theme, W, H),
        droplet(1036, 118, 22, 0.95),
        droplet(984, 168, 18, 0.85),
        droplet(1068, 206, 15, 0.75),
        place("bloom", 1010, 598, 1.5),
        # Copy.
        f'<text x="96" y="196" font-family="{SANS}" font-size="17" font-weight="700" '
        f'letter-spacing="{eyebrow_spacing}" fill="{c["accent"]}">{_esc(t["eyebrow"])}</text>',
        f'<text x="90" y="292" font-family="{SANS}" font-size="{title_size}" font-weight="800" '
        f'letter-spacing="-2" fill="{c["ink"]}">Token Sprout</text>',
        f'<text x="96" y="348" font-family="{SANS}" font-size="29" font-weight="500" fill="{c["muted"]}">{_esc(line1)}</text>',
        f'<text x="96" y="388" font-family="{SANS}" font-size="29" font-weight="500" fill="{c["muted"]}">{_esc(line2)}</text>',
        statusline_chip(96, 430, theme),
        f'<text x="98" y="518" font-family="{SANS}" font-size="16" font-weight="500" fill="{c["muted"]}" '
        f'opacity="0.9">{_esc(t["where"])}</text>',
    ]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'role="img" aria-label="{_esc(t["hero_label"])}">'
        f"<title>{_esc(t['hero_label'])}</title>"
        f'<defs>{PLANT_DEFS}{sky_defs}<clipPath id="ts-frame"><rect width="{W}" height="{H}" rx="{radius}"/></clipPath></defs>'
        f'<g clip-path="url(#ts-frame)">{"".join(body)}</g></svg>\n'
    )


FLOW_THEME = {
    "light": {
        "bg": "#FCF8F1", "card": "#FFFFFF", "line": "#E8DCCB", "ink": "#1F3A33", "muted": "#5A7064",
        "faint": "#8A9A90", "proxy": "#EBF6E5", "proxy_line": "#9CCB8E", "wire": "#6B8579",
        "green": "#3F8F55", "accent": "#C0603F", "term": "#17302A", "term_text": "#DDEFE2",
        "term_muted": "#86AD97", "shadow": "#1F3A33", "badge": "#C0603F", "badge_text": "#FFFFFF",
        "editor": "#F1ECE3", "editor_bar": "#2D4A3E", "code": "#D9CFBF",
    },
    "dark": {
        "bg": "#0E1A24", "card": "#152431", "line": "#29404F", "ink": "#F3EEE3", "muted": "#AFC2BC",
        "faint": "#7F948D", "proxy": "#153026", "proxy_line": "#3E7C59", "wire": "#8FA9A0",
        "green": "#7CC48A", "accent": "#F0A07A", "term": "#0A141C", "term_text": "#DDEFE2",
        "term_muted": "#7FA493", "shadow": "#000000", "badge": "#F0A07A", "badge_text": "#10202B",
        "editor": "#1B2C3A", "editor_bar": "#0B151D", "code": "#2C4252",
    },
}

FLOW_TEXT = {
    "en": {
        "eyebrow": "HOW IT WORKS",
        "headline": "One local hop. Only the token counts stay.",
        "sub": "Requests and replies stream through untouched; the proxy keeps a copy of the usage numbers and nothing else.",
        "client_lines": ("$ claude", "Thinking…"),
        "proxy_title": "Token Sprout proxy",
        "proxy_lines": ("Zero buffering · no retries", "Fail-open: never blocks Claude"),
        "api_lines": ("The endpoint Claude already uses", "Your login passes through as-is"),
        "request": "request",
        "stream": "stream",
        "tee": "usage numbers only",
        "state_line": "~/.token-sprout/ · 0600 · counts only",
        "bus": "read-only views",
        "r1": ("Claude Code status line", "Shown while Claude thinks"),
        "r2": ("VS Code", "Status bar + illustrated sidebar"),
        "r3": ("Terminal", "token-sprout watch · status"),
        "new": "NEW",
        "privacy": "Prompts, replies, and API keys stay in memory — never written to disk or logs.",
        "label": "How Token Sprout works: Claude Code talks to the Anthropic API through a local proxy that copies only token usage numbers into a private state file, which three read-only views display.",
    },
    "zh-TW": {
        "eyebrow": "運作方式",
        "headline": "只多經過一個本機站點，留下的只有 token 數字。",
        "sub": "請求與回應原封不動地串流通過；proxy 只複製一份用量數字，其他一概不留。",
        "client_lines": ("$ claude", "Thinking…"),
        "proxy_title": "Token Sprout proxy",
        "proxy_lines": ("零緩衝 · 不重試", "fail-open：絕不擋住 Claude"),
        "api_lines": ("與 Claude 原本連線的端點相同", "登入憑證原樣透傳"),
        "request": "請求",
        "stream": "串流",
        "tee": "只取用量數字",
        "state_line": "~/.token-sprout/ · 0600 · 只有數字",
        "bus": "唯讀畫面",
        "r1": ("Claude Code 狀態列", "Claude 思考時出現"),
        "r2": ("VS Code 狀態列＋側邊欄", "插畫植物面板"),
        "r3": ("終端機", "token-sprout watch · status"),
        "new": "新增",
        "privacy": "prompt、回應與 API 金鑰只在記憶體中透傳——永不寫入磁碟或 log。",
        "label": "Token Sprout 運作方式：Claude Code 經本機 proxy 連到 Anthropic API，proxy 只把 token 用量數字寫入私有 state 檔，再由三個唯讀畫面顯示。",
    },
}


def _t(x, y, text, size, fill, weight=500, family=SANS, anchor="start", extra=""):
    a = "" if anchor == "start" else f' text-anchor="{anchor}"'
    return (
        f'<text x="{_f(x)}" y="{_f(y)}" font-family="{family}" font-size="{_f(size)}" '
        f'font-weight="{weight}" fill="{fill}"{a}{extra}>{_esc(text)}</text>'
    )


def _card(x, y, w, h, fill, line, rx=22, shadow=True):
    s = ' filter="url(#ts-soft)"' if shadow else ""
    return f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" rx="{rx}" fill="{fill}" stroke="{line}" stroke-width="1.5"{s}/>'


def _arrow(x1, y1, x2, y2, color, marker, dashed=False, width=2.5):
    dash = ' stroke-dasharray="7 7"' if dashed else ""
    return (
        f'<path d="M{_pt(x1, y1)}L{_pt(x2, y2)}" stroke="{color}" stroke-width="{_f(width)}" '
        f'stroke-linecap="round" fill="none" marker-end="url(#{marker})"{dash}/>'
    )


def _window(x, y, w, h, c, dark=True):
    fill = c["term"] if dark else c["editor"]
    return (
        f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" rx="10" fill="{fill}"/>'
        f'<circle cx="{_f(x + 14)}" cy="{_f(y + 13)}" r="3.6" fill="#F0A695"/>'
        f'<circle cx="{_f(x + 26)}" cy="{_f(y + 13)}" r="3.6" fill="#EED17D"/>'
        f'<circle cx="{_f(x + 38)}" cy="{_f(y + 13)}" r="3.6" fill="#83C28D"/>'
    )


def _cloud_icon(cx, cy, s, color):
    return (
        f'<g transform="translate({_f(cx)} {_f(cy)}) scale({_f(s)})">'
        f'<path d="M-22,10H20A12,12 0 0 0 20,-14A16,16 0 0 0 -10,-18A12,12 0 0 0 -22,10Z" fill="{color}"/></g>'
    )


def _file_icon(x, y, c):
    return (
        f'<path d="M{_pt(x, y)}h26l12,12v34a4,4 0 0 1 -4,4h-34a4,4 0 0 1 -4,-4v-42a4,4 0 0 1 4,-4z" '
        f'fill="{c["proxy"]}" stroke="{c["green"]}" stroke-width="2"/>'
        f'<path d="M{_pt(x + 26, y)}v12h12" fill="none" stroke="{c["green"]}" stroke-width="2"/>'
        f'<rect x="{_f(x + 7)}" y="{_f(y + 30)}" width="20" height="15" rx="3" fill="{c["green"]}"/>'
        f'<path d="M{_pt(x + 11, y + 30)}v-5a6,6 0 0 1 12,0v5" fill="none" stroke="{c["green"]}" stroke-width="2.4"/>'
    )


def flow_svg(theme: str, lang: str) -> str:
    """How-it-works diagram, 1280 × 850."""
    W, H = 1280, 850
    c = FLOW_THEME[theme]
    t = FLOW_TEXT[lang]
    defs = (
        '<filter id="ts-soft" x="-10%" y="-10%" width="120%" height="140%">'
        f'<feDropShadow dx="0" dy="6" stdDeviation="9" flood-color="{c["shadow"]}" '
        f'flood-opacity="{0.08 if theme == "light" else 0.35}"/></filter>'
        f'<marker id="ts-head" markerWidth="10" markerHeight="10" refX="7" refY="5" orient="auto" markerUnits="userSpaceOnUse">'
        f'<path d="M0,0L9,5L0,10Z" fill="{c["wire"]}"/></marker>'
        f'<marker id="ts-head-green" markerWidth="10" markerHeight="10" refX="7" refY="5" orient="auto" markerUnits="userSpaceOnUse">'
        f'<path d="M0,0L9,5L0,10Z" fill="{c["green"]}"/></marker>'
        f"{PLANT_DEFS}"
    )
    out = [f'<rect width="{W}" height="{H}" rx="28" fill="{c["bg"]}"/>']

    # Header.
    out.append(_t(72, 78, t["eyebrow"], 15, c["accent"], 800, extra=' letter-spacing="3"'))
    out.append(_t(72, 122, t["headline"], 34, c["ink"], 800))
    out.append(_t(72, 158, t["sub"], 18, c["muted"], 500))

    # Row A: client ⇄ proxy ⇄ API.
    ay, ah = 206, 156
    out.append(_card(72, ay, 320, ah, c["card"], c["line"]))
    out.append(_window(92, ay + 20, 280, 116, c))
    out.append(_t(108, ay + 66, t["client_lines"][0], 18, c["term_text"], 700, MONO))
    out.append(_t(108, ay + 94, t["client_lines"][1], 15, c["term_muted"], 500, MONO))
    out.append(f'<rect x="106" y="{ay + 106}" width="252" height="18" rx="5" fill="#0F241D"/>')
    out.append(mini_sprout(120, ay + 116, 0.75) + mini_sprout(136, ay + 116, 0.75))
    out.append(_t(150, ay + 120, "2/20 · 67%", 12, c["term_text"], 600, MONO))
    out.append(_t(232, ay - 14, "Claude Code", 20, c["ink"], 800, anchor="middle"))

    out.append(_card(480, ay, 320, ah, c["proxy"], c["proxy_line"]))
    out.append(f'<circle cx="530" cy="{ay + 52}" r="26" fill="{c["card"]}"/>')
    out.append(f'<g transform="translate(530 {ay + 52})">'
               f'<path d="M0,14V-2" stroke="{PLANT["stem"]}" stroke-width="3" stroke-linecap="round"/>'
               f'<path d="M0,0C-3,-9 -11,-12 -16,-9C-14,-2 -7,1 0,0Z" fill="{PLANT["leaf_top_lit"]}"/>'
               f'<path d="M0,-2C3,-12 12,-15 18,-11C15,-3 7,0 0,-2Z" fill="{PLANT["leaf_top_shade"]}"/></g>')
    out.append(_t(570, ay + 46, t["proxy_title"], 21, c["ink"], 800))
    out.append(_t(570, ay + 72, "127.0.0.1:8000", 15, c["green"], 700, MONO))
    out.append(f'<path d="M500,{ay + 96}H780" stroke="{c["proxy_line"]}" stroke-width="1.5"/>')
    out.append(_t(640, ay + 122, t["proxy_lines"][0], 15, c["muted"], 600, anchor="middle"))
    out.append(_t(640, ay + 144, t["proxy_lines"][1], 15, c["muted"], 600, anchor="middle"))
    out.append(_t(640, ay - 14, "Local proxy" if lang == "en" else "本機 proxy", 20, c["ink"], 800, anchor="middle"))

    out.append(_card(888, ay, 320, ah, c["card"], c["line"]))
    out.append(f'<circle cx="940" cy="{ay + 56}" r="30" fill="{c["proxy"]}"/>')
    out.append(_cloud_icon(941, ay + 59, 0.95, c["wire"]))
    out.append(_t(984, ay + 62, "Anthropic API", 21, c["ink"], 800))
    out.append(f'<path d="M908,{ay + 96}H1188" stroke="{c["line"]}" stroke-width="1.5"/>')
    out.append(_t(1048, ay + 122, t["api_lines"][0], 15, c["muted"], 600, anchor="middle"))
    out.append(_t(1048, ay + 144, t["api_lines"][1], 15, c["muted"], 600, anchor="middle"))
    out.append(_t(1048, ay - 14, "Upstream" if lang == "en" else "上游", 20, c["ink"], 800, anchor="middle"))

    for x1, x2 in ((392, 480), (800, 888)):
        out.append(_arrow(x1 + 10, ay + 58, x2 - 12, ay + 58, c["wire"], "ts-head"))
        out.append(_arrow(x2 - 10, ay + 98, x1 + 12, ay + 98, c["wire"], "ts-head"))
        mid = (x1 + x2) / 2
        out.append(_t(mid, ay + 46, t["request"], 13, c["faint"], 700, anchor="middle"))
        out.append(_t(mid, ay + 122, t["stream"], 13, c["faint"], 700, anchor="middle"))

    # Tee: usage numbers only → state file.
    out.append(_arrow(640, ay + ah + 8, 640, 444, c["green"], "ts-head-green", dashed=True))
    tee_w = 190 if lang == "en" else 150
    out.append(f'<rect x="{_f(658)}" y="{ay + ah + 22}" width="{tee_w}" height="30" rx="15" fill="{c["proxy"]}" stroke="{c["proxy_line"]}"/>')
    out.append(_t(658 + tee_w / 2, ay + ah + 42, t["tee"], 14, c["green"], 700, anchor="middle"))

    sy = 450
    out.append(_card(430, sy, 420, 84, c["card"], c["proxy_line"]))
    out.append(_file_icon(456, sy + 15, c))
    out.append(_t(516, sy + 38, "plant_state.json", 19, c["ink"], 800, MONO))
    out.append(_t(516, sy + 64, t["state_line"], 14, c["muted"], 600, MONO))

    # Bus to the three read-only views.
    by = 586
    out.append(f'<path d="M640,{sy + 84}V{by}M232,{by}H1048" stroke="{c["wire"]}" stroke-width="2" fill="none"/>')
    for x in (232, 640, 1048):
        out.append(_arrow(x, by, x, by + 26, c["wire"], "ts-head"))
    bus_w = 150 if lang == "en" else 104
    out.append(f'<rect x="{_f(660)}" y="{by - 34}" width="{bus_w}" height="26" rx="13" fill="{c["bg"]}" stroke="{c["line"]}"/>')
    out.append(_t(660 + bus_w / 2, by - 16, t["bus"], 13, c["faint"], 700, anchor="middle"))

    ry, rh = 618, 134
    for i, x in enumerate((72, 480, 888)):
        out.append(_card(x, ry, 320, rh, c["card"], c["line"]))
    # Reader 1: Claude Code status line.
    out.append(_t(96, ry + 36, t["r1"][0], 18, c["ink"], 800))
    out.append(_t(96, ry + 60, t["r1"][1], 14, c["muted"], 600))
    out.append(f'<rect x="96" y="{ry + 78}" width="272" height="34" rx="9" fill="{c["term"]}"/>')
    out.append(mini_sprout(114, ry + 95, 0.85) + mini_sprout(132, ry + 95, 0.85))
    out.append(_t(146, ry + 100, "2/20 ·", 13, c["term_text"], 600, MONO))
    out.append(droplet(206, ry + 95, 12))
    cells = "".join(
        f'<rect x="{_f(218 + i * 9)}" y="{ry + 89}" width="7" height="12" rx="1.5" fill="{"#8FD07D" if i < 6 else "#2C4A40"}"/>'
        for i in range(10)
    )
    out.append(cells + _t(312, ry + 100, "67%", 13, c["term_text"], 600, MONO))
    # Reader 2: VS Code (generic editor window, no product marks).
    out.append(_t(504, ry + 36, t["r2"][0], 18, c["ink"], 800))
    out.append(_t(504, ry + 60, t["r2"][1], 14, c["muted"], 600))
    badge_w = 52 if lang == "en" else 48
    out.append(f'<rect x="{_f(800 - 20 - badge_w)}" y="{ry + 18}" width="{badge_w}" height="24" rx="12" fill="{c["badge"]}"/>')
    out.append(_t(800 - 20 - badge_w / 2, ry + 35, t["new"], 12, c["badge_text"], 800, anchor="middle", extra=' letter-spacing="1"'))
    ex, ey = 504, ry + 72
    out.append(f'<rect x="{ex}" y="{ey}" width="272" height="50" rx="8" fill="{c["editor"]}" stroke="{c["line"]}"/>')
    out.append(f'<rect x="{ex}" y="{ey}" width="14" height="50" rx="4" fill="{c["code"]}"/>')
    out.append(f'<rect x="{ex + 18}" y="{ey + 4}" width="62" height="34" rx="4" fill="{c["proxy"]}"/>')
    out.append(f'<g transform="translate({ex + 30} {ey + 2}) scale(0.155)">{plant_group("bud")}</g>')
    for j, wv in enumerate((120, 90, 140)):
        out.append(f'<rect x="{ex + 90}" y="{ey + 9 + j * 10}" width="{wv}" height="5" rx="2.5" fill="{c["code"]}"/>')
    out.append(f'<rect x="{ex}" y="{ey + 40}" width="272" height="10" rx="0" fill="{c["editor_bar"]}"/>')
    out.append(mini_sprout(ex + 214, ey + 46, 0.42) + _t(ex + 222, ey + 48.5, "2/20 · 67%", 7.5, "#DDEFE2", 700, MONO))
    # Reader 3: terminal watch panel.
    out.append(_t(912, ry + 36, t["r3"][0], 18, c["ink"], 800))
    out.append(_t(912, ry + 60, t["r3"][1], 14, c["muted"], 600, MONO))
    out.append(f'<rect x="912" y="{ry + 72}" width="272" height="50" rx="8" fill="{c["term"]}"/>')
    out.append(f'<rect x="924" y="{ry + 80}" width="248" height="34" rx="6" fill="none" stroke="#4E7D68" stroke-width="1.2"/>')
    for k in range(4):
        out.append(mini_sprout(944 + k * 22, ry + 100, 0.8))
    out.append(_t(1040, ry + 102, "sprout · 4/20", 11, c["term_muted"], 600, MONO))

    # Privacy line.
    out.append(f'<g transform="translate(72 {H - 38})"><rect x="0" y="-15" width="20" height="16" rx="3" fill="{c["green"]}"/>'
               f'<path d="M4,-15v-5a6,6 0 0 1 12,0v5" fill="none" stroke="{c["green"]}" stroke-width="2.4"/></g>')
    out.append(_t(104, H - 39, t["privacy"], 16, c["muted"], 600))

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'role="img" aria-label="{_esc(t["label"])}"><title>{_esc(t["label"])}</title>'
        f"<defs>{defs}</defs>{''.join(out)}</svg>\n"
    )


GROWTH_TEXT = {
    "en": {
        "eyebrow": "GROWTH",
        "headline": "Every 10,000 tokens plants a seed. Twenty of a kind grow into the next stage.",
        "names": ("Seed", "Sprout", "Leaf", "Bud", "Bloom"),
        "costs": ("10,000 tokens", "200,000 tokens", "4 million tokens", "80 million tokens", "1.6 billion tokens"),
        "each": ("each", "each", "each", "each", "per generation"),
        "formula": "10,000 × 20⁴ = 1,600,000,000 food tokens to bloom — the next feeding starts a new generation.",
        "food": "Food = input + output + cache writes. Cache reads are counted but never eaten.",
        "label": "Growth ladder: seed, sprout, leaf, bud, bloom. Twenty units of one stage merge into one unit of the next; bloom takes 1.6 billion food tokens.",
    },
    "zh-TW": {
        "eyebrow": "成長規則",
        "headline": "每 10,000 個 token 種下一顆種子；同階湊滿 20 顆，就長成下一階。",
        "names": ("種子", "幼芽", "盆栽", "花苞", "開花"),
        "costs": ("1 萬 token", "20 萬 token", "400 萬 token", "8,000 萬 token", "16 億 token"),
        "each": ("每顆", "每顆", "每盆", "每朵", "每一世代"),
        "formula": "10,000 × 20⁴ = 1,600,000,000 個食物 token 才開花——下一次進食開始新的世代。",
        "food": "食物 = input + output + cache 寫入；cache 讀取會記錄，但不餵植物。",
        "label": "成長階梯：種子、幼芽、盆栽、花苞、開花。同階 20 顆合成下一階 1 顆；開花需要 16 億個食物 token。",
    },
}


def growth_svg(theme: str, lang: str) -> str:
    """The 20:1 growth ladder, 1280 × 560."""
    W, H = 1280, 560
    c = FLOW_THEME[theme]
    s = SCENE[theme]
    t = GROWTH_TEXT[lang]
    top, mid, bottom = s["sky"]
    defs = (
        '<linearGradient id="ts-tile" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{top}"/><stop offset="0.6" stop-color="{mid}"/>'
        f'<stop offset="1" stop-color="{bottom}"/></linearGradient>'
        '<filter id="ts-soft" x="-10%" y="-10%" width="120%" height="140%">'
        f'<feDropShadow dx="0" dy="6" stdDeviation="9" flood-color="{c["shadow"]}" '
        f'flood-opacity="{0.08 if theme == "light" else 0.35}"/></filter>'
        f"{PLANT_DEFS}"
    )
    out = [f'<rect width="{W}" height="{H}" rx="28" fill="{c["bg"]}"/>']
    out.append(_t(72, 78, t["eyebrow"], 15, c["accent"], 800, extra=' letter-spacing="3"'))
    out.append(_t(72, 120, t["headline"], 30, c["ink"], 800))

    centers = (168, 404, 640, 876, 1112)
    tile_w, tile_y, tile_h = 184, 158, 268
    ground = s["hills"][3]
    for i, (cx, stage) in enumerate(zip(centers, STAGES)):
        x = cx - tile_w / 2
        is_bloom = stage == "bloom"
        line = c["accent"] if is_bloom else c["line"]
        out.append(
            f'<rect x="{_f(x)}" y="{tile_y}" width="{tile_w}" height="{tile_h}" rx="24" '
            f'fill="{c["card"]}" stroke="{line}" stroke-width="{2 if is_bloom else 1.5}" filter="url(#ts-soft)"/>'
        )
        out.append(
            f'<clipPath id="ts-tile-{stage}"><rect x="{_f(x + 10)}" y="{tile_y + 10}" width="{tile_w - 20}" '
            f'height="170" rx="16"/></clipPath>'
        )
        out.append(
            f'<g clip-path="url(#ts-tile-{stage})"><rect x="{_f(x + 10)}" y="{tile_y + 10}" width="{tile_w - 20}" '
            f'height="170" fill="url(#ts-tile)"/>'
            f'<path d="M{_f(x)},{tile_y + 162}Q{_f(cx)},{tile_y + 148} {_f(x + tile_w)},{tile_y + 162}V{tile_y + 190}H{_f(x)}Z" '
            f'fill="{ground}" opacity="0.9"/>'
            f"{place(stage, cx, tile_y + 168, 0.66)}</g>"
        )
        out.append(_t(cx, tile_y + 214, t["names"][i], 21, c["ink"], 800, anchor="middle"))
        out.append(_t(cx, tile_y + 238, t["costs"][i], 14, c["accent"] if is_bloom else c["green"], 700, anchor="middle"))
        out.append(_t(cx, tile_y + 257, t["each"][i], 12, c["faint"], 600, anchor="middle"))
        if i < len(STAGES) - 1:
            mx = (cx + centers[i + 1]) / 2
            out.append(
                f'<rect x="{_f(mx - 22)}" y="{tile_y + 80}" width="44" height="30" rx="15" '
                f'fill="{c["proxy"]}" stroke="{c["proxy_line"]}"/>'
            )
            out.append(_t(mx, tile_y + 100, "×20", 14, c["green"], 800, anchor="middle"))

    out.append(f'<path d="M72,{tile_y + tile_h + 34}H{W - 72}" stroke="{c["line"]}" stroke-width="1.5"/>')
    out.append(_t(72, tile_y + tile_h + 70, t["formula"], 18, c["ink"], 700))
    out.append(_t(72, tile_y + tile_h + 98, t["food"], 16, c["muted"], 600))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'role="img" aria-label="{_esc(t["label"])}"><title>{_esc(t["label"])}</title>'
        f"<defs>{defs}</defs>{''.join(out)}</svg>\n"
    )


# ---------------------------------------------------------------------------
# VS Code extension media.

def scene_svg(theme: str) -> str:
    """Sidebar backdrop (sky, orb, hills, no plant), scaled with slice."""
    W, H = 400, 260
    orb = (300, 112, 40) if theme == "light" else (318, 64, 22)
    sky_defs, sky = _sky(theme, W, H, *orb)
    birds = _birds(theme, [(92, 70, 0.8), (114, 80, 0.6)]) if theme == "light" else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        'preserveAspectRatio="xMidYMax slice" aria-hidden="true">'
        f"<defs>{sky_defs}</defs>{sky}{_cloud(theme, 40, 112, 70)}{birds}{_hills(theme, W, H)}</svg>\n"
    )


def icon_svg() -> str:
    """Marketplace icon: the sprout at dawn in a rounded tile."""
    W = H = 256
    sky_defs, sky = _sky("light", W, H, 186, 112, 40)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
        f'<defs>{PLANT_DEFS}{sky_defs}<clipPath id="ts-icon"><rect width="{W}" height="{H}" rx="56"/></clipPath></defs>'
        f'<g clip-path="url(#ts-icon)">{sky}{_hills("light", W, H)}{place("sprout", 128, 236, 1.06)}</g></svg>\n'
    )


def activity_icon_svg() -> str:
    """Monochrome activity-bar icon (VS Code tints it as a mask)."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">'
        '<path d="M12 14.5V9" stroke="#C5C5C5" stroke-width="1.6" stroke-linecap="round" fill="none"/>'
        '<path d="M11.6 9.6C10.9 6.6 8.2 5 5.2 5.5 5.5 8.6 8.2 10.4 11.6 9.6Z" fill="#C5C5C5"/>'
        '<path d="M12.4 8.6C13.2 5.4 16.2 3.6 19.3 4.2 18.9 7.6 16 9.5 12.4 8.6Z" fill="#C5C5C5"/>'
        '<path d="M5.5 14h13l-.5 2H6Z" fill="#C5C5C5"/>'
        '<path d="M6.5 17h11l-1.3 4.2c-.1.5-.6.8-1.1.8H8.9c-.5 0-1-.3-1.1-.8Z" fill="#C5C5C5"/></svg>\n'
    )


# ---------------------------------------------------------------------------

def outputs() -> dict[Path, str]:
    files: dict[Path, str] = {}
    assets = ROOT / "docs" / "assets"
    media = ROOT / "vscode-extension" / "media"
    for stage in STAGES:
        files[assets / "plants" / f"{stage}.svg"] = plant_svg(stage)
        files[media / "plants" / f"{stage}.svg"] = plant_svg(stage)
    for theme in ("light", "dark"):
        files[media / f"scene-{theme}.svg"] = scene_svg(theme)
    files[assets / "icon.svg"] = icon_svg()
    files[assets / "social-preview.svg"] = hero_svg("light", "en", radius=0)
    files[media / "activity.svg"] = activity_icon_svg()
    for theme in ("light", "dark"):
        for lang, suffix in (("en", ""), ("zh-TW", ".zh-TW")):
            files[assets / f"hero-{theme}{suffix}.svg"] = hero_svg(theme, lang)
            files[assets / f"how-it-works-{theme}{suffix}.svg"] = flow_svg(theme, lang)
            files[assets / f"growth-{theme}{suffix}.svg"] = growth_svg(theme, lang)
    return files


PNG_RENDERS = {
    # source SVG (relative to docs/assets) -> (output, width, height)
    "icon.svg": (ROOT / "vscode-extension" / "media" / "icon.png", 256, 256),
    "social-preview.svg": (ROOT / "docs" / "assets" / "social-preview.png", 1280, 640),
}

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
    "chromium-browser",
)


def render_pngs() -> int:
    """Rasterize the PNG assets with headless Chrome (binary output, so it
    stays outside --check)."""
    import shutil
    import subprocess

    chrome = next((c for c in CHROME_CANDIDATES if shutil.which(c) or Path(c).exists()), None)
    if chrome is None:
        print("headless Chrome not found; skipped PNG rendering", file=sys.stderr)
        return 1
    for name, (target, width, height) in PNG_RENDERS.items():
        source = ROOT / "docs" / "assets" / name
        subprocess.run(
            [
                chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                "--force-device-scale-factor=1", "--default-background-color=00000000",
                f"--window-size={width},{height}",
                f"--screenshot={target}", source.as_uri(),
            ],
            check=True,
            capture_output=True,
        )
        print(f"rendered {target.relative_to(ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if a generated file is stale")
    parser.add_argument("--png", action="store_true", help="also rasterize PNGs with headless Chrome")
    args = parser.parse_args(argv)

    stale = []
    for path, text in outputs().items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == text:
            continue
        if args.check:
            stale.append(path.relative_to(ROOT))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    if stale:
        print("stale generated art (run python scripts/build_art.py):", file=sys.stderr)
        for path in stale:
            print(f"  {path}", file=sys.stderr)
        return 1
    if args.png:
        return render_pngs()
    return 0


if __name__ == "__main__":
    sys.exit(main())
