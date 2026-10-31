# cost-per-task

**Price per token is the wrong unit.** Models differ in how many tokens they
burn, how often they need retries, and how often they fail outright — so the
cheapest model on a pricing page is routinely *not* the cheapest model per
job done. `cost-per-task` ranks LLMs by the number that actually hits your
bill:

```text
cost per successful task = total spend (every attempt, failures included)
                           ───────────────────────────────────────────────
                           tasks that succeeded at least once
```

Feed it run logs from your own evaluation harness and it produces a ranked
Markdown report (plus machine-readable JSON): success rate, attempts,
total spend, **cost per successful task**, median latency, and tokens per
task — with the ranking flip called out when the per-token-cheapest model
loses. Pure Python, zero dependencies.

## The flip, in one table

Bundled demo (`cost-per-task demo`): the same 20 tasks run against three
models, retries included, prompt-cache discounts applied.

| Rank | Model | Tasks | Success rate | Attempts | Total cost | Cost / successful task | Median latency | Tokens / task |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | claude-haiku-4-5 | 20 | 90% | 25 | $0.1509 | **$0.0084** | 3.8s | 5,731 |
| 2 | gpt-5-mini | 20 | 60% | 55 | $0.1329 | $0.0111 | 6.2s | 17,607 |
| 3 | gpt-5 | 20 | 100% | 22 | $0.3100 | $0.0155 | 9.8s | 5,650 |

> Cheapest per token (gpt-5-mini) is NOT cheapest per successful task: it
> succeeds on 60% of tasks with 2.8 attempts/task, so claude-haiku-4-5 wins
> once retries and failures are counted ($0.0084 vs $0.0111 per successful
> task).

Read that table carefully: gpt-5-mini has the **lowest total spend** *and*
the lowest per-token price — and still loses, because 40% of its tasks never
complete and each retry re-sends a growing context (17,607 tokens/task vs
~5,700 for the others). gpt-5 is flawless and still the most expensive way
to finish a task. Neither fact is visible in a per-token price list.

## Quickstart

```bash
pip install cost-per-task        # or from a clone: pip install .

cost-per-task demo                       # analyze the bundled sample log
cost-per-task analyze runs.jsonl         # your harness log -> ranked table
cost-per-task analyze runs.jsonl --format json --output report.json
cost-per-task analyze router.jsonl --cache-loss-fraction 1  # price mid-session model switches
cost-per-task demo-handoff --from-model gpt-5 --to-model gpt-5-mini  # handoff curve on the bundled experiment log
cost-per-task prices                     # show the price table in effect
cost-per-task analyze runs.jsonl --prices my-prices.json   # override prices
```

No installation needed for the demo if you clone the repo:

```bash
python -m cost_per_task demo
```

## Run-log format

One JSON object per line (JSONL), **one line per attempt** — a single model
invocation for a single task, as produced by your harness:

```json
{"model": "gpt-5-mini", "task_id": "task-01", "success": false,
 "input_tokens": 4000, "output_tokens": 812,
 "cached_input_tokens": 0, "latency_s": 5.9, "attempts": 1}
{"model": "gpt-5-mini", "task_id": "task-01", "success": true,
 "input_tokens": 5200, "output_tokens": 640,
 "cached_input_tokens": 3120, "latency_s": 4.8, "attempts": 2}
```

| Field | Required | Meaning |
|---|---|---|
| `model` | yes | Must match a name in the price table (or your `--prices` file). |
| `task_id` | yes | Groups attempts into tasks. Use the *same* ids across models for paired comparisons. |
| `success` | yes | Did **this attempt** succeed? |
| `input_tokens` | yes | Total input tokens for the attempt, *including* cached ones. |
| `output_tokens` | yes | Output tokens for the attempt. |
| `cached_input_tokens` | no (0) | Subset of `input_tokens` served from the prompt cache. |
| `latency_s` | no (0) | Wall-clock seconds for the attempt. |
| `attempts` | no | Informational only: 1-based ordinal of the attempt within the task. Grouping never relies on it. |
| `run_id` | no | Identifies one *run* (one session) when the same task was executed several times — e.g. a handoff experiment that ran each task under several switch points. Ignored by `analyze`; required by `handoff`. |

## Semantics (the decisions that matter)

- **Retries are not free.** Every attempt's tokens are billed, including
  attempts for tasks that ultimately fail. Money spent on failures is real
  money.
- **A task succeeds if any attempt succeeded.** Success rate is per task,
  not per attempt — a model that fails twice and succeeds on the third try
  gets the task, but pays for all three attempts.
- **Cost per successful task** divides *total* spend (all tasks, all
  attempts) by the number of successful tasks. A model with zero successes
  has no value for this metric: it ranks last and shows `—` (JSON: `null`).
- **Cached tokens** are billed at the model's cached-input price when the
  price table lists one; otherwise at `--cached-fraction` (default `0.5`)
  of the regular input price.
- **Model switches re-bill context** (opt-in). In a router/escalation log,
  one `task_id` is one session handed between models — and a model cannot
  reuse another model's prompt cache. `--cache-loss-fraction` charges the
  destination model for re-reading the accumulated context; see
  [Model switches and cache loss](#model-switches-and-cache-loss).
- **Ranking** is strictly by cost per successful task. Total cost and
  per-attempt cost are reported for context but never used to rank — both
  mislead in opposite directions.

## Model switches and cache loss

The ranking above prices each model on its own attempts. That misses a
cost hiding in *router* and *escalation* setups, where a cheap model
takes the first shot at a task and a stronger model takes over
mid-session when it fails: **the new model cannot reuse the previous
model's prompt cache.** The context accumulated so far has to be ingested
again at the new model's full input price — and the pricier the model you
escalate to, the more that re-read costs.

For logs where one `task_id` is one session handed between models, turn
it on:

```bash
cost-per-task analyze router.jsonl --cache-loss-fraction 1
```

(API equivalent: `analyze(attempts, prices, cache_loss_fraction=1.0)`.)

Semantics: attempts are read in log order; whenever a task's attempt
names a different model than the task's previous attempt, that is a
switch. The destination model is charged — once per switch — the sum of
`input_tokens` over the task's earlier attempts (the accumulated
context) at its own full, uncached input price, scaled by the fraction
(0–1; use less than 1 if only part of the context would realistically
have stayed cached). The charge is included in the model's total cost
and cost per successful task, broken out as `cache_loss_cost` (JSON:
`cache_loss_cost_usd`, with the switch count in `switches_in`), and
flagged in the insights when nonzero.

It is **off by default** (fraction 0) on purpose: the log format carries
no session marker, and paired-comparison logs reuse the same task ids
across models that each ran independently — charging there would tax
head-to-head comparisons for switches that never happened. At fraction 0
every result is exactly what earlier versions produced.

Motivation: history-aware routing research reported in Latent Space
AINews ([2026-10-16](https://www.latent.space/p/ainews-high-taste-testers-ep-1))
found that giving a router the session history helps, but that once
cache costs are counted, switching up to the expensive model mid-session
is often not worth it. This option lets you run the same arithmetic on
your own logs: "start cheap, escalate later" stops looking cheap the
moment the re-read is billed.

## Handoff curves: when to switch models mid-task

Cache loss prices a switch you already made. The prior question is
*where* to switch: start every task on a strong model and hand off to a
cheap one too early, and the cheap model fumbles tasks the strong model
had already set up; hand off too late (or never) and you paid strong-model
prices for steps the cheap model could have finished. The answer is an
experiment, not a guess: run the same tasks repeatedly, each run handing
off after a different number of steps, then aggregate by switch point.

That is what `handoff` does. Records must carry `run_id` (one run = one
task under one switch point — see the format table above):

```bash
cost-per-task handoff experiment.jsonl --from-model gpt-5 --to-model gpt-5-mini
cost-per-task demo-handoff --from-model gpt-5 --to-model gpt-5-mini  # bundled sample
```

The bundled experiment runs 12 tasks × 5 switch points (60 runs), and the
curve makes the tradeoff concrete:

| Switch after | Runs | Success rate | Total cost | Cost / run | Cost / successful run |
|---|---:|---:|---:|---:|---:|
| 0 | 12 | 58% | $0.2178 | $0.0182 | $0.0311 |
| 1 | 12 | 83% | $0.3757 | $0.0313 | $0.0376 |
| 2 **(recommended)** | 12 | 92% | $0.5377 | $0.0448 | $0.0489 |
| 3 | 12 | 92% | $0.6982 | $0.0582 | $0.0635 |
| never | 12 | 92% | $1.1164 | $0.0930 | $0.1015 |

Semantics:

- **Switch point** = the number of leading attempts served by the
  from-model before the first to-model attempt in that run. `0` is the
  all-cheap endpoint, `never` the all-strong one; switch-backs after the
  first switch are billed but do not move the point.
- A run **succeeds** if any of its attempts succeeded; its cost is the
  sum of its attempts, plus the cache-loss charge at each switch when
  `--cache-loss-fraction` is set (same accounting as `analyze`).
- **Recommendation**: among points whose success rate is within
  `--tolerance` (default 0.05) of the best observed rate, the lowest cost
  per successful run. In the sample, handing off after 2 steps matches
  never switching on success (92%) at **52% lower cost per successful
  run**.
- Runs that cannot be classified for the pair (a third model appears, or
  the run goes cheap → strong — the escalation direction) are counted in
  the report as excluded, never silently dropped; a record without
  `run_id` fails loudly.

Motivation: the AgentBaton results reported in Latent Space AINews
([2026-10-30](https://www.latent.space/p/ainews-building-aie-code-the-top-coding-agent-conference/))
— a single mid-task handoff from a strong model to a cheap one cut API
cost by 65.9%. Savings like that live or die on *where* the handoff
happens; this report finds that point from your own experiment logs
instead of someone else's workload.

## Cache scenarios: what the prompt cache is worth

Every report above bills cached tokens at the cached rate, as recorded
— one number that hides how much of the bill the prompt cache is
carrying. `cache` recomputes the same log under three scenarios:

```bash
cost-per-task cache runs.jsonl
cost-per-task demo-cache  # bundled sample
```

- **With cache** — as recorded: cached input tokens at the cached rate.
- **No cache** — every cached token re-billed at the full input rate:
  what identical traffic would cost with prompt caching off.
- **One bust** — exactly one full cache invalidation per session: the
  session's largest cache read is re-billed at the full input rate
  (the accumulated prefix has to be re-ingested), every other attempt
  bills as recorded. A worst-case single bust, placed where it hurts
  most.

Sessions are attempts sharing a `run_id` when present, else a
`task_id`, in log order — the unit caching actually works on. The
bundled sample (10 sessions × 8 attempts, contexts growing ~10K tokens
per step, nearly all of it cache-served from the second step on) shows
the shape of the answer:

| Model | With cache | No cache | No-cache × | One bust | Bust adds |
|---|---:|---:|---:|---:|---:|
| claude-sonnet-4-5 | $1.8713 | $5.5940 | 2.99x | $2.8116 | +50.2% |
| gpt-5 | $0.8042 | $2.3505 | 2.92x | $1.1946 | +48.6% |
| **All models** | $2.6755 | $7.9445 | 2.97x | $4.0062 | +49.7% |

Read it as two questions. The no-cache column asks *how much of this
bill is the cache paying for* — if the multiplier is large, cache hit
rate matters more than per-token price, and a caching regression will
swamp any model-price win. The bust column asks *how fragile that is*:
one invalidation per session, at the worst point, adds half again to
this sample's bill. A log that records no cached tokens at all gets a
third answer — all three scenarios coincide, which usually means the
cache is not engaging in the first place.

Motivation: an Agent Arena cost analysis reported in Latent Space
AINews
([2026-10-31](https://www.latent.space/p/ainews-google-admits-ai-mode-hurts-search-revenue-15-500b-drop-worst-since-2004-ipo-ai-bear-market-c/))
— across 26K sessions, top agent tasks cost 3.6× more with prompt
caching off, and a single cache invalidation raised the bill by ~5%.
The main analysis could not show either number; this report prices
both from your own logs.

## Price table

The bundled table (`src/cost_per_task/data/prices.json`) covers a handful
of GPT / Claude / Gemini models with an `as_of` date. **It is example data**:
providers change prices frequently, so verify against the provider's pricing
page, or supply your own file — same shape, entries override or extend the
bundled ones by model name:

```json
{"models": {"my-fine-tune": {"input_per_m": 0.40, "output_per_m": 1.60,
                             "cached_input_per_m": 0.04}}}
```

Prices are USD per 1M tokens. `cached_input_per_m` is optional.

## Sample data

`src/cost_per_task/data/sample_runs.jsonl` is synthetic (generated by
`scripts/generate_sample.py`, fixed seed, fully offline) but shaped like
real harness output: 20 shared tasks × 3 models, retry contexts that grow,
and retries that lean on the prompt cache. Regenerate it any time:

```bash
python scripts/generate_sample.py
```

`src/cost_per_task/data/sample_handoff.jsonl` is the handoff-experiment
sample used by `demo-handoff`: 12 tasks × 5 switch points of
gpt-5 → gpt-5-mini, every attempt carrying a `run_id`
(`scripts/generate_handoff_sample.py`).

`src/cost_per_task/data/sample_cache.jsonl` is the cache-scenario
sample used by `demo-cache`: 10 sessions × 8 attempts (gpt-5 and
claude-sonnet-4-5), contexts growing ~10K tokens per step with nearly
all of the accumulated context cache-served
(`scripts/generate_cache_sample.py`).

## Limitations

- **Bring your own logs.** This tool does not call models or run evals; it
  prices and aggregates logs your harness already produced. Comparisons are
  only fair if the models ran the *same* tasks under the *same* harness.
- **Bundled prices are a dated snapshot**, not a live feed — see above.
- **Cost is not quality.** Two models can both "succeed" with very different
  output quality; pair this with a quality eval before switching providers.
- Token counts are trusted as reported; the tool validates shape and basic
  sanity (e.g. cached ≤ input) but cannot detect a mis-instrumented harness.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e . pytest
python -m pytest        # 80 tests, fully offline
```

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Gang Ji.
