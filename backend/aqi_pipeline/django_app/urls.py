"""URL mappings for AQI lookup API."""

from django.urls import path

from .views import AqiLookupBatchView, AqiLookupView

app_name = "aqi_pipeline"

urlpatterns = [
    path("api/v1/aqi/lookup", AqiLookupView.as_view(), name="aqi-lookup"),
    path("api/v1/aqi/lookup-batch", AqiLookupBatchView.as_view(), name="aqi-lookup-batch"),
]
