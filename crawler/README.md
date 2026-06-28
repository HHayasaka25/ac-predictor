# crawler

## setup
```sh
# install
pip install -e .
# set repository path
export REPOSITORY_PATH=~/ghq/github.com/key-moon/ac-predictor-data
# login by your atcoder account
ac-predictor-crawler login
```
## usage

自動更新
```sh
# 成績表の差分更新
ac-predictor-crawler-runner crawl-results
# 成績表の全体更新
ac-predictor-crawler-runner refresh-results
# aperfs の更新
ac-predictor-crawler-runner update-aperfs
```

手動で更新
```sh
# コンテスト一覧の差分更新
ac-predictor-crawler contests
# コンテスト一覧の更新
ac-predictor-crawler contests --refresh
# 順位表を更新
ac-predictor-crawler standings [contestScreenName]
# 成績表を更新
ac-predictor-crawler results [contestScreenName]
# aperfs を計算して更新（ローカルの成績表を使用）
ac-predictor-crawler aperfs --use-results-cache [contestScreenName]
# 全員の rating を計算して更新（ローカルの成績表を使用）
ac-predictor-crawler ratings --use-results-cache [contestType]
```

## APerf state

The crawler keeps the latest exact per-user APerf state in
`aperf-state/{algorithm,heuristic}.json`. Contest APerf files are generated from
state that predates the target contest; finalized results are applied only
after that pre-contest snapshot has been written.

Each entry is keyed by `UserScreenName` and contains `aperf`, `ratedCount`, and
`lastContest`. `ratedCount` is the number of rated performances already
included in `aperf`, including `lastContest`. Contest ordering is taken from
`contest-details.json` start times, never from lexicographic contest IDs.
The two-decimal contest snapshots may be used to locate reusable data, but are
never persisted as exact state; exact state is rebuilt from results or history.

The scheduled workflow also finalizes contests for 24 hours after they end.
This covers delayed GitHub Actions runs and produces a complete final snapshot,
but it cannot recover predictions for a period during the contest in which no
workflow ran.

Atomic file replacement prevents readers from observing truncated JSON. It is
not a lost-update lock: concurrent data-repository writers are reconciled by
Git rebase and push retry, and unresolved conflicts fail the run for a later
retry.
