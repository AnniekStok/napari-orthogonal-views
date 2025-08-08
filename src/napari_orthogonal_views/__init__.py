try:
    from ._version import version as __version__
except ImportError:
    __version__ = "unknown"

from ._widget import OrthViewerWidget
from .cross_widget import CrossWidget

__all__ = ("OrthViewerWidget", "CrossWidget")
