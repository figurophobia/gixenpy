"""gixenpy — unofficial Python client for Gixen.com (eBay sniping)."""
from .client import (
    GixenClient,
    GixenError,
    Snipe,
    SnipeForm,
    SnipeResult,
    legacy_item_number,
)

__all__ = [
    "GixenClient",
    "GixenError",
    "Snipe",
    "SnipeForm",
    "SnipeResult",
    "legacy_item_number",
]

__version__ = "0.2.2"
