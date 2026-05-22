"""Parse filament metadata out of .gcode and .bgcode files.

Centralized so the upload path (and any future backfill / inspection
tooling) goes through one function. The Phase 6 fix lands here: parse
at upload time, persist to upload_sessions, copy onto print_jobs at
start time — bypassing the post-FINISHED /api/v1/job blank-payload bug.

Library path was attempted: ``pip install gcode-metadata`` failed
(package isn't on PyPI), so this module is the in-tree fallback the
phase prompt called for.

Coverage:
- ``.gcode`` (PrusaSlicer plaintext): tails the file and regex-scans
  the ``; key = value`` slicer-comment block that lives at the end.
  Robust — this is the same block every PrusaSlicer / SuperSlicer
  preset emits.
- ``.bgcode`` (binary G-code): best-effort. Reads the GCDE header,
  walks blocks, and if the SlicerMetadata block is uncompressed,
  parses key/value pairs from it. Compressed blocks (heatshrink /
  zstd / deflate) are not decoded in-tree — parse_error is set and
  callers fall through to the filename / mm_estimate chain. As a
  cheap secondary, also scans the first ~64KB raw for the same
  ASCII ``; filament used [g] = `` markers, which catches some
  uncompressed slicer dumps embedded in bgcode files.

Never raises. Returns a dict with the schema below, with parse_error
set on any failure and all other fields None:

    {
        "parsed_filament_used_g": float | None,
        "parsed_filament_used_mm": float | None,
        "parsed_filament_used_g_per_tool": str (JSON list) | None,
        "parsed_filament_used_mm_per_tool": str (JSON list) | None,
        "parsed_filament_type": str | None,
        "parsed_layer_height": float | None,
        "parsed_nozzle_diameter": float | None,
        "parsed_fill_density": float | None,
        "parsed_nozzle_temp": float | None,
        "parsed_bed_temp": float | None,
        "parsed_at": ISO-8601 timestamp,
        "parse_error": str | None,
    }
"""

import json as _json
import os
import re
import struct
from datetime import datetime, timezone


# Tail bytes scanned for plaintext .gcode and for the bgcode raw fallback.
_GCODE_TAIL_BYTES = 32 * 1024
_BGCODE_RAW_SCAN_BYTES = 64 * 1024

# Slicer-comment regex shapes. PrusaSlicer / SuperSlicer emit
# `; key = value` (with optional surrounding spaces).
_NUMERIC_RE_TEMPLATE = (
    r"^\s*;\s*{key}\s*=\s*(?P<value>-?\d+(?:\.\d+)?)"
)
_TEXT_RE_TEMPLATE = (
    r"^\s*;\s*{key}\s*=\s*(?P<value>.+?)\s*$"
)
_PERCENT_RE_TEMPLATE = (
    r"^\s*;\s*{key}\s*=\s*(?P<value>-?\d+(?:\.\d+)?)%?"
)
_LIST_RE_TEMPLATE = (
    r"^\s*;\s*{key}\s*=\s*(?P<value>.+?)\s*$"
)


def _numeric_re(key):
    return re.compile(_NUMERIC_RE_TEMPLATE.format(key=re.escape(key)),
                      re.MULTILINE)


def _text_re(key):
    return re.compile(_TEXT_RE_TEMPLATE.format(key=re.escape(key)),
                      re.MULTILINE)


def _percent_re(key):
    return re.compile(_PERCENT_RE_TEMPLATE.format(key=re.escape(key)),
                      re.MULTILINE)


def _list_re(key):
    return re.compile(_LIST_RE_TEMPLATE.format(key=re.escape(key)),
                      re.MULTILINE)


# Multiple alternates for each field — slicer presets vary slightly.
_KEY_VARIANTS = {
    "filament_used_g": [
        "filament used [g]", "filament_used_g", "total filament used [g]",
    ],
    "filament_used_mm": [
        "filament used [mm]", "filament_used_mm", "total filament used [mm]",
    ],
    "filament_type": ["filament_type", "filament type"],
    "layer_height": ["layer_height", "layer height"],
    "nozzle_diameter": ["nozzle_diameter", "nozzle diameter"],
    "fill_density": ["fill_density", "fill density"],
    "nozzle_temp": [
        "nozzle_temperature", "first_layer_temperature",
        "nozzle_temp", "temperature",
    ],
    "bed_temp": [
        "bed_temperature", "first_layer_bed_temperature", "bed_temp",
    ],
}

# Per-tool variants. PrusaSlicer writes ``; filament used [g] per tool = 1.2, 0, 3.4``
# or comma-separated arrays in the slicer metadata block.
_PER_TOOL_KEY_VARIANTS = {
    "filament_used_g_per_tool": [
        "filament used [g] per tool", "filament_used_g_per_tool",
    ],
    "filament_used_mm_per_tool": [
        "filament used [mm] per tool", "filament_used_mm_per_tool",
    ],
}


def _empty_result():
    return {
        "parsed_filament_used_g": None,
        "parsed_filament_used_mm": None,
        "parsed_filament_used_g_per_tool": None,
        "parsed_filament_used_mm_per_tool": None,
        "parsed_filament_type": None,
        "parsed_layer_height": None,
        "parsed_nozzle_diameter": None,
        "parsed_fill_density": None,
        "parsed_nozzle_temp": None,
        "parsed_bed_temp": None,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "parse_error": None,
    }


def _coerce_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_numeric(text, key_variants):
    for key in key_variants:
        match = _numeric_re(key).search(text)
        if match:
            value = _coerce_float(match.group("value"))
            if value is not None:
                return value
    return None


def _extract_percent(text, key_variants):
    for key in key_variants:
        match = _percent_re(key).search(text)
        if match:
            value = _coerce_float(match.group("value"))
            if value is not None:
                return value
    return None


def _extract_text(text, key_variants):
    for key in key_variants:
        match = _text_re(key).search(text)
        if match:
            value = match.group("value").strip().strip(";").strip()
            if value:
                return value
    return None


def _extract_list(text, key_variants):
    """Extract a comma-separated numeric list as a JSON string."""
    for key in key_variants:
        match = _list_re(key).search(text)
        if not match:
            continue
        raw = match.group("value").strip()
        if not raw:
            continue
        parts = [p.strip() for p in raw.split(",")]
        floats = []
        for part in parts:
            number = _coerce_float(part)
            if number is None:
                floats = []
                break
            floats.append(number)
        if floats:
            return _json.dumps(floats)
    return None


def _populate_from_text(result, text):
    """Walk the canonical key list against ``text`` and fill ``result``."""
    for field, variants in _KEY_VARIANTS.items():
        target_key = "parsed_" + field
        if result.get(target_key) is not None:
            continue
        if field == "fill_density":
            value = _extract_percent(text, variants)
        elif field == "filament_type":
            value = _extract_text(text, variants)
        else:
            value = _extract_numeric(text, variants)
        if value is not None:
            result[target_key] = value
    for field, variants in _PER_TOOL_KEY_VARIANTS.items():
        target_key = "parsed_" + field
        if result.get(target_key) is not None:
            continue
        value = _extract_list(text, variants)
        if value is not None:
            result[target_key] = value


def _parse_gcode(file_path, result):
    """Tail the file and scan for slicer comments."""
    size = os.path.getsize(file_path)
    read_bytes = min(size, _GCODE_TAIL_BYTES)
    with open(file_path, "rb") as handle:
        if size > read_bytes:
            handle.seek(size - read_bytes)
        tail = handle.read(read_bytes)
    text = tail.decode("utf-8", errors="replace")
    _populate_from_text(result, text)


# ---------------------------------------------------------------------------
# .bgcode parser (best-effort)
# ---------------------------------------------------------------------------

_BGCODE_MAGIC = b"GCDE"
_BLOCK_TYPE_SLICER_METADATA = 2
# Compression types per libbgcode spec.
_COMPRESSION_NONE = 0


def _parse_bgcode(file_path, result):
    """Walk the GCDE block structure looking for SlicerMetadata."""
    with open(file_path, "rb") as handle:
        header = handle.read(10)
        if len(header) < 10 or header[:4] != _BGCODE_MAGIC:
            raise ValueError("not a bgcode file (missing GCDE magic)")
        # header: magic(4) version(4 LE) checksum_type(2 LE)
        checksum_type = struct.unpack("<H", header[8:10])[0]

        parsed_any = False
        while True:
            block_header = handle.read(8)
            if len(block_header) < 8:
                break
            block_type, compression, uncompressed_size = struct.unpack(
                "<HHI", block_header
            )
            compressed_size = None
            if compression != _COMPRESSION_NONE:
                cs_bytes = handle.read(4)
                if len(cs_bytes) < 4:
                    break
                compressed_size = struct.unpack("<I", cs_bytes)[0]

            data_len = (compressed_size if compression != _COMPRESSION_NONE
                        else uncompressed_size)
            payload = handle.read(data_len)
            if len(payload) < data_len:
                break

            # checksum trailer (skip 4 bytes if crc32)
            if checksum_type != 0:
                handle.read(4)

            if (block_type == _BLOCK_TYPE_SLICER_METADATA
                    and compression == _COMPRESSION_NONE):
                text = payload.decode("utf-8", errors="replace")
                _populate_from_text(result, text)
                parsed_any = True

        if not parsed_any:
            # Secondary scan: many bgcode tools embed plain ASCII slicer
            # comments in the file. Try the first ~64KB raw.
            handle.seek(0)
            raw = handle.read(_BGCODE_RAW_SCAN_BYTES)
            try:
                text = raw.decode("utf-8", errors="replace")
                _populate_from_text(result, text)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def parse_print_metadata(file_path):
    """Extract filament metadata from a .gcode or .bgcode file.

    Returns the schema described at the module docstring. Never raises.
    """
    result = _empty_result()
    if not file_path or not os.path.exists(file_path):
        result["parse_error"] = "file not found"
        return result

    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext in (".gcode", ".gco", ".g"):
            _parse_gcode(file_path, result)
        elif ext == ".bgcode":
            _parse_bgcode(file_path, result)
        else:
            result["parse_error"] = "unsupported extension: {}".format(ext)
            return result
    except Exception as exc:
        result["parse_error"] = "{}: {}".format(type(exc).__name__, exc)
        return result

    if result["parsed_filament_used_g"] is None and not result["parse_error"]:
        # We never found the canonical grams marker. Not a hard failure —
        # callers still get whatever else we recovered — but flag it so
        # diagnostics can spot files the parser didn't fully understand.
        result["parse_error"] = "filament_used_g not found in slicer block"

    return result
