"""Domain exceptions for AQI pipeline."""


class AQIPipelineError(Exception):
    """Base exception for pipeline errors."""


class InvalidCoordinateError(AQIPipelineError):
    """Raised when latitude/longitude are invalid."""


class OutsideIndiaCoverageError(AQIPipelineError):
    """Raised when coordinates are outside known India coverage hexes."""


class NoSensorDataError(AQIPipelineError):
    """Raised when no fresh sensor data exists for estimation."""


class CoverageIndexError(AQIPipelineError):
    """Raised when loading or using the coverage index fails."""
