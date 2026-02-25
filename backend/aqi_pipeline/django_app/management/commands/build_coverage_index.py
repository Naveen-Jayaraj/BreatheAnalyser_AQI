"""Build compact H3 coverage artifact from large India GeoJSON."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from aqi_pipeline.coverage_preprocess import build_coverage_index


class Command(BaseCommand):
    help = "Build compact H3 coverage artifact from india_hex_grid.json"

    def add_arguments(self, parser):
        parser.add_argument("--input", required=True, help="Path to india_hex_grid.json")
        parser.add_argument(
            "--output",
            required=True,
            help="Output artifact path (recommended: coverage/india_h3_coverage.json.gz)",
        )

    def handle(self, *args, **options):
        try:
            result = build_coverage_index(
                source_path=options["input"],
                output_path=options["output"],
            )
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                (
                    "coverage index created "
                    f"output={result.output_path} city={result.city_count} "
                    f"general={result.general_count} uninhibited={result.uninhibited_count} total={result.total}"
                )
            )
        )
