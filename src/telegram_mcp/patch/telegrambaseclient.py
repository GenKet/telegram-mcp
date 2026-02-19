"""
Patch TelegramClient to support lang_pack and layer parameters.

Telethon hardcodes lang_pack='' in InitConnectionRequest.
This patch allows passing custom lang_pack value.
"""

from telethon import TelegramClient
from telethon.tl.alltlobjects import LAYER

# Store original __init__
_original_init = TelegramClient.__init__


def _patched_init(
    self,
    session,
    api_id,
    api_hash,
    *,
    lang_pack="",  # Add lang_pack parameter (default empty like original)
    layer=None,     # Add layer parameter for future use
    **kwargs
):
    """
    Patched __init__ that accepts lang_pack and layer parameters.

    After calling original __init__, modifies _init_request.lang_pack
    to use the provided value instead of hardcoded empty string.
    """
    # Store custom parameters
    self._custom_lang_pack = lang_pack
    self._custom_layer = layer if layer is not None else LAYER

    # Call original init (this creates _init_request with lang_pack='')
    _original_init(self, session, api_id, api_hash, **kwargs)

    # Now override the hardcoded lang_pack value
    if hasattr(self, '_init_request') and self._init_request:
        self._init_request.lang_pack = lang_pack


def apply_patch():
    """Apply the lang_pack patch to TelegramClient."""
    TelegramClient.__init__ = _patched_init


# Apply patch on import
apply_patch()
