# Changelog

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
