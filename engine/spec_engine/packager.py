"""Stub packager for the JSON 3.0 newspec format.

This stays isolated from production packagers until the new parser is stable.
"""

from __future__ import annotations

from typing import Dict, List

from .models import PackagedQuiz, QuizPayload, RationalesEntry
import logging

logger = logging.getLogger(__name__)


def _index_items_by_id(items: List[Dict]) -> Dict[str, Dict]:
    indexed = {}
    for item in items:
        ident = item.get("id")
        if isinstance(ident, str):
            indexed[ident] = item
    return indexed


def _filter_rationales(rationales: List[RationalesEntry], items_by_id: Dict[str, Dict]) -> List[RationalesEntry]:
    """Keep rationales only for items that exist and are scorable."""
    filtered: List[RationalesEntry] = []
    for entry in rationales:
        item = items_by_id.get(entry.item_id)
        if not item:
            continue
        if item.get("type") in ("STIMULUS", "STIMULUS_END"):
            continue
        filtered.append(entry)
    return filtered


def _collect_experimental(items: List[Dict]) -> List[Dict]:
    experimental: List[Dict] = []
    for item in items:
        if item.get("type") == "NUMERICAL":
            evaluation = item.get("evaluation", {})
            if isinstance(evaluation, dict) and evaluation.get("mode", "exact") != "exact":
                experimental.append(item)
        flags = item.get("experimental_flags")
        if isinstance(flags, list) and flags:
            experimental.append(item)
    return experimental


class DefaultExporter:
    """Baseline exporter that simply returns the packaged quiz."""

    def export(self, packaged: PackagedQuiz, context: str = "default"):
        return packaged


class ExporterFactory:
    @staticmethod
    def get_exporter(context: str):
        if context == "default":
            return DefaultExporter()
        # Future: WorldForgeExporter, AnalyticsExporter, etc.
        return DefaultExporter()


def package_quiz(payload: QuizPayload, context: str = "default"):
    """Convert a QuizPayload into a packaged representation and route through an exporter."""
    items_by_id = _index_items_by_id(payload.items)
    rationales = _filter_rationales(payload.rationales, items_by_id)
    experimental = _collect_experimental(payload.items)
    if experimental:
        logger.info("Experimental items detected in packaged quiz (count=%d)", len(experimental))
    packaged = PackagedQuiz(
        version=payload.version,
        title=payload.title,
        metadata=payload.metadata,
        items=payload.items,
        rationales=rationales,
        experimental=experimental,
        instructions=payload.instructions,
        total_points=payload.total_points,
    )
    exporter = ExporterFactory.get_exporter(context)
    return exporter.export(packaged, context=context)
