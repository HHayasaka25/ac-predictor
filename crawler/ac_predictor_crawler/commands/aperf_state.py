from argparse import ArgumentParser, Namespace
from collections.abc import Iterable, Mapping
from typing import Optional

from ac_predictor_crawler.client.history import get_history
from ac_predictor_crawler.client.results import get_results
from ac_predictor_crawler.commands.aperf import _history_with_retry, _performances
from ac_predictor_crawler.commands.subcommand import SubCommand
from ac_predictor_crawler.config import get_repository
from ac_predictor_crawler.domain.aperf import APerfState
from ac_predictor_crawler.domain.contestinfo import ContestInfo


def apply_results_to_states(
  states: Mapping[str, APerfState],
  contest: ContestInfo,
  results,
  contests: Iterable[ContestInfo],
  performance_overrides: Optional[Mapping[str, int]] = None,
) -> dict[str, APerfState]:
  states = dict(states)
  positions = {
    item.contest_screen_name: position
    for position, item in enumerate(sorted(contests, key=lambda item: item.start_time))
  }
  contest_position = positions[contest.contest_screen_name]
  performances = _performances(results, contest.performance_cap())
  performances.update(performance_overrides or {})
  missing_inner_performance = sorted(
    user for user, performance in performances.items()
    if performance is None
    and (
      user not in states
      or states[user].last_contest != contest.contest_screen_name
    )
  )
  if missing_inner_performance:
    raise RuntimeError(
      "cannot update aperf state without inner performance: "
      + ", ".join(missing_inner_performance[:10])
    )

  for user, performance in performances.items():
    state = states.get(user)
    if state is not None and state.last_contest == contest.contest_screen_name:
      continue
    if state is not None and state.last_contest is not None:
      last_position = positions.get(state.last_contest)
      if last_position is None:
        raise RuntimeError(f"unknown last contest in aperf state: {state.last_contest}")
      if last_position > contest_position:
        raise RuntimeError(
          f"refusing out-of-order aperf update: {user}, "
          f"{state.last_contest} -> {contest.contest_screen_name}"
        )
    if state is None:
      result = next(
        item for item in results
        if item.is_rated and item.user_screen_name == user
      )
      if result.competitions != 1:
        raise RuntimeError(
          f"missing pre-contest aperf state for experienced user: {user}"
        )
      states[user] = APerfState.first(performance, contest.contest_screen_name)
    else:
      states[user] = state.apply(performance, contest.contest_screen_name)
  return states


def _initializer(parser: ArgumentParser):
  parser.add_argument("contest", action="store")
  parser.add_argument("--use-results-cache", dest="use_results_cache", action="store_true")


def _handler(res: Namespace):
  repo = get_repository()
  contests = repo.get_contests()
  contest = next(
    item for item in contests
    if item.contest_screen_name == res.contest
  )
  try:
    states = repo.get_aperf_state(contest.contest_type)
  except FileNotFoundError:
    states = {}
  results = (
    repo.get_results(contest.contest_screen_name)
    if res.use_results_cache
    else get_results(contest.contest_screen_name)
  )
  performances = _performances(results, contest.performance_cap())
  overrides = {}
  for user, performance in performances.items():
    if performance is not None:
      continue
    if (
      user in states
      and states[user].last_contest == contest.contest_screen_name
    ):
      continue
    history = _history_with_retry(user, contest.contest_type, get_history)
    matching_records = [
      record for record in history
      if record.IsRated
      and record.ContestScreenName.split(".", 1)[0] == contest.contest_screen_name
    ]
    if len(matching_records) != 1 or matching_records[0].InnerPerformance is None:
      raise RuntimeError(
        f"exact performance unavailable for {user} in {contest.contest_screen_name}"
      )
    overrides[user] = matching_records[0].InnerPerformance
  states = apply_results_to_states(
    states,
    contest,
    results,
    contests,
    overrides,
  )
  repo.store_aperf_state(contest.contest_type, states)


aperf_state_command = SubCommand(
  "update-aperf-state",
  _initializer,
  _handler,
  description="apply finalized results to persistent aperf state",
)
