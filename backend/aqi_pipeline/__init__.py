"""AQI pipeline package."""

__all__ = ["AQIEngine", "AqiResult", "predict_aqi"]


def __getattr__(name: str):
    if name == "AQIEngine":
        from .engine import AQIEngine

        return AQIEngine
    if name == "AqiResult":
        from .types import AqiResult

        return AqiResult
    if name == "predict_aqi":
        from .physics_pipeline import predict_aqi

        return predict_aqi
    raise AttributeError(name)
