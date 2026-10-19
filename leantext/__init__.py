"""leantext: convert anything an agent reads into clean Markdown."""
from .router import convert, Result

__version__ = "0.2.0"
__all__ = ["convert", "Result", "__version__"]
