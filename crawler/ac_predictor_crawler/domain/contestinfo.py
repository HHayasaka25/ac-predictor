
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Literal

from ac_predictor_crawler.domain.raterange import RateRange

@dataclass
class ContestInfo:
  start_time: datetime
  contest_type: Literal["algorithm", "heuristic"]
  contest_name: str
  contest_screen_name: str
  duration: timedelta
  ratedrange: RateRange
  @property
  def end_time(self):
    return self.start_time + self.duration
  def is_rated(self):
    return self.ratedrange.has_value()
  def default_aperf(self):
    if self.contest_type == "heuristic":
      return 1000
    if not self.is_rated():
      raise ValueError("unrated contest")
    if not self.ratedrange.lower <= 0 <= self.ratedrange.upper:
      return 0
    if datetime(2025, 11, 1, tzinfo=timezone.utc) < self.start_time:
      return 1200
    if self.ratedrange.upper in (1199, 1999):
      return 800
    if self.ratedrange.upper == 2799:
      return 1000
    return 1200
  def performance_cap(self):
    if self.contest_type == "heuristic":
      return None
    if not self.is_rated():
      raise ValueError("unrated contest")
    if 4000 <= self.ratedrange.upper:
      return None
    return self.ratedrange.upper + 1 + 400
  def has_start_within(self, timedelta: timedelta):
    return datetime.now(timezone.utc) <= self.start_time <= datetime.now(timezone.utc) + timedelta
  def is_running(self):
    return self.start_time <= datetime.now(timezone.utc) <= self.start_time + self.duration
  def has_ended_within(self, delta: timedelta):
    now = datetime.now(timezone.utc)
    return now - delta <= self.end_time <= now
  def is_over(self):
    return self.start_time + self.duration <= datetime.now(timezone.utc)
