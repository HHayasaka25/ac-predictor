import unittest
from datetime import datetime, timedelta, timezone

from ac_predictor_crawler.domain.contestinfo import ContestInfo
from ac_predictor_crawler.domain.raterange import RateRange


class ContestInfoTest(unittest.TestCase):
  def test_algorithm_performance_cap_comes_from_rated_upper_bound(self):
    contest = ContestInfo(
      start_time=datetime.now(timezone.utc),
      contest_type="algorithm",
      contest_name="abc",
      contest_screen_name="abc",
      duration=timedelta(hours=1),
      ratedrange=RateRange(0, 1999),
    )
    self.assertEqual(contest.performance_cap(), 2400)

  def test_recently_ended_contest_is_in_finalization_grace(self):
    contest = ContestInfo(
      start_time=datetime.now(timezone.utc) - timedelta(hours=2),
      contest_type="algorithm",
      contest_name="delayed",
      contest_screen_name="delayed",
      duration=timedelta(hours=1),
      ratedrange=RateRange(0, 1999),
    )
    self.assertTrue(contest.has_ended_within(timedelta(hours=24)))
    self.assertFalse(contest.is_running())


if __name__ == "__main__":
  unittest.main()
