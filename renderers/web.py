"""
renderers/web.py — version router
Delegates to renderers/web_templates/v{WEB_VERSION}/renderer.py.
Set WEB_VERSION in config/settings.py (default: 1).
"""

from config.settings import WEB_VERSION as _WEB_VERSION

if _WEB_VERSION == 1:
    from renderers.web_templates.v1.renderer import render
elif _WEB_VERSION == 2:
    from renderers.web_templates.v2.renderer import render
else:
    raise ImportError(
        f"Unknown WEB_VERSION: {_WEB_VERSION!r}  "
        f"(config/settings.py). Valid values: 1, 2"
    )
