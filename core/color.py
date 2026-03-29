"""
core/color.py — Shared hex colour utilities.

Used by all renderers so that brand-derived shades (dark/light tints)
are computed consistently regardless of output format.
"""


def hex_darken(hex_color: str, factor: float = 0.78) -> str:
    """
    Blend a hex color toward black.
    factor = how much of the original channel to keep (e.g. 0.78 = 22% darker).
    Returns a '#rrggbb' string.  Falls back to the input on parse failure.
    """
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = h[0]*2 + h[1]*2 + h[2]*2
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return "#{:02x}{:02x}{:02x}".format(
            max(0, min(255, int(r * factor))),
            max(0, min(255, int(g * factor))),
            max(0, min(255, int(b * factor))),
        )
    except Exception:
        return hex_color


def hex_lighten(hex_color: str, factor: float = 0.88) -> str:
    """
    Blend a hex color toward white.
    factor = fraction of white to mix in (e.g. 0.88 = 88% of the way to white).
    Returns a '#rrggbb' string.  Falls back to the input on parse failure.
    """
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = h[0]*2 + h[1]*2 + h[2]*2
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return "#{:02x}{:02x}{:02x}".format(
            max(0, min(255, int(r + (255 - r) * factor))),
            max(0, min(255, int(g + (255 - g) * factor))),
            max(0, min(255, int(b + (255 - b) * factor))),
        )
    except Exception:
        return hex_color
