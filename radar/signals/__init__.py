"""Scoring of the five individual signals. All functions are pure."""
from radar.signals.catalyst import score_catalyst
from radar.signals.float_size import score_float
from radar.signals.price_action import score_price
from radar.signals.reverse_split import score_reverse_split
from radar.signals.volume import score_volume

__all__ = [
    "score_catalyst",
    "score_float",
    "score_price",
    "score_reverse_split",
    "score_volume",
]
