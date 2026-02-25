"""Django persistence models for station metadata and latest AQI snapshot."""

from __future__ import annotations

from django.contrib.gis.db import models as gis_models
from django.contrib.gis.geos import Point
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class AQIStation(models.Model):
    """Reference station entity with stable identity and location."""

    station_id = models.CharField(max_length=128, primary_key=True)
    name = models.CharField(max_length=255)
    city = models.CharField(max_length=255, null=True, blank=True)
    state = models.CharField(max_length=255, null=True, blank=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    geom = gis_models.PointField(geography=True, srid=4326, spatial_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "aqi_station"

    def save(self, *args, **kwargs):  # type: ignore[override]
        self.geom = Point(self.longitude, self.latitude, srid=4326)
        return super().save(*args, **kwargs)


class AQIStationSnapshot(models.Model):
    """Latest AQI reading per station."""

    station = models.OneToOneField(
        AQIStation,
        on_delete=models.CASCADE,
        related_name="snapshot",
        primary_key=True,
    )
    aqi = models.PositiveSmallIntegerField(validators=[MinValueValidator(0), MaxValueValidator(500)])
    observed_at = models.DateTimeField(db_index=True)
    fetched_at = models.DateTimeField()
    source_payload_hash = models.CharField(max_length=64)

    class Meta:
        db_table = "aqi_station_snapshot"
        indexes = [
            models.Index(fields=["observed_at"], name="aqi_snapshot_observed_idx"),
        ]
