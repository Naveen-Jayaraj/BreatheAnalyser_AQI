"""Manual command to run Data.gov sensor sync."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from aqi_pipeline.django_app.services import run_sensor_sync


class Command(BaseCommand):
    help = "Fetch and upsert latest AQI sensors from Data.gov"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even if AIR_QUALITY_AUTOSYNC_ENABLED is false",
        )

    def handle(self, *args, **options):
        result = run_sensor_sync(force=bool(options.get("force")))
        payload = result.to_dict()

        if payload["status"] == "ok":
            self.stdout.write(self.style.SUCCESS(str(payload)))
        else:
            self.stdout.write(self.style.WARNING(str(payload)))
