from __future__ import annotations

from pathlib import Path

from backend.app.catalog.models import MetricSpec
from backend.app.catalog.store import CatalogStore


def get_metric(metric_id: str, *, catalog_db: str | Path | None = None) -> MetricSpec:
    return CatalogStore(catalog_db).get_metric(metric_id)
