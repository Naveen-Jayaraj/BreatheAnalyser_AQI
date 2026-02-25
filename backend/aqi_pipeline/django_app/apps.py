from django.apps import AppConfig


class AQIPipelineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "aqi_pipeline.django_app"
    verbose_name = "AQI Pipeline"
