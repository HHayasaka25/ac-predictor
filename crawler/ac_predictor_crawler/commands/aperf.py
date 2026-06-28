from argparse import ArgumentParser, Namespace
from collections.abc import Callable, Iterable, Mapping
from typing import List, Optional

from tqdm import tqdm

from ac_predictor_crawler.client.history import HistoryRecord, get_history
from ac_predictor_crawler.client.results import get_results
from ac_predictor_crawler.client.standings import get_standings
from ac_predictor_crawler.commands.subcommand import SubCommand
from ac_predictor_crawler.config import get_repository
from ac_predictor_crawler.domain.aperf import APerfState
from ac_predictor_crawler.domain.contestinfo import ContestInfo
from ac_predictor_crawler.domain.result import Result
from ac_predictor_crawler.logger import logger

HISTORY_RETRY_COUNT = 3


def _initializer(parser: ArgumentParser):
  parser.add_argument("contest", action="store")
  parser.add_argument("--refresh", dest="use_aperf_cache", action="store_false")
  parser.add_argument("--use-standings-cache", dest="use_standings_cache", action="store_true")
  parser.add_argument("--use-results-cache", dest="use_results_cache", action="store_true")


def _rated_participants(standings) -> dict[str, dict]:
  participants = {}
  for data in standings["StandingsData"]:
    if not data["IsRated"]:
      continue
    user = data["UserScreenName"]
    if user in participants:
      raise ValueError(f"duplicate rated participant: {user}")
    participants[user] = data
  return participants


def _history_with_retry(
  user: str,
  contest_type: str,
  history_provider: Callable[[str, str], List[HistoryRecord]],
) -> List[HistoryRecord]:
  for attempt in range(1, HISTORY_RETRY_COUNT + 1):
    try:
      return history_provider(user, contest_type)
    except Exception:
      logger.warning(
        "history fetch failed for %s (attempt %d/%d)",
        user,
        attempt,
        HISTORY_RETRY_COUNT,
      )
      if attempt == HISTORY_RETRY_COUNT:
        raise
  raise AssertionError("unreachable")


def _performances(
  results: List[Result],
  performance_cap: Optional[int],
) -> dict[str, Optional[int]]:
  performances = {}
  for result in results:
    if not result.is_rated:
      continue
    if result.inner_performance is not None:
      performances[result.user_screen_name] = result.inner_performance
    elif performance_cap is None or result.performance < performance_cap:
      performances[result.user_screen_name] = result.performance
    else:
      performances[result.user_screen_name] = None
  return performances


def _state_from_history(
  records: List[HistoryRecord],
  this_info: ContestInfo,
) -> Optional[APerfState]:
  valid_records = [
    record for record in records
    if record.IsRated and record.EndTime < this_info.start_time
  ]
  valid_records.sort(key=lambda record: record.EndTime)
  state = None
  for record in valid_records:
    if record.InnerPerformance is None:
      raise RuntimeError(
        f"inner performance missing in history: {record.ContestScreenName}"
      )
    contest_screen_name = record.ContestScreenName.split(".", 1)[0]
    state = (
      APerfState.first(record.InnerPerformance, contest_screen_name)
      if state is None
      else state.apply(record.InnerPerformance, contest_screen_name)
    )
  return state


def calculate_aperfs_and_states(
  standings,
  cached_aperfs: Mapping[str, float],
  contests: Iterable[ContestInfo],
  this_info: ContestInfo,
  results_provider: Callable[[str], List[Result]],
  history_provider: Callable[[str, str], List[HistoryRecord]],
  stored_states: Optional[Mapping[str, APerfState]] = None,
  snapshot_provider: Optional[Callable[[str], Optional[Mapping[str, float]]]] = None,
  target_results: Optional[List[Result]] = None,
) -> tuple[dict[str, float], dict[str, APerfState]]:
  participants = _rated_participants(standings)
  contests = sorted(contests, key=lambda contest: contest.start_time)
  if all(
    contest.contest_screen_name != this_info.contest_screen_name
    for contest in contests
  ):
    contests.append(this_info)
    contests.sort(key=lambda contest: contest.start_time)
  affective_contests = [
    contest for contest in contests
    if contest.contest_type == this_info.contest_type
    and contest.is_rated()
    and contest.start_time < this_info.start_time
  ]
  contest_positions = {
    contest.contest_screen_name: position
    for position, contest in enumerate(contests)
  }
  target_position = contest_positions[this_info.contest_screen_name]
  states = dict(stored_states or {})
  usable_states = {}
  future_state_users = set()
  standings_fixed = bool(standings.get("Fixed", False))
  target_results_by_user = {
    result.user_screen_name: result
    for result in target_results or []
    if result.is_rated
  }

  def expected_prior_count(user):
    target_result = target_results_by_user.get(user)
    if (
      standings_fixed
      and target_result is not None
      and target_result.competitions is not None
    ):
      return max(0, target_result.competitions - 1)
    if standings_fixed:
      return None
    return participants[user]["Competitions"]

  for user in participants:
    if user not in states:
      continue
    state = states[user]
    if state.last_contest is None:
      continue
    last_position = contest_positions.get(state.last_contest)
    if last_position is None:
      raise RuntimeError(f"unknown last contest in aperf state: {state.last_contest}")
    if last_position >= target_position:
      future_state_users.add(user)
      continue
    expected_count = expected_prior_count(user)
    if expected_count is not None and state.rated_count != expected_count:
      continue
    usable_states[user] = state

  unresolved = set(participants) - set(usable_states)
  events: dict[str, list[tuple[ContestInfo, Optional[int]]]] = {
    user: [] for user in unresolved
  }
  logger.info("gathering histories from results...")
  for contest in tqdm(affective_contests if unresolved else []):
    for user, performance in _performances(
      results_provider(contest.contest_screen_name),
      contest.performance_cap(),
    ).items():
      if user in unresolved:
        events[user].append((contest, performance))

  snapshot_candidates = set(cached_aperfs) & unresolved
  if snapshot_provider is not None:
    for contest in reversed(affective_contests):
      snapshot = snapshot_provider(contest.contest_screen_name)
      if snapshot is not None:
        snapshot_candidates.update(set(snapshot) & unresolved)
  if snapshot_candidates:
    logger.info(
      "found %d rounded snapshot candidates; exact state will use results/history",
      len(snapshot_candidates),
    )

  history_required = set()
  confirmed_newcomers = set()
  for user in unresolved:
    user_events = events[user]
    user_expected_count = expected_prior_count(user)
    state = None
    events_to_apply = user_events
    if user_expected_count is None or len(events_to_apply) != user_expected_count:
      history_required.add(user)
      continue

    if any(performance is None for _, performance in events_to_apply):
      history_required.add(user)
      continue
    for contest, performance in events_to_apply:
      state = (
        APerfState.first(performance, contest.contest_screen_name)
        if state is None
        else state.apply(performance, contest.contest_screen_name)
      )
    if state is not None:
      usable_states[user] = state
      if user not in future_state_users:
        states[user] = state
    elif not standings_fixed and participants[user]["Competitions"] == 0:
      confirmed_newcomers.add(user)
    elif (
      standings_fixed
      and user in target_results_by_user
      and target_results_by_user[user].competitions == 1
    ):
      # The finalized result count includes the target contest.
      confirmed_newcomers.add(user)
    else:
      history_required.add(user)

  logger.info("gathering %d histories from AtCoder...", len(history_required))
  for user in tqdm(sorted(history_required)):
    if participants[user]["UserIsDeleted"]:
      continue
    records = _history_with_retry(user, this_info.contest_type, history_provider)
    state = _state_from_history(records, this_info)
    if state is None:
      if expected_prior_count(user) == 0:
        confirmed_newcomers.add(user)
    else:
      expected_count = expected_prior_count(user)
      if expected_count is None or state.rated_count == expected_count:
        usable_states[user] = state
        if user not in future_state_users:
          states[user] = state

  aperfs = {
    user: state.aperf
    for user, state in usable_states.items()
  }
  for user in participants:
    if user in confirmed_newcomers:
      aperfs[user] = this_info.default_aperf()

  missing = sorted(set(participants) - set(aperfs))
  if missing:
    preview = ", ".join(missing[:10])
    raise RuntimeError(
      f"incomplete aperfs: rated={len(participants)}, output={len(aperfs)}, "
      f"missing={len(missing)} ({preview})"
    )
  if set(aperfs) != set(participants):
    raise AssertionError("aperf output does not match rated participants")
  return aperfs, states


def calculate_aperfs(
  standings,
  cached_aperfs: Mapping[str, float],
  contests: Iterable[ContestInfo],
  this_info: ContestInfo,
  results_provider: Callable[[str], List[Result]],
  history_provider: Callable[[str, str], List[HistoryRecord]],
) -> dict[str, float]:
  aperfs, _ = calculate_aperfs_and_states(
    standings,
    cached_aperfs,
    contests,
    this_info,
    results_provider,
    history_provider,
  )
  return aperfs


def _target_fully_applied(
  participants: Mapping[str, dict],
  target_results: List[Result],
  stored_states: Mapping[str, APerfState],
  contests: List[ContestInfo],
  target: ContestInfo,
) -> bool:
  positions = {
    contest.contest_screen_name: position
    for position, contest in enumerate(sorted(contests, key=lambda item: item.start_time))
  }
  target_position = positions[target.contest_screen_name]
  rated_result_users = {
    result.user_screen_name for result in target_results if result.is_rated
  }
  if rated_result_users != set(participants):
    return False
  for user in rated_result_users:
    state = stored_states.get(user)
    if state is None or state.last_contest is None:
      return False
    position = positions.get(state.last_contest)
    if position is None or position < target_position:
      return False
  return True


def _handler(res: Namespace):
  repo = get_repository()
  contest_screen_name = res.contest
  standings = (
    repo.get_standings(contest_screen_name)
    if res.use_standings_cache
    else get_standings(contest_screen_name)
  )
  if res.use_aperf_cache:
    try:
      cached_aperfs = repo.get_aperfs(contest_screen_name)
    except FileNotFoundError:
      logger.warning("aperfs cache not found")
      cached_aperfs = {}
  else:
    cached_aperfs = {}

  contests = repo.get_contests()
  this_info = next(
    contest for contest in contests
    if contest.contest_screen_name == contest_screen_name
  )
  try:
    stored_states = repo.get_aperf_state(this_info.contest_type)
  except FileNotFoundError:
    stored_states = {}
  participants = _rated_participants(standings)
  target_results = (
    repo.get_results(contest_screen_name)
    if repo.has_results(contest_screen_name)
    else []
  )
  cached_complete = set(participants) <= set(cached_aperfs)
  target_already_applied = _target_fully_applied(
    participants,
    target_results,
    stored_states,
    contests,
    this_info,
  )
  if cached_complete and target_already_applied:
    repo.store_aperfs(
      contest_screen_name,
      {user: cached_aperfs[user] for user in participants},
    )
    return

  def snapshot_provider(contest):
    if not repo.has_aperfs(contest):
      return None
    return repo.get_aperfs(contest)

  aperfs, states = calculate_aperfs_and_states(
    standings,
    cached_aperfs,
    contests,
    this_info,
    repo.get_results if res.use_results_cache else get_results,
    get_history,
    stored_states,
    snapshot_provider,
    (
      target_results or None
    ),
  )
  logger.info("storing complete aperfs: rated=%d, output=%d", len(aperfs), len(aperfs))
  repo.store_aperfs(contest_screen_name, aperfs)
  repo.store_aperf_state(this_info.contest_type, states)


aperfs_command = SubCommand(
  "aperfs",
  _initializer,
  _handler,
  description="get aperfs",
)
