"""
Filament usage helpers shared across upload, deduction, and production
logging paths.
"""

import os
import re
from typing import Iterable, Optional

MM_TO_GRAMS_FACTOR = 0.00298

FILAMENT_SOURCE_API = "api"
FILAMENT_SOURCE_FILENAME = "filename"
FILAMENT_SOURCE_MM_ESTIMATE = "mm_estimate"
FILAMENT_SOURCE_NONE = "none"

_FILENAME_GRAMS_RE = re.compile(
    r"(?<![0-9A-Za-z])(?P<grams>\d+(?:\.\d+)?)g(?![0-9A-Za-z])",
    re.IGNORECASE,
)


def coerce_nonnegative_float(value) -> float:
    """Convert a numeric-ish value into a non-negative float."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number >= 0 else 0.0


def coerce_positive_float(value) -> Optional[float]:
    """Convert a numeric-ish value into a positive float."""
    number = coerce_nonnegative_float(value)
    return number if number > 0 else None


def extract_grams_from_filename(file_name: str) -> Optional[float]:
    """Extract a single standalone `12.3g` token from a G-code filename."""
    if not file_name:
        return None

    stem = os.path.splitext(os.path.basename(str(file_name)))[0]
    if not stem:
        return None

    matches = [m.group("grams") for m in _FILENAME_GRAMS_RE.finditer(stem)]
    if len(matches) != 1:
        return None

    try:
        grams = float(matches[0])
    except (TypeError, ValueError):
        return None
    return grams if grams > 0 else None


def estimate_grams_from_mm(mm_used) -> Optional[float]:
    """Convert filament millimeters into grams using the app's estimate."""
    mm_value = coerce_positive_float(mm_used)
    if mm_value is None:
        return None
    return mm_value * MM_TO_GRAMS_FACTOR


def resolve_total_filament_usage(filament_used_g=0, filament_used_mm=0,
                                 filename_candidates: Iterable[str] = None,
                                 include_mm_estimate: bool = True) -> dict:
    """Resolve total filament grams using API grams, filename, then mm."""
    grams = coerce_positive_float(filament_used_g)
    mm_used = coerce_nonnegative_float(filament_used_mm)
    if grams is not None:
        return {
            "grams": grams,
            "mm_used": mm_used,
            "source": FILAMENT_SOURCE_API,
            "filename": None,
        }

    seen = set()
    for candidate in filename_candidates or []:
        text = str(candidate or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        parsed = extract_grams_from_filename(text)
        if parsed is not None:
            return {
                "grams": parsed,
                "mm_used": mm_used,
                "source": FILAMENT_SOURCE_FILENAME,
                "filename": text,
            }

    if include_mm_estimate:
        estimated = estimate_grams_from_mm(mm_used)
        if estimated is not None:
            return {
                "grams": estimated,
                "mm_used": mm_used,
                "source": FILAMENT_SOURCE_MM_ESTIMATE,
                "filename": None,
            }

    return {
        "grams": 0.0,
        "mm_used": mm_used,
        "source": FILAMENT_SOURCE_NONE,
        "filename": None,
    }
