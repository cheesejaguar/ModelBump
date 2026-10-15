# ModelBump

Dependabot for hard-coded LLM model slugs. ModelBump finds model IDs in your
code and config (`"claude-opus-5"`, `"anthropic/claude-sonnet-4.5"`,
`"us.anthropic.claude-sonnet-4-20250514-v1:0"`, `"gpt-4o-2024-08-06"`,
`"deepseek-ai/DeepSeek-V3.2"`, …) and opens one pull request per update:

> **Bump Claude Opus 5 to Claude Opus 5.5**
> `src/agent.py:12` `claude-opus-5` → `claude-opus-5-5` ·
> $5.00/$25.00 → $4.00/$20.00 per 1M tokens · links to the migration notes

## Defaults

These are the choices ModelBump makes when your config doesn't say otherwise:

- **Strategy: `same-family`.** A model only moves to a newer release of its own
  line (Opus → Opus, Sonnet → Sonnet, `gpt-*-mini` → `gpt-*-mini`,
  `qwen*-max` → `qwen*-max`). Snapshot, minor and major bumps are all allowed;
  limit them with `update-types`.
- **The original format is kept.** A slug is replaced with the same model's slug
  on the same host: OpenRouter ids stay OpenRouter ids (dots, `:batch`/`:free`
  variants, `:nitro` routing suffixes), Bedrock versioned ids stay versioned,
  Hugging Face ids stay Hugging Face ids, and dated snapshots stay dated where
  the newer model has one. If the newer model doesn't exist in that format, the
  slug is left alone and the plan says why.
- **Replacement slugs are never built from a pattern.** They come from the
  catalog (bundled data plus OpenRouter's live model list). The one exception is
  first-party OpenAI, Google and xAI ids, which are inferred from OpenRouter ids
  and flagged in the PR until a live vendor list confirms them (set
  `OPENAI_API_KEY`, or `derive-native-slugs: false` to turn inference off).
- **Previews are opt-in**, and deprecated or retired models are never bump
  targets. A model that is itself deprecated or retired is called out in every
  run.
- **`cost-optimized` stays inside an ecosystem**: Anthropic, OpenAI, Google,
  xAI, Meta and Mistral each stand alone, and DeepSeek, Qwen, Moonshot (Kimi),
  Z.ai (GLM), MiniMax, Xiaomi (MiMo), StepFun, Tencent and Baidu form one
  `chinese-open` ecosystem. Open-weight models are only swapped for open-weight
  models.

## Quick start

```bash
uv tool install git+https://github.com/cheesejaguar/ModelBump
```

```bash
modelbump plan --diff
```

| Command | What it does |
|---|---|
| `modelbump scan` | List every model slug found, with its model, host format and lifecycle status |
| `modelbump plan [--diff] [--json] [--fail-on-updates]` | Show proposed updates without changing anything |
| `modelbump apply` | Rewrite the slugs in the working tree |
| `modelbump pr [--dry-run] [--base main]` | Open or refresh one PR per update with `git` and `gh` |
| `modelbump catalog [--search opus] [--vendor openai]` | Inspect the model catalog |
| `modelbump init` | Write a commented starter `.github/modelbump.yml` |

Common flags: `--strategy cost-optimized`, `--offline` (cached or bundled data
only), `--refresh` (bypass the 24-hour cache), `-C path/to/repo`.

`plan --fail-on-updates` exits with status 2 when updates exist, so it also
works as a CI check.

## GitHub Action

Copy [`examples/modelbump-workflow.yml`](examples/modelbump-workflow.yml) to
`.github/workflows/modelbump.yml`:

```yaml
on:
  schedule:
    - cron: "0 6 * * 1"
  workflow_dispatch:
permissions:
  contents: write
  pull-requests: write
jobs:
  modelbump:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: cheesejaguar/ModelBump@main
        env:
          ARTIFICIAL_ANALYSIS_API_KEY: ${{ secrets.ARTIFICIAL_ANALYSIS_API_KEY }}
```

Turn on *Settings → Actions → General → Allow GitHub Actions to create and
approve pull requests*. PRs opened with the default `GITHUB_TOKEN` don't trigger
your other workflows; pass a GitHub App or fine-grained token as `github-token`
if CI should run on them.

Pull requests behave like Dependabot's:

- A branch is only rewritten when its edits change (new references, or the
  lines around them changed on the base branch). Unrelated commits on the base
  branch don't cause a force-push.
- Once someone else pushes a commit to a ModelBump branch, ModelBump leaves it
  alone.
- Closing a PR without merging stops that particular update; a newer target
  opens a new PR.
- A PR is closed when a newer target supersedes it (Opus 5 → 5.2 is closed
  when Opus 5 → 5.5 opens), and `open-pull-requests-limit` caps how many are
  open at once.
- Only tracked files are edited.

## Strategies

### `same-family`

Candidates are newer releases with the same vendor and line. "Newer" means a
later release date, falling back to the version number when dates are unknown,
because vendors don't always number in order (`grok-4.20` shipped before
`grok-4.3`). The newest allowed candidate that exists in the original format
wins.

| `update-types` | Example |
|---|---|
| `snapshot` | `gpt-4o-2024-08-06` → `gpt-4o-2024-11-20` |
| `minor` | `claude-opus-5` → `claude-opus-5-5` |
| `major` | `claude-opus-4-8` → `claude-opus-5-5` |

### `cost-optimized`

Candidates come from the same ecosystem (or the same vendor with
`scope: vendor`) and must:

- score at least `current × (1 − max-score-drop)` on the configured benchmark,
- cost at most `current × (1 − min-savings)` at the blended price
  (`price-weights`, 3:1 input:output by default),
- be active and non-preview, accept the same input modalities and produce the
  same outputs, have at least the same context window, and not be older than
  the current model (`allow-older: false`).

The cheapest match wins (`prefer: best-score` picks the highest score instead).
If the current model is retiring and nothing qualifies, ModelBump falls back to
its same-family successor and says so in the PR.

**Benchmarks are required for this mode.** ModelBump ships no scores of its
own. Set `ARTIFICIAL_ANALYSIS_API_KEY` (free key from
[artificialanalysis.ai](https://artificialanalysis.ai)) to use its
Intelligence, Coding or Math index, or supply your own scores, for example from
your eval suite:

```yaml
catalog:
  benchmark: coding
  scores:                      # any slug of the model works as the key
    claude-opus-5-5: 74
    anthropic/claude-sonnet-5.5: 71
  scores-file: .github/model-scores.yml
```

Your scores take precedence over Artificial Analysis.

## Configuration

`.github/modelbump.yml` (also `.github/modelbump.yaml`, `.modelbump.yml`):

```yaml
version: 1
strategy: same-family

same-family:
  update-types: [snapshot, minor, major]
  allow-prerelease: false

cost-optimized:
  scope: ecosystem              # or vendor
  max-score-drop: 0.02
  min-savings: 0.10
  price-weights: {input: 3, output: 1}
  prefer: cheapest              # or best-score
  preserve-open-weights: true
  allow-older: false
  require-context: true

updates:                        # optional per-path rules, first match wins
  - paths: ["workers/batch/**"]
    strategy: cost-optimized
  - paths: ["**"]
    same-family:
      update-types: [snapshot, minor]

exclude: ["**/CHANGELOG*", "docs/archive/**"]

ignore:
  - model: "gpt-4o*"            # glob on the slug found in your code
  - model: "claude-*"
    update-types: [major]
  - target: "*-preview*"        # glob on the proposed replacement
  - paths: ["tests/fixtures/**"]

ecosystems:                     # replaces the default grouping for these vendors
  chinese-open: [deepseek, qwen, moonshotai, z-ai, minimax]

catalog:
  sources: [openrouter, anthropic, openai, artificial-analysis]
  cache-ttl-hours: 24
  benchmark: intelligence       # intelligence | coding | math | <any AA field>
  derive-native-slugs: true
  models:                       # add models the catalog doesn't know yet
    - vendor: anthropic
      name: Claude Opus 6
      released: 2027-01-01
      native: [claude-opus-6]
      openrouter: [anthropic/claude-opus-6]
      pricing: {input: 4, output: 20}

pull-requests:
  grouping: per-model           # or single
  branch-prefix: modelbump
  labels: [dependencies, llm-models]
  reviewers: []
  assignees: []
  commit-message-prefix: "chore(models):"
  open-pull-requests-limit: 5
```

Pin a single line with a comment:

```python
MODEL = "gpt-4o-2024-08-06"  # modelbump: ignore
# modelbump: ignore-next-line
EVAL_BASELINE = "claude-opus-4-5"
```

The config file and `scores-file` are never rewritten.

## The catalog

| Source | Key | Provides |
|---|---|---|
| Bundled seed (`src/modelbump/data/seed.yaml`) | – | First-party Claude ids and dated snapshots, Bedrock and Vertex AI ids, pinned OpenAI snapshots, deprecations and retirements |
| OpenRouter `/api/v1/models` | – | Every model and its prices, release date, context window, modalities, Hugging Face id and expiry date; a snapshot is bundled for offline use |
| Anthropic Models API | `ANTHROPIC_API_KEY` + `pip install "modelbump[anthropic]"` | Confirms first-party Claude ids, picks up new ones |
| OpenAI `/v1/models` | `OPENAI_API_KEY` | Confirms inferred first-party OpenAI ids |
| Artificial Analysis | `ARTIFICIAL_ANALYSIS_API_KEY` | Benchmark scores |

Live responses are cached for 24 hours in `~/.cache/modelbump`
(`MODELBUMP_CACHE_DIR` overrides). If a fetch fails, ModelBump falls back to
the stale cache, then to the bundled snapshot, and the PR body names the data
it used.

### How matching works

Every slug is parsed into an identity: vendor, model line, version and
pre-release flag. `claude-opus-4-5-20251101`, `anthropic/claude-opus-4.5`,
`anthropic.claude-opus-4-5-20251101-v1:0` and `claude-opus-4-5@20251101` are
all `anthropic/claude-opus@4.5`; `claude-3-5-sonnet-20241022` is
`anthropic/claude-sonnet@3.5`; `Qwen/Qwen3-235B-A22B-Instruct-2507` is
`qwen/qwen-235b-a22b@3`. Bumping moves along the version axis of one identity
family; the replacement is then looked up in the catalog in the original
format.

Detection only matches exact catalog slugs standing alone: `claude-opus-5`
doesn't match inside `claude-opus-5-5`, `gpt-5` doesn't match inside an unknown
`gpt-5.9`, and a first-party id inside an unknown Bedrock id is left alone.
Separator-free ids such as `o3` only match inside quotes.

## Limitations

- A model bump can change behaviour and accepted API parameters (for example,
  newer Claude models reject some `thinking` settings that older ones accept).
  ModelBump changes the string; your tests and evals decide whether to merge.
- Line names are taken from the slugs. When a vendor renames a line
  (`gpt-5-mini` → `gpt-5.6-luna`), ModelBump won't connect them in
  `same-family` mode; `cost-optimized` can still find the new line.
- Only vendors in `src/modelbump/vendors.py` are recognized.

## Development

```bash
uv sync
uv run pytest
```
