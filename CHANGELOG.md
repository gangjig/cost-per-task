# Changelog

## 0.4.0 — 2026-10-31

- **Cache scenarios: what prompt caching is worth, and what losing it
  costs.** New `cache` subcommand (plus `demo-cache` on a bundled
  session log) and `analyze_cache_scenarios()` API. The log is
  recomputed per model under three scenarios: as recorded (cached
  tokens at the cached rate), with caching off (every cached token
  re-billed at the full input rate), and with one full cache
  invalidation per session (the session's largest cache read re-billed
  at the full input rate — a worst-case single bust). Sessions are
  attempts sharing a `run_id`, else a `task_id`, in log order. The
  report shows the no-cache multiplier and the bust uplift per model,
  so "how much of the bill is the cache paying for" and "how much
  rides on the cache surviving a session" become numbers instead of
  vibes; a log with no cached tokens reports all three scenarios
  coinciding. In the bundled sample (10 sessions × 8 attempts), the
  same traffic costs 2.97x as much with caching off, and one
  worst-case bust per session adds +49.7%.
  Motivated by an Agent Arena cost analysis reported in Latent Space
  AINews (2026-10-31) — 26K sessions; top agent tasks cost 3.6x more
  without prompt caching, and a single cache invalidation raised the
  bill by ~5%:
  https://www.latent.space/p/ainews-google-admits-ai-mode-hurts-search-revenue-15-500b-drop-worst-since-2004-ipo-ai-bear-market-c/

## 0.3.0 — 2026-10-30

- **Handoff curves: find the switch point, not just the switch cost.**
  New `handoff` subcommand (plus `demo-handoff` on a bundled experiment
  log) and `analyze_handoff()` API. Records gain an optional `run_id`
  identifying one run of a handoff experiment — the same tasks run
  repeatedly, each run handing off from a strong model to a cheap one
  after a different number of steps. Runs are aggregated by switch point
  (0 = all cheap, `never` = all strong): runs, success rate, total cost,
  cost per run, and cost per successful run per point, with a recommended
  point — the lowest cost per successful run among points whose success
  rate is within `--tolerance` (default 0.05) of the best. Cache-loss
  accounting applies at switches when opted in, with the same semantics
  as `analyze`. Unclassifiable runs (a third model, or the escalation
  direction) are counted as excluded; a record without `run_id` fails
  loudly. In the bundled sample (12 tasks × 5 switch points,
  gpt-5 → gpt-5-mini), handing off after 2 steps matches never switching
  on success (92%) at 52% lower cost per successful run.
  Motivated by the AgentBaton results reported in Latent Space AINews
  (2026-10-30) — a single mid-task handoff cut API cost by 65.9%:
  https://www.latent.space/p/ainews-building-aie-code-the-top-coding-agent-conference/

## 0.2.0 — 2026-10-17

- **Cache-loss accounting for router/escalation logs (opt-in).**
  `analyze()` gains a `cache_loss_fraction` parameter and the CLI a
  `--cache-loss-fraction` flag (0–1, default 0 = off). When a task's
  attempts switch models mid-session, the destination model is charged a
  one-time re-billing of the task's accumulated context tokens — the sum
  of `input_tokens` over its earlier attempts — at its full (uncached)
  input price, scaled by the fraction. Escalation strategies were
  previously under-costed because a new model cannot reuse the previous
  model's prompt cache. `ModelStats` gains `cache_loss_cost` and
  `switches_in`; JSON reports add `cache_loss_cost_usd` and
  `switches_in`; an insight calls out the charge when it is nonzero.
  With the default fraction of 0, all results are unchanged from 0.1.0.
  Motivated by history-aware routing research reported in Latent Space
  AINews (2026-10-16):
  https://www.latent.space/p/ainews-high-taste-testers-ep-1

## 0.1.0 — 2026-10-14

- Initial release: rank LLMs by cost per successful task from JSONL run
  logs, with retries and failures billed, prompt-cache discounts,
  Markdown and JSON reports, a bundled example price table, and a demo
  log.
