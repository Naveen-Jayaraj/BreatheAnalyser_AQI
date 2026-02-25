"""Request/response models for backend API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LookupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    radius_km: float = Field(default=50.0, gt=0.0, le=500.0)
    power: float = Field(default=2.0, gt=0.0, le=6.0)
    use_diffusion: bool = True
    diffusion_alpha: float = Field(default=0.15, ge=0.0, le=1.0)
    include_trace: bool = False


class LookupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aqi: int = Field(..., ge=0, le=500)
    aqi_raw: float = Field(..., ge=0.0, le=500.0)
    pm25: float = Field(..., ge=0.0, le=500.0)
    category: str
    source: str
    trace: dict[str, Any] | None = None


class BatchLookupPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, max_length=128)
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)


class BatchLookupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points: list[BatchLookupPoint] = Field(..., min_length=1, max_length=100)
    radius_km: float = Field(default=50.0, gt=0.0, le=500.0)
    power: float = Field(default=2.0, gt=0.0, le=6.0)
    use_diffusion: bool = True
    diffusion_alpha: float = Field(default=0.15, ge=0.0, le=1.0)
    include_trace: bool = False

    @field_validator("points")
    @classmethod
    def _validate_unique_ids(cls, value: list[BatchLookupPoint]) -> list[BatchLookupPoint]:
        seen: set[str] = set()
        for idx, point in enumerate(value, start=1):
            if point.id is None:
                continue
            if point.id in seen:
                raise ValueError(f"Duplicate point id: {point.id!r} at index {idx}")
            seen.add(point.id)
        return value


class ApiError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class BatchLookupItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["ok", "error"]
    result: LookupResponse | None = None
    error: ApiError | None = None


class BatchLookupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    results: list[BatchLookupItem]


class ForecastHourItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hour: int = Field(..., ge=1, le=24)
    timestamp_utc: str
    aqi: int = Field(..., ge=0, le=500)
    aqi_raw: float = Field(..., ge=0.0, le=500.0)


class ForecastResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    source: str
    cached: bool
    h3_index: str
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    hours: int = Field(..., ge=1, le=24)
    current_aqi: int = Field(..., ge=0, le=500)
    current_aqi_raw: float = Field(..., ge=0.0, le=500.0)
    forecast: list[ForecastHourItem]
    elapsed_ms: float = Field(..., ge=0.0)
    trace: dict[str, Any] | None = None


class ForecastBboxCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    h3_index: str
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    current_aqi: int = Field(..., ge=0, le=500)
    current_aqi_raw: float = Field(..., ge=0.0, le=500.0)
    land_use: str | None = None
    forecast: list[ForecastHourItem]


class ForecastBboxResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    source: str
    resolution: int
    hours: int
    count: int
    cache_hits: int
    cache_misses: int
    elapsed_ms: float = Field(..., ge=0.0)
    cells: list[ForecastBboxCell]
    trace: dict[str, Any] | None = None


class ForecastValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    hours: int = Field(default=12, ge=1, le=24)
    observed_aqi: list[float] = Field(..., min_length=1, max_length=24)
    forecast_aqi: list[float] | None = Field(default=None, min_length=1, max_length=24)
    include_trace: bool = False


class ForecastValidationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    hours: int
    forecast_points: int
    observed_points: int
    metrics: dict[str, float]
    trace: dict[str, Any] | None = None
