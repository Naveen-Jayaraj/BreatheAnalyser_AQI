"""Compatibility wrapper for h3 package APIs across versions."""

from __future__ import annotations

from typing import Callable

try:
    import h3  # type: ignore[import-not-found]
except ModuleNotFoundError as exc:  # pragma: no cover
    h3 = None  # type: ignore[assignment]
    _H3_IMPORT_ERROR = exc
else:
    _H3_IMPORT_ERROR = None


def _resolve(name: str, legacy_name: str) -> Callable:
    if h3 is None:  # pragma: no cover
        raise RuntimeError(
            "The 'h3' package is required for coordinate resolution. "
            "Install dependencies with: pip install -e ."
        ) from _H3_IMPORT_ERROR
    fn = getattr(h3, name, None)
    if fn is not None:
        return fn
    legacy = getattr(h3, legacy_name, None)
    if legacy is not None:
        return legacy
    raise RuntimeError(f"h3 function '{name}'/'{legacy_name}' is not available")


_latlng_to_cell = _resolve("latlng_to_cell", "geo_to_h3")
_cell_to_parent = _resolve("cell_to_parent", "h3_to_parent")
_grid_disk = _resolve("grid_disk", "k_ring")
_cell_to_latlng = _resolve("cell_to_latlng", "h3_to_geo")


def latlng_to_cell(lat: float, lon: float, res: int) -> str:
    """Return H3 cell for coordinate at a given resolution."""
    return str(_latlng_to_cell(lat, lon, res))


def cell_to_parent(cell: str, res: int) -> str:
    """Return parent H3 cell at resolution ``res``."""
    return str(_cell_to_parent(cell, res))


def grid_disk(cell: str, k: int) -> tuple[str, ...]:
    """Return neighborhood cells up to ``k`` rings around ``cell``."""
    cells = _grid_disk(cell, k)
    return tuple(str(item) for item in cells)


def cell_to_latlng(cell: str) -> tuple[float, float]:
    """Return centroid `(lat, lon)` for an H3 cell."""
    lat, lon = _cell_to_latlng(cell)
    return float(lat), float(lon)


def bbox_to_cells(
    *,
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    res: int,
) -> tuple[str, ...]:
    """
    Return H3 cells for a bbox.

    Uses native polygon APIs when available, then falls back to a disk scan.
    """
    if h3 is not None:
        geo_to_cells = getattr(h3, "geo_to_cells", None)
        if callable(geo_to_cells):
            payload = {
                "type": "Polygon",
                "coordinates": [
                    [
                        [min_lon, min_lat],
                        [max_lon, min_lat],
                        [max_lon, max_lat],
                        [min_lon, max_lat],
                        [min_lon, min_lat],
                    ]
                ],
            }
            try:
                return tuple(str(item) for item in geo_to_cells(payload, res))
            except Exception:
                pass

    center_lat = (min_lat + max_lat) / 2.0
    center_lon = (min_lon + max_lon) / 2.0
    center = latlng_to_cell(center_lat, center_lon, res)

    lat_span = max(0.0, max_lat - min_lat)
    lon_span = max(0.0, max_lon - min_lon)
    span_km = max(lat_span, lon_span) * 111.0
    ring_k = max(1, int(span_km / 2.0))

    matched: list[str] = []
    for cell in grid_disk(center, ring_k):
        lat, lon = cell_to_latlng(cell)
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            matched.append(cell)
    return tuple(matched)
