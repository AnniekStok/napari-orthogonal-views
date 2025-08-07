try:
    from ._version import version as __version__
except ImportError:
    __version__ = "unknown"

from ._widget import MultipleViewerWidget
from .cross_widget import CrossWidget

__all__ = ("MultipleViewerWidget", "CrossWidget")
