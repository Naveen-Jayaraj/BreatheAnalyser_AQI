"""DRF serializers for AQI lookup endpoints."""

from __future__ import annotations

from rest_framework import serializers

from aqi_pipeline.constants import DEFAULT_MAX_BATCH_POINTS


class LookupRequestSerializer(serializers.Serializer):
    latitude = serializers.FloatField(min_value=-90.0, max_value=90.0)
    longitude = serializers.FloatField(min_value=-180.0, max_value=180.0)


class BatchPointSerializer(LookupRequestSerializer):
    id = serializers.CharField(required=False, allow_blank=False, max_length=128)


class BatchLookupRequestSerializer(serializers.Serializer):
    points = BatchPointSerializer(many=True)

    def validate_points(self, value):
        if not value:
            raise serializers.ValidationError("At least one point is required.")
        if len(value) > DEFAULT_MAX_BATCH_POINTS:
            raise serializers.ValidationError(
                f"Batch size cannot exceed {DEFAULT_MAX_BATCH_POINTS} points."
            )
        return value
