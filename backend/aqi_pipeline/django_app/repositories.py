"""Django ORM-backed sensor repository implementation."""

from __future__ import annotations

from datetime import datetime

from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D

from aqi_pipeline.sensors import SensorReading, SensorRepository

from .models import AQIStationSnapshot


class DjangoSensorRepository(SensorRepository):
    """GeoDjango-based nearest-station lookup for AQI engine."""

    def nearest_within(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_km: float,
        fresh_after: datetime,
        limit: int,
    ) -> list[SensorReading]:
        point = Point(longitude, latitude, srid=4326)

        query = (
            AQIStationSnapshot.objects.select_related("station")
            .filter(observed_at__gte=fresh_after)
            .annotate(distance_m=Distance("station__geom", point))
            .filter(distance_m__lte=D(km=radius_km))
            .order_by("distance_m")
        )

        return [self._to_sensor(snapshot) for snapshot in query[:limit]]

    def nearest_any(
        self,
        *,
        latitude: float,
        longitude: float,
        fresh_after: datetime,
        limit: int,
    ) -> list[SensorReading]:
        point = Point(longitude, latitude, srid=4326)

        query = (
            AQIStationSnapshot.objects.select_related("station")
            .filter(observed_at__gte=fresh_after)
            .annotate(distance_m=Distance("station__geom", point))
            .order_by("distance_m")
        )

        return [self._to_sensor(snapshot) for snapshot in query[:limit]]

    @staticmethod
    def _to_sensor(snapshot: AQIStationSnapshot) -> SensorReading:
        station = snapshot.station
        distance = snapshot.distance_m
        distance_km = float(getattr(distance, "km", float(distance) / 1000.0))
        return SensorReading(
            station_id=station.station_id,
            station_name=station.name,
            city=station.city,
            state=station.state,
            latitude=station.latitude,
            longitude=station.longitude,
            aqi=snapshot.aqi,
            observed_at=snapshot.observed_at,
            distance_km=distance_km,
        )
