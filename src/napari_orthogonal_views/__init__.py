try:
    from ._version import version as __version__
except ImportError:
    __version__ = "unknown"

from .ortho_view_widget import OrthoViewWidget

__all__ = ("OrthoViewWidget",)
