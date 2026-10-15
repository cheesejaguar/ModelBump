#!/usr/bin/env bash
# Add ModelBump to the git repository in the current directory.
#
#   curl -fsSL https://cheesejaguar.github.io/ModelBump/install.sh | bash
#
# Writes .github/workflows/modelbump.yml and .github/modelbump.yml, stages
# them, and (with your permission, using the GitHub CLI) turns on the
# repository setting that lets GitHub Actions open pull requests.
#
# Options:
#   --strategy same-family|cost-optimized   default: same-family
#   --schedule weekly|daily                 default: weekly
#   --updates snapshot,minor,major          allowed update types (default: all)
#   --grouping per-model|single             pull requests (default: per-model)
#   --prerelease     allow preview models
#   --yes            answer yes to every question
#   --no-settings    don't touch repository settings
#   --force          overwrite existing files
set -euo pipefail

strategy="same-family"
schedule="weekly"
updates="snapshot,minor,major"
grouping="per-model"
prerelease=false
assume_yes=false
touch_settings=true
force=false

while [ $# -gt 0 ]; do
  case "$1" in
    --strategy) strategy="${2:-}"; shift 2 ;;
    --schedule) schedule="${2:-}"; shift 2 ;;
    --yes|-y) assume_yes=true; shift ;;
    --no-settings) touch_settings=false; shift ;;
    --force) force=true; shift ;;
    --updates) updates="${2:-}"; shift 2 ;;
    --grouping) grouping="${2:-}"; shift 2 ;;
    --prerelease) prerelease=true; shift ;;
    -h|--help) sed -n '2,20p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "modelbump: unknown option $1" >&2; exit 2 ;;
  esac
done

case "$strategy" in same-family|cost-optimized) ;; *) echo "modelbump: --strategy must be same-family or cost-optimized" >&2; exit 2 ;; esac
case "$grouping" in per-model|single) ;; *) echo "modelbump: --grouping must be per-model or single" >&2; exit 2 ;; esac
for u in ${updates//,/ }; do
  case "$u" in snapshot|minor|major) ;; *) echo "modelbump: --updates takes snapshot, minor and/or major" >&2; exit 2 ;; esac
done
[ -n "${updates//,/}" ] || { echo "modelbump: --updates needs at least one type" >&2; exit 2; }
case "$schedule" in
  weekly) cron="0 6 * * 1" ;;
  daily) cron="0 6 * * *" ;;
  *) echo "modelbump: --schedule must be weekly or daily" >&2; exit 2 ;;
esac

bold=$(printf '\033[1m'); dim=$(printf '\033[2m'); reset=$(printf '\033[0m')
[ -t 1 ] || { bold=""; dim=""; reset=""; }

say() { printf '%s\n' "$*"; }
ask() {
  # Read from the terminal even when this script arrives through a pipe.
  $assume_yes && return 0
  [ -r /dev/tty ] || return 1
  printf '%s [Y/n] ' "$1" > /dev/tty
  read -r reply < /dev/tty || return 1
  case "$reply" in ""|y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

root=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "modelbump: run this inside a git repository" >&2
  exit 1
}
cd "$root"

write() {
  local path="$1" content="$2"
  if [ -e "$path" ] && ! $force; then
    say "  ${dim}kept${reset}     $path ${dim}(exists; --force to overwrite)${reset}"
    return
  fi
  mkdir -p "$(dirname "$path")"
  printf '%s\n' "$content" > "$path"
  say "  ${bold}wrote${reset}    $path"
}

prerelease_line=""
$prerelease && prerelease_line=$'\n  allow-prerelease: true'
cost_block=""
if [ "$strategy" = "cost-optimized" ]; then
  cost_block=$'\n\n# Needs benchmark scores: add the ARTIFICIAL_ANALYSIS_API_KEY secret, or list\n# your own scores under catalog.scores.\ncost-optimized:\n  max-score-drop: 0.02\n  min-savings: 0.10'
  $prerelease && cost_block+=$'\n  allow-prerelease: true'
fi
grouping_block=""
[ "$grouping" = "single" ] && grouping_block=$'\n\npull-requests:\n  grouping: single'

say "${bold}Adding ModelBump to $(basename "$root")${reset}"

write ".github/workflows/modelbump.yml" "# Opens pull requests when newer versions of hard-coded LLM models ship.
# https://cheesejaguar.github.io/ModelBump/
name: ModelBump

on:
  schedule:
    - cron: \"$cron\"
  workflow_dispatch:

permissions:
  contents: write
  pull-requests: write
  issues: write # lets ModelBump create its PR labels

jobs:
  modelbump:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: cheesejaguar/ModelBump@v0
        env:
          # Optional. Benchmark scores for strategy: cost-optimized.
          ARTIFICIAL_ANALYSIS_API_KEY: \${{ secrets.ARTIFICIAL_ANALYSIS_API_KEY }}"

write ".github/modelbump.yml" "# ModelBump settings: https://cheesejaguar.github.io/ModelBump/#configure
version: 1

# same-family: newer release of the same model line (Opus -> newer Opus)
# cost-optimized: same benchmark score or better, lower price, same ecosystem
strategy: $strategy

same-family:
  update-types: [${updates//,/, }]${prerelease_line}${cost_block}${grouping_block}

ignore: []
#  - model: \"gpt-4o*\"        # never touch these slugs
#  - paths: [\"legacy/**\"]"

git add .github/workflows/modelbump.yml .github/modelbump.yml 2>/dev/null || true

repo=""
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  repo=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || true)
fi

say ""
if $touch_settings && [ -n "$repo" ]; then
  current=$(gh api "repos/$repo/actions/permissions/workflow" 2>/dev/null || true)
  if printf '%s' "$current" | grep -q '"can_approve_pull_request_reviews":true'; then
    say "  ${bold}ok${reset}       GitHub Actions can already open pull requests in $repo"
  elif [ -n "$current" ] && ask "Allow GitHub Actions to open pull requests in $repo? (repository setting)"; then
    perms=$(printf '%s' "$current" | sed -n 's/.*"default_workflow_permissions":"\([a-z]*\)".*/\1/p')
    if gh api -X PUT "repos/$repo/actions/permissions/workflow" \
      -f default_workflow_permissions="${perms:-read}" -F can_approve_pull_request_reviews=true >/dev/null 2>&1; then
      say "  ${bold}enabled${reset}  Actions can open pull requests in $repo"
    else
      say "  ${dim}could not change the setting (admin rights needed); see the last step below${reset}"
    fi
  fi
fi

say ""
say "${bold}Next${reset}"
say "  1. Commit and push:  git commit -m \"Add ModelBump\" && git push"
if [ -n "$repo" ]; then
  say "  2. Run it once:      gh workflow run modelbump.yml"
else
  say "  2. Run it once from the Actions tab (ModelBump -> Run workflow)."
fi
say "  ${dim}If pull requests fail to open, enable Settings -> Actions -> General ->"
say "  'Allow GitHub Actions to create and approve pull requests'.${reset}"
