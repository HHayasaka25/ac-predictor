import unittest
from datetime import datetime, timedelta, timezone

from ac_predictor_crawler.client.history import HistoryRecord
from ac_predictor_crawler.commands.aperf import (
  _performances,
  _target_fully_applied,
  calculate_aperfs,
  calculate_aperfs_and_states,
)
from ac_predictor_crawler.commands.aperf_state import apply_results_to_states
from ac_predictor_crawler.domain.aperf import APerfState
from ac_predictor_crawler.domain.contestinfo import ContestInfo
from ac_predictor_crawler.domain.raterange import RateRange
from ac_predictor_crawler.domain.rating import calc_aperf
from ac_predictor_crawler.domain.result import Result


def contest(name, start_day, rated=True):
  return ContestInfo(
    start_time=datetime(2026, 1, start_day, tzinfo=timezone.utc),
    contest_type="algorithm",
    contest_name=name,
    contest_screen_name=name,
    duration=timedelta(hours=2),
    ratedrange=RateRange(0, 1999) if rated else RateRange(1, 0),
  )


def participant(name, competitions, rated=True, deleted=False):
  return {
    "UserScreenName": name,
    "IsRated": rated,
    "Competitions": competitions,
    "UserIsDeleted": deleted,
  }


def record(performance, day, rated=True):
  return HistoryRecord(
    IsRated=rated,
    Place=1,
    OldRating=0,
    NewRating=0,
    Performance=performance,
    InnerPerformance=performance,
    ContestScreenName=f"past-{day}",
    ContestName="past",
    ContestNameEn="past",
    EndTime=datetime(2026, 1, day, tzinfo=timezone.utc),
  )


class CalcAPerfTest(unittest.TestCase):
  def test_weighted_average_uses_newest_performance_most(self):
    self.assertAlmostEqual(calc_aperf([1000, 2000]), (1000 * 0.9 + 2000) / 1.9)

  def test_incremental_state_matches_full_calculation(self):
    state = APerfState.first(1000, "first")
    state = state.apply(2000, "second")
    self.assertAlmostEqual(state.aperf, calc_aperf([1000, 2000]))
    self.assertEqual(state.rated_count, 2)


class CalculateAPerfsTest(unittest.TestCase):
  def setUp(self):
    self.target = contest("target", 20)

  def calculate(self, participants, histories, cached=None):
    calls = []

    def get_history(user, contest_type):
      calls.append((user, contest_type))
      value = histories[user]
      if isinstance(value, Exception):
        raise value
      return value

    result = calculate_aperfs(
      {"StandingsData": participants},
      cached or {},
      [],
      self.target,
      lambda _: [],
      get_history,
    )
    return result, calls

  def test_newcomer_gets_explicit_default(self):
    result, calls = self.calculate([participant("new", 0)], {})
    self.assertEqual(result, {"new": 1200})
    self.assertEqual(calls, [])

  def test_empty_prior_history_identifies_newcomer_after_standings_are_fixed(self):
    calls = []
    result, _ = calculate_aperfs_and_states(
      {
        "Fixed": True,
        "StandingsData": [participant("new", 1)],
      },
      {},
      [],
      self.target,
      lambda _: [],
      lambda user, contest_type: calls.append((user, contest_type)) or [],
      target_results=[
        Result(True, 1, 0, 100, 500, "new", 500, competitions=1),
      ],
    )
    self.assertEqual(result, {"new": 1200})
    self.assertEqual(calls, [])

  def test_empty_history_does_not_reclassify_known_experienced_user(self):
    with self.assertRaisesRegex(RuntimeError, "incomplete aperfs"):
      calculate_aperfs_and_states(
        {
          "Fixed": True,
          "StandingsData": [participant("experienced", 2)],
        },
        {},
        [],
        self.target,
        lambda _: [],
        lambda *_: [],
        target_results=[
          Result(
            True,
            1,
            100,
            200,
            500,
            "experienced",
            500,
            competitions=2,
          ),
        ],
      )

  def test_history_count_must_match_finalized_result_count(self):
    with self.assertRaisesRegex(RuntimeError, "incomplete aperfs"):
      calculate_aperfs_and_states(
        {
          "Fixed": True,
          "StandingsData": [participant("experienced", 3)],
        },
        {},
        [],
        self.target,
        lambda _: [],
        lambda *_: [record(500, 1)],
        target_results=[
          Result(
            True,
            1,
            100,
            200,
            500,
            "experienced",
            500,
            competitions=3,
          ),
        ],
      )

  def test_fixed_deleted_user_is_not_classified_by_competitions(self):
    standings = {
      "Fixed": True,
      "StandingsData": [{
        **participant("experienced", 0, deleted=True),
        "OldRating": 330,
      }],
    }
    with self.assertRaisesRegex(RuntimeError, "incomplete aperfs"):
      calculate_aperfs(
        standings,
        {},
        [],
        self.target,
        lambda _: [],
        lambda *_: self.fail("deleted history cannot be fetched"),
      )

  def test_fixed_deleted_newcomer_requires_zero_old_rating_and_no_results(self):
    standings = {
      "Fixed": True,
      "StandingsData": [{
        **participant("new", 99, deleted=True),
        "OldRating": 0,
      }],
    }
    result, _ = calculate_aperfs_and_states(
      standings,
      {},
      [],
      self.target,
      lambda _: [],
      lambda *_: self.fail("deleted history cannot be fetched"),
      target_results=[
        Result(True, 1, 0, 0, 100, "new", 100, competitions=1),
      ],
    )
    self.assertEqual(result, {"new": 1200})

  def test_fixed_deleted_experienced_user_uses_results_count_and_history(self):
    past = contest("past", 1)
    standings = {
      "Fixed": True,
      "StandingsData": [{
        **participant("experienced", 0, deleted=True),
        "OldRating": 330,
      }],
    }
    result, states = calculate_aperfs_and_states(
      standings,
      {},
      [past],
      self.target,
      lambda _: [Result(True, 1, 0, 100, 900, "experienced", 850)],
      lambda *_: self.fail("cached results are sufficient"),
      target_results=[
        Result(True, 1, 330, 338, 407, "experienced", None, competitions=2),
      ],
    )
    self.assertEqual(result, {"experienced": 850})
    self.assertEqual(states["experienced"], APerfState(850, 1, "past"))

  def test_experienced_user_uses_all_prior_rated_inner_performances(self):
    histories = {
      "experienced": [
        record(1000, 1),
        record(9999, 2, rated=False),
        record(2000, 3),
        record(3000, 21),
      ]
    }
    result, _ = self.calculate([participant("experienced", 2)], histories)
    self.assertAlmostEqual(result["experienced"], calc_aperf([1000, 2000]))

  def test_results_cache_avoids_history_request(self):
    past = contest("past", 1)
    results = [
      Result(True, 1, 0, 0, 2000, "experienced", 1800),
      Result(False, 2, 0, 0, 9999, "experienced", 9999),
    ]

    result = calculate_aperfs(
      {"StandingsData": [participant("experienced", 1)]},
      {},
      [past],
      self.target,
      lambda _: results,
      lambda *_: self.fail("history should not be fetched"),
    )

    self.assertEqual(result, {"experienced": 1800})

  def test_temporary_history_failure_recovers(self):
    attempts = 0

    def flaky_history(user, contest_type):
      nonlocal attempts
      attempts += 1
      if attempts < 3:
        raise RuntimeError("temporary failure")
      return [record(1500, 1)]

    result = calculate_aperfs(
      {"StandingsData": [participant("experienced", 1)]},
      {},
      [],
      self.target,
      lambda _: [],
      flaky_history,
    )

    self.assertEqual(result, {"experienced": 1500})
    self.assertEqual(attempts, 3)

  def test_history_failure_is_retried_and_does_not_become_default(self):
    attempts = 0

    def failing_history(user, contest_type):
      nonlocal attempts
      attempts += 1
      raise RuntimeError("temporary failure")

    with self.assertRaisesRegex(RuntimeError, "temporary failure"):
      calculate_aperfs(
        {"StandingsData": [participant("experienced", 1)]},
        {},
        [],
        self.target,
        lambda _: [],
        failing_history,
      )
    self.assertEqual(attempts, 3)

  def test_incomplete_output_is_rejected(self):
    with self.assertRaisesRegex(
      RuntimeError,
      r"incomplete aperfs: rated=2, output=1, missing=1",
    ):
      self.calculate(
        [participant("new", 0), participant("deleted", 1, deleted=True)],
        {},
      )

  def test_exact_results_override_rounded_snapshot(self):
    past = contest("past", 1)
    result = calculate_aperfs(
      {
        "StandingsData": [
          participant("current", 1),
          participant("unrated", 0, rated=False),
        ]
      },
      {"current": 900, "stale": 1000},
      [past],
      self.target,
      lambda _: [Result(True, 1, 0, 0, 1000, "current", 1000)],
      lambda *_: self.fail("history should not be fetched"),
    )
    self.assertEqual(result, {"current": 1000})

  def test_stored_state_is_frozen_before_running_contest(self):
    result, states = calculate_aperfs_and_states(
      {"StandingsData": [participant("known", 2)]},
      {},
      [contest("past", 1), self.target],
      self.target,
      lambda _: self.fail("results should not be scanned for a known user"),
      lambda *_: self.fail("history should not be fetched for a known user"),
      {"known": APerfState(1234, 2, "past")},
    )
    self.assertEqual(result, {"known": 1234})
    self.assertEqual(states["known"].last_contest, "past")

  def test_past_snapshot_never_uses_or_overwrites_future_state(self):
    future = contest("future", 21)
    current_state = APerfState(2000, 4, "future")
    result, states = calculate_aperfs_and_states(
      {
        "Fixed": True,
        "StandingsData": [participant("known", 1)],
      },
      {"known": 900},
      [self.target, future],
      self.target,
      lambda _: [],
      lambda *_: [record(900, 1)],
      {"known": current_state},
    )
    self.assertEqual(result, {"known": 900})
    self.assertEqual(states["known"], current_state)


class ApplyResultsTest(unittest.TestCase):
  def setUp(self):
    self.first = contest("first", 1)
    self.second = contest("second", 2)
    self.result = Result(
      True, 1, 0, 100, 1500, "user", 1400, competitions=1
    )

  def test_final_result_is_applied_once(self):
    states = apply_results_to_states({}, self.first, [self.result], [self.first])
    self.assertEqual(states["user"], APerfState(1400, 1, "first"))
    rerun = apply_results_to_states(states, self.first, [self.result], [self.first])
    self.assertEqual(rerun, states)

  def test_missing_experienced_prestate_is_rejected_after_json_crash(self):
    experienced = Result(
      True, 1, 100, 200, 1500, "user", 1400, competitions=2
    )
    with self.assertRaisesRegex(RuntimeError, "missing pre-contest"):
      apply_results_to_states({}, self.first, [experienced], [self.first])

  def test_out_of_order_result_is_rejected(self):
    states = {"user": APerfState(1500, 2, "second")}
    with self.assertRaisesRegex(RuntimeError, "out-of-order"):
      apply_results_to_states(
        states,
        self.first,
        [self.result],
        [self.first, self.second],
      )

  def test_contest_order_uses_start_time_not_id_text(self):
    earlier = contest("zzz-earlier", 1)
    later = contest("aaa-later", 2)
    states = {"user": APerfState(1500, 2, "aaa-later")}
    with self.assertRaisesRegex(RuntimeError, "out-of-order"):
      apply_results_to_states(
        states,
        earlier,
        [self.result],
        [later, earlier],
      )

  def test_missing_inner_performance_is_rejected(self):
    capped = Result(True, 1, 0, 100, 2400, "user", None, competitions=1)
    with self.assertRaisesRegex(RuntimeError, "without inner performance"):
      apply_results_to_states({}, self.first, [capped], [self.first])

  def test_history_override_supplies_exact_capped_performance(self):
    capped = Result(True, 1, 0, 100, 2400, "user", None, competitions=1)
    states = apply_results_to_states(
      {},
      self.first,
      [capped],
      [self.first],
      {"user": 2512},
    )
    self.assertEqual(states["user"], APerfState(2512, 1, "first"))
    self.assertEqual(
      apply_results_to_states(states, self.first, [capped], [self.first]),
      states,
    )


class ResultSerializationTest(unittest.TestCase):
  def test_competitions_survives_result_cache_round_trip(self):
    result = Result(True, 1, 0, 100, 1500, "user", 1400, competitions=7)
    self.assertEqual(Result.from_dict(result.to_dict()), result)


class PerformanceExtractionTest(unittest.TestCase):
  def test_unrated_user_above_cap_does_not_hide_rated_capping(self):
    results = [
      Result(True, 1, 0, 0, 2400, "rated", None),
      Result(False, 2, 0, 0, 2873, "unrated", None),
    ]
    self.assertEqual(_performances(results, 2400), {"rated": None})

  def test_inner_performance_always_wins_at_cap(self):
    results = [Result(True, 1, 0, 0, 2400, "rated", 2637)]
    self.assertEqual(_performances(results, 2400), {"rated": 2637})


class FastPathTest(unittest.TestCase):
  def test_partial_state_does_not_mark_complete_json_as_fully_applied(self):
    target = contest("target", 2)
    past = contest("past", 1)
    participants = {
      "complete": participant("complete", 2),
      "missing": participant("missing", 2),
    }
    results = [
      Result(True, 1, 0, 0, 1000, "complete", 1000, competitions=2),
      Result(True, 2, 0, 0, 900, "missing", 900, competitions=2),
    ]
    states = {
      "complete": APerfState(1000, 2, "target"),
      "missing": APerfState(800, 1, "past"),
    }
    self.assertFalse(
      _target_fully_applied(
        participants,
        results,
        states,
        [past, target],
        target,
      )
    )

  def test_all_users_at_or_after_target_are_fully_applied(self):
    target = contest("target", 2)
    future = contest("future", 3)
    participants = {
      "target": participant("target", 2),
      "future": participant("future", 3),
    }
    results = [
      Result(True, 1, 0, 0, 1000, "target", 1000, competitions=2),
      Result(True, 2, 0, 0, 900, "future", 900, competitions=2),
    ]
    states = {
      "target": APerfState(1000, 2, "target"),
      "future": APerfState(900, 3, "future"),
    }
    self.assertTrue(
      _target_fully_applied(
        participants,
        results,
        states,
        [target, future],
        target,
      )
    )


if __name__ == "__main__":
  unittest.main()
