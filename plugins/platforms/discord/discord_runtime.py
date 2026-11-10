"""Publish a late discord.py install to every Discord adapter sibling.

The gateway imports this package before ``pm.ensure_import("discord")`` has
installed discord.py. Sibling mixins bind ``discord`` at import
(``except ImportError: discord = None``). Rebinding the adapter facade alone
leaves those ``None``s in place for the life of the process, so the first
``discord.DMChannel`` or ``discord.Thread`` access crashes.

``publish_discord`` is the one write. It covers every sibling already loaded
under this platform, including a mixin added later with the same import shape.
Call sites keep reading their own module global.
"""

from __future__ import annotations

import logging
import sys
from types import ModuleType

logger = logging.getLogger(__name__)

# Names the loader actually uses. The ``__file__`` check covers a sibling
# executed under any other name (flat import, synthetic package).
_PACKAGE_PREFIXES = (
    "plugins.platforms.discord",
    "hermes_plugins.platforms__discord",
)


def publish_discord(module: ModuleType) -> None:
    """Point every loaded Discord sibling whose ``discord`` is still None at *module*.

    A binding that is already set stays as it is, so a test double or a sibling
    that imported discord.py successfully is not replaced. Modules outside this
    platform are not touched.
    """
    if module is None:
        return
    healed: list[str] = []
    for name, mod in list(sys.modules.items()):
        if mod is None or not _is_discord_sibling(name, mod):
            continue
        namespace = getattr(mod, "__dict__", None)
        if not isinstance(namespace, dict) or "discord" not in namespace:
            continue
        if namespace["discord"] is None:
            namespace["discord"] = module
            healed.append(name)
    if healed:
        logger.debug("Published discord.py into %s", ", ".join(sorted(healed)))


def _is_discord_sibling(name: str, mod: ModuleType) -> bool:
    for prefix in _PACKAGE_PREFIXES:
        if name == prefix or name.startswith(prefix + "."):
            return True
    path = getattr(mod, "__file__", None)
    if not isinstance(path, str) or not path:
        return False
    normalized = path.replace("\\", "/")
    return "/plugins/platforms/discord/" in f"/{normalized}"
