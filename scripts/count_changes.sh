#!/usr/bin/env bash
# Count yaaf's changes per week by the chairman's rule, and print the version chain.
#
# THE RULE (fixed by the chairman; do not interpret, just apply)
#   - A week is seven days, Thursday to Wednesday, counted from Thursday 1 October 2026.
#   - One pull request merged into the default branch during that week = one change.
#   - One direct commit to the default branch that did not come through a pull request = one change.
#   - These count as zero: merge commits themselves, reverts of something merged in the
#     same week, and this project's own changelog / version-bump commits.
#   - Six or more changes: minor +1, patch = 0.  Five or fewer: patch +1.
#   - No changes: no release, the version does not move.
#
# HOW IT COUNTS, FROM THE REPOSITORY ALONE
#   Everything comes from `git log --first-parent` on the default branch, so anybody can
#   re-run this against a plain clone with no token, no network and no API quota, and get
#   the same number. The first-parent line is the mainline: one entry per thing that
#   landed on it. Each entry is classified from its own commit:
#     "Merge pull request #N ..."  -> PR N (the merge commit counts once, for its PR)
#     "Merge #N: ..."              -> PR N (same thing, the repository's own merge title)
#     "... (#N)"                   -> PR N (squash merge: the PR's single commit on main)
#     anything else                -> a direct commit; its SHA is the change
#   Merge commits are never counted as a change of their own, so no pull request can be
#   counted twice by appearing both as a merge commit and as a branch commit.
#
# TIME ZONE
#   Weeks are cut in UTC, because the public timestamps of a merge (the GitHub Release
#   date, the pull request's merged_at) are UTC. Commit dates are read with TZ=UTC so the
#   answer does not depend on the machine the script runs on.
#
# WEEKS COVERED
#   From the first week (1-7 October 2026) to the last COMPLETE week as of today, so a
#   finished week's numbers are fixed forever. Pass an end date (YYYY-MM-DD, a Wednesday)
#   as $1 to stop at a given week instead.
#
# OUTPUT
#   Plain text, deterministic: a base line for 0.1.0, then one block per week.
#   The counted pull request numbers / commit SHAs are listed under each week so the
#   arithmetic can be checked by hand.

set -euo pipefail

cd "$(dirname "$0")/.."

BASE_VERSION="0.1.0"
FIRST_WEEK_START="2026-10-01"        # Thursday; 0.1.0 is the last commit before this date
DEFAULT_BRANCH="${DEFAULT_BRANCH:-main}"
BASE_ANCESTOR_REF="${BASE_ANCESTOR_REF:-$DEFAULT_BRANCH}"

# --- how far to go -----------------------------------------------------------
today_utc="$(date -u +%Y-%m-%d)"
if [ "${1:-}" != "" ]; then
  last_week_end="$1"
else
  # days since the first week started, then the last week that has fully elapsed
  d0="$(date -u -d "$FIRST_WEEK_START" +%s)"
  d1="$(date -u -d "$today_utc" +%s)"
  days=$(( (d1 - d0) / 86400 ))
  if [ "$days" -lt 7 ]; then
    echo "count_changes: no complete week has passed since $FIRST_WEEK_START" >&2
    exit 1
  fi
  last_week_end="$(date -u -d "$FIRST_WEEK_START + $(( (days - 7) / 7 * 7 + 6 )) days" +%Y-%m-%d)"
fi

# 0.1.0 sits on the last commit before the first week started.
base_sha="$(TZ=UTC git log -1 --first-parent --format='%H' \
              --date=format-local:'%Y-%m-%d' \
              --until="${FIRST_WEEK_START}T00:00:00" "$BASE_ANCESTOR_REF")"
if [ -z "$base_sha" ]; then
  echo "count_changes: no commit on $BASE_ANCESTOR_REF before $FIRST_WEEK_START" >&2
  exit 1
fi

# One pass over the mainline. Dates are rendered in UTC so the week cut is fixed.
log="$(TZ=UTC git log --first-parent --format='%H%x09%cd%x09%s' \
        --date=format-local:'%Y-%m-%dT%H:%M:%SZ' "$BASE_ANCESTOR_REF" |
       awk -F'\t' -v first="$FIRST_WEEK_START" 'substr($2,1,10) >= first')"

printf 'Week boundaries: Thursday to Wednesday, cut in UTC, from %s.\n' "$FIRST_WEEK_START"
printf 'Base: v%s on %s (the last commit on %s before %s).\n' \
  "$BASE_VERSION" "$(git rev-parse --short "$base_sha")" "$DEFAULT_BRANCH" "$FIRST_WEEK_START"
printf 'Weeks covered: %s to %s.\n\n' "$FIRST_WEEK_START" "$last_week_end"

# --- classify, then walk the weeks -------------------------------------------
# Emits, per counted change: <utc-date> <counted-as> <label>
#   counted-as is "pr" for a merged pull request, "commit" for a direct push.
change_rows="$(printf '%s\n' "$log" | awk -F'\t' '
  NF < 3 { next }
  {
    sha = $1; day = substr($2, 1, 10); subject = $3;

    # the release commits of this project count as zero (changelog and version bump)
    if (subject ~ /^Release v?[0-9]+\.[0-9]+\.[0-9]+/) next
    if (subject ~ /^[Cc]hangelog/ && subject ~ /[0-9]+\.[0-9]+\.[0-9]+/) next

    # a revert is recorded, and dropped later only if it reverts a change of the same week
    if (subject ~ /^Revert "/) {
      reverted = subject; sub(/^Revert "/, "", reverted); sub(/"$/, "", reverted)
      printf "%s\t%s\t%s\t%s\n", day, "revert", sha, reverted
      next
    }

    pr = ""
    if (match(subject, /^Merge pull request #[0-9]+/)) {
      pr = subject; sub(/^Merge pull request #/, "", pr); sub(/[^0-9].*$/, "", pr)
    } else if (match(subject, /^Merge #[0-9]+/)) {
      pr = subject; sub(/^Merge #/, "", pr); sub(/[^0-9].*$/, "", pr)
    } else if (match(subject, /\(#[0-9]+\)$/)) {
      pr = subject; sub(/^.*\(#/, "", pr); sub(/\)$/, "", pr)
    }

    if (pr != "") printf "%s\t%s\t%s\t%s\n", day, "pr", pr, subject
    else          printf "%s\t%s\t%s\t%s\n", day, "commit", sha, subject
  }
')"

version="$BASE_VERSION"
week=0
while :; do
  start="$(date -u -d "$FIRST_WEEK_START + $((week * 7)) days" +%Y-%m-%d)"
  end="$(date -u -d "$FIRST_WEEK_START + $((week * 7 + 6)) days" +%Y-%m-%d)"
  if [ "$start" \> "$last_week_end" ]; then break; fi

  # this week's counted changes: pull requests and direct commits, one line each.
  # Reverts are dropped when they undo something merged in the same week.
  # Reverts are dropped when they undo something merged in the same week, and the
  # remaining entries are printed in history order.
  counted="$(printf '%s\n' "$change_rows" | awk -F'\t' -v s="$start" -v e="$end" '
    NF < 4 { next }
    $1 < s || $1 > e { next }
    { idx[++n] = NR; line[NR] = $0 }
    END {
      m = 0
      for (i = 1; i <= n; i++) {
        split(line[idx[i]], f, "\t")
        if (f[2] == "revert") continue
        m++; key[m] = f[2] ":" f[3]; label[m] = (f[2] == "pr" ? "PR #" f[3] : "commit " substr(f[3],1,9)); subj[m] = f[4]; keep[m] = 1
      }
      for (i = 1; i <= n; i++) {
        split(line[idx[i]], f, "\t")
        if (f[2] != "revert") continue
        for (j = 1; j <= m; j++) if (subj[j] == f[4]) keep[j] = 0
      }
      for (j = 1; j <= m; j++) if (keep[j]) print j "\t" label[j] "\t" subj[j]
    }
  ')"

  count="$(printf '%s\n' "$counted" | grep -c . || true)"

  if [ "$count" -eq 0 ]; then
    printf '%s %s - %s: 0 changes, no release (stays v%s)\n' \
      "$(printf '%02d' $((week+1)))" "$start" "$end" "$version"
  else
    if [ "$count" -ge 6 ]; then
      major="${version%%.*}"; rest="${version#*.}"; minor="${rest%%.*}"
      version="${major}.$((minor + 1)).0"
    else
      major="${version%%.*}"; rest="${version#*.}"; minor="${rest%%.*}"; patch="${rest#*.}"
      version="${major}.${minor}.$((patch + 1))"
    fi
    printf '%s %s - %s: %s change%s -> v%s\n' \
      "$(printf '%02d' $((week+1)))" "$start" "$end" "$count" \
      "$([ "$count" -eq 1 ] && echo '' || echo 's')" "$version"
    printf '%s\n' "$counted" | awk -F'\t' '{ printf "     - %s  %s\n", $2, $3 }'
  fi
  week=$((week + 1))
done

printf '\nFinal version after %s: v%s\n' "$last_week_end" "$version"
