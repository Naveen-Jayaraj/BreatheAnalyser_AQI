"""Build and load H3 properties lookup artifacts."""

from __future__ import annotations

import argparse
import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class GridPropertiesBuildResult:
    source_path: Path
    output_path: Path
    backend: str
    count: int


def build_h3_properties_index(
    *,
    source_path: str | Path,
    output_path: str | Path,
    backend: str = "pickle",
) -> GridPropertiesBuildResult:
    """
    Build `h3_index -> properties` map from GeoJSON and persist artifact.

    Supported backends:
    - `pickle`: built-in fast local file.
    - `parquet`: optional (requires `pandas` + parquet engine).
    - `redis`: optional (requires `redis` package + `redis://...` output path).
    """
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"Input file does not exist: {source}")

    with source.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    features = payload.get("features")
    if not isinstance(features, list):
        raise ValueError("GeoJSON payload missing features list")

    mapping: dict[str, dict[str, Any]] = {}
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            continue
        h3_index = properties.get("h3_index")
        if h3_index is None:
            continue
        key = str(h3_index).strip()
        if not key:
            continue
        mapping[key] = dict(properties)

    backend_normalized = backend.strip().lower()
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if backend_normalized == "pickle":
        with out.open("wb") as handle:
            pickle.dump(mapping, handle, protocol=pickle.HIGHEST_PROTOCOL)
    elif backend_normalized == "parquet":
        _save_parquet(mapping, out)
    elif backend_normalized == "redis":
        _save_redis(mapping, str(output_path))
    else:
        raise ValueError(f"Unsupported backend: {backend}")

    return GridPropertiesBuildResult(
        source_path=source,
        output_path=out,
        backend=backend_normalized,
        count=len(mapping),
    )


def load_h3_properties_index(
    *,
    input_path: str | Path,
    backend: str = "pickle",
) -> dict[str, dict[str, Any]]:
    """Load previously generated H3 properties artifact."""
    backend_normalized = backend.strip().lower()
    if backend_normalized == "pickle":
        path = Path(input_path)
        with path.open("rb") as handle:
            payload = pickle.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("Pickle payload is not a dict")
        return {str(k): dict(v) for k, v in payload.items() if isinstance(v, dict)}
    if backend_normalized == "parquet":
        return _load_parquet(Path(input_path))
    if backend_normalized == "redis":
        return _load_redis(str(input_path))
    raise ValueError(f"Unsupported backend: {backend}")


def _save_parquet(mapping: dict[str, dict[str, Any]], output_path: Path) -> None:
    try:
        import pandas as pd  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Parquet backend requires pandas + pyarrow/fastparquet") from exc

    rows = []
    for h3_index, props in mapping.items():
        row = dict(props)
        row["h3_index"] = h3_index
        rows.append(row)
    df = pd.DataFrame(rows)
    df.to_parquet(output_path, index=False)


def _load_parquet(input_path: Path) -> dict[str, dict[str, Any]]:
    try:
        import pandas as pd  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Parquet backend requires pandas + pyarrow/fastparquet") from exc

    df = pd.read_parquet(input_path)
    mapping: dict[str, dict[str, Any]] = {}
    for row in df.to_dict(orient="records"):
        h3_index = row.get("h3_index")
        if h3_index is None:
            continue
        key = str(h3_index)
        payload = dict(row)
        payload.pop("h3_index", None)
        mapping[key] = payload
    return mapping


def _save_redis(mapping: dict[str, dict[str, Any]], redis_url: str) -> None:
    try:
        import redis  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Redis backend requires redis package") from exc

    client = redis.from_url(redis_url, decode_responses=True)
    pipe = client.pipeline(transaction=False)
    for h3_index, props in mapping.items():
        key = f"h3:properties:{h3_index}"
        pipe.set(key, json.dumps(props, separators=(",", ":")))
    pipe.execute()


def _load_redis(redis_url: str) -> dict[str, dict[str, Any]]:
    try:
        import redis  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Redis backend requires redis package") from exc

    client = redis.from_url(redis_url, decode_responses=True)
    keys = list(client.scan_iter(match="h3:properties:*"))
    mapping: dict[str, dict[str, Any]] = {}
    if not keys:
        return mapping

    values = client.mget(keys)
    for key, value in zip(keys, values, strict=False):
        if value is None:
            continue
        h3_index = str(key).replace("h3:properties:", "", 1)
        try:
            mapping[h3_index] = json.loads(value)
        except json.JSONDecodeError:
            continue
    return mapping


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build h3_index -> properties lookup artifact")
    parser.add_argument("--input", required=True, help="Path to source GeoJSON")
    parser.add_argument("--output", required=True, help="Artifact output path or redis:// URL")
    parser.add_argument(
        "--backend",
        default="pickle",
        choices=("pickle", "parquet", "redis"),
        help="Artifact backend",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    result = build_h3_properties_index(
        source_path=args.input,
        output_path=args.output,
        backend=args.backend,
    )
    print(
        "h3_properties_index_created",
        f"output={result.output_path}",
        f"backend={result.backend}",
        f"count={result.count}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
