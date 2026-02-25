"""DRF endpoints for coordinate-to-AQI lookup."""

from __future__ import annotations

from http import HTTPStatus

from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from aqi_pipeline.exceptions import InvalidCoordinateError, NoSensorDataError, OutsideIndiaCoverageError

from .serializers import BatchLookupRequestSerializer, LookupRequestSerializer
from .services import get_aqi_engine


def _map_error(exc: Exception) -> tuple[int, str, str]:
    if isinstance(exc, OutsideIndiaCoverageError):
        return int(HTTPStatus.UNPROCESSABLE_ENTITY), "outside_india", str(exc)
    if isinstance(exc, NoSensorDataError):
        return int(HTTPStatus.SERVICE_UNAVAILABLE), "no_sensor_data", str(exc)
    if isinstance(exc, InvalidCoordinateError):
        return int(HTTPStatus.BAD_REQUEST), "invalid_coordinate", str(exc)
    return int(HTTPStatus.INTERNAL_SERVER_ERROR), "internal_error", "Unexpected AQI processing error"


class AqiLookupView(APIView):
    """Single-point AQI lookup endpoint."""

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = LookupRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        engine = get_aqi_engine()
        try:
            result = engine.estimate_aqi(
                serializer.validated_data["latitude"],
                serializer.validated_data["longitude"],
            )
        except Exception as exc:  # noqa: BLE001
            status, code, message = _map_error(exc)
            return Response({"error": {"code": code, "message": message}}, status=status)

        return Response(result.to_dict(), status=int(HTTPStatus.OK))


class AqiLookupBatchView(APIView):
    """Batch AQI lookup endpoint with per-point status."""

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = BatchLookupRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        engine = get_aqi_engine()
        points = serializer.validated_data["points"]

        results: list[dict[str, object]] = []
        for idx, point in enumerate(points, start=1):
            point_id = point.get("id") or f"p{idx}"
            try:
                result = engine.estimate_aqi(point["latitude"], point["longitude"])
                results.append({"id": point_id, "status": "ok", "result": result.to_dict()})
            except Exception as exc:  # noqa: BLE001
                status, code, message = _map_error(exc)
                results.append(
                    {
                        "id": point_id,
                        "status": "error",
                        "error": {
                            "status": status,
                            "code": code,
                            "message": message,
                        },
                    }
                )

        return Response({"count": len(results), "results": results}, status=int(HTTPStatus.OK))
