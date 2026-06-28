from dataclasses import dataclass
from typing import Optional


def aperf_weight(rated_count: int) -> float:
  if rated_count < 0:
    raise ValueError(rated_count)
  return (1 - 0.9 ** rated_count) / 0.1


@dataclass(frozen=True)
class APerfState:
  aperf: float
  rated_count: int
  last_contest: Optional[str]

  def apply(self, performance: int, contest_screen_name: str):
    if self.last_contest == contest_screen_name:
      return self
    weight = aperf_weight(self.rated_count)
    next_weight = weight * 0.9 + 1
    next_aperf = (self.aperf * weight * 0.9 + performance) / next_weight
    return APerfState(next_aperf, self.rated_count + 1, contest_screen_name)

  @staticmethod
  def first(performance: int, contest_screen_name: str):
    return APerfState(float(performance), 1, contest_screen_name)

  def to_dict(self):
    return {
      "aperf": self.aperf,
      "ratedCount": self.rated_count,
      "lastContest": self.last_contest,
    }

  @staticmethod
  def from_dict(value):
    return APerfState(
      aperf=value["aperf"],
      rated_count=value["ratedCount"],
      last_contest=value["lastContest"],
    )
