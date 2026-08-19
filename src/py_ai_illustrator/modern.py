"""Public facade for the modern AI reader.

The implementation lives in :mod:`._modern_container`, which owns PDF
syntax, object resolution, bounded stream codecs, and PrivateData section
discovery.  Keeping this compatibility facade preserves the historical
``py_ai_illustrator.modern`` import path while making the dependency boundary
explicit for internal modules.
"""

from ._modern_container import (
    ModernAIReadResult,
    ModernDiagnostic,
    ModernReadLimits,
    PdfRef,
    PrivateDataSection,
    PrivateDataSegment,
    PrivateDataToken,
    read_modern_ai,
    tokenize_private_data,
)

__all__ = [
    "ModernAIReadResult",
    "ModernDiagnostic",
    "ModernReadLimits",
    "PdfRef",
    "PrivateDataSection",
    "PrivateDataSegment",
    "PrivateDataToken",
    "read_modern_ai",
    "tokenize_private_data",
]
