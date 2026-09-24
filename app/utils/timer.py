# timer.py
from datetime import datetime, timedelta
from typing import Optional


class TimerUtils:
    @staticmethod
    def format_seconds(sekundy: int) -> str:
        """
        Format seconds to H:MM:SS (using datetime.timedelta for readability).
        """
        return str(timedelta(seconds=int(sekundy or 0)))

    @staticmethod
    def seconds_to_hours(seconds: int) -> float:
        """
        Convert seconds to hours (float).
        """
        return float(seconds) / 3600.0

    @staticmethod
    def round_hours_minimum_half(hours: float) -> float:
        """
        Round up to the nearest 0.5 hour (minimum unit is 0.5h).
        Examples:
            0.01 -> 0.5
            0.25 -> 0.5
            0.5 -> 0.5
            0.51 -> 1.0
        """
        if hours <= 0:
            return 0.0
        # number of half-hours
        half_units = int((hours * 2) + (0 if (hours * 2).is_integer() else 0))
        # if exact multiple of 0.5, keep it; otherwise round up
        if (hours * 2) != half_units:
            half_units = int((hours * 2) // 1 + 1)
        return round(half_units * 0.5, 2)

    @staticmethod
    def round_seconds_to_minimum_half(seconds: int) -> float:
        """
        Convert seconds to hours and round up to nearest 0.5 hour.
        Returns hours (float).
        """
        hours = TimerUtils.seconds_to_hours(int(seconds or 0))
        rounded = TimerUtils.round_hours_minimum_half(hours)
        return rounded

    @staticmethod
    def now() -> datetime:
        return datetime.now()
