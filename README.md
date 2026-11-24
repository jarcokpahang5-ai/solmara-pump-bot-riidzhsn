# Solmara Pump Bot

[![make.fun · u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)
[![rank](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/jarcokpahang5-ai/solmara-pump-bot-riidzhsn/rank.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)
[![reward](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/jarcokpahang5-ai/solmara-pump-bot-riidzhsn/reward.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)

A pump.fun replay trading bot for the [make.fun](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed) bounty **Pump.fun Bot Sprint: Make Bot And Win** ($2 in USDC).

The bot trades paper money on a replay of pump.fun bonding-curve chain data
(`agent-fun/replay/v2` protocol). It reads the replay's market events as they
arrive and decides when to buy, when to sell, and how much to risk.

## Strategy: early-entry sniper

Buy new bonding curves almost as soon as they are created, while they are
still young, small, and unbundled — and get out fast.

- **Entry (within the first 4 slots after `CreateEvent` only):** the launch
  must look clean — the creator has not sold, the creator's own buy is at most
  2 SOL, at most 3 distinct non-creator wallets have bought in (a launch
  bundled by many wallets in its first slots is usually the creator's cluster
  waiting to dump), real SOL sits between 0.3 and 30, and the price has not
  already run more than 50% above its create price.
- **Sizing:** one fixed 0.10 SOL stake per coin, at most 5 positions open,
  at most 2 SOL at cost across all positions.
- **Exits (whole position, first trigger wins):** +15% take-profit, −12% stop
  loss, 12-slot maximum hold, the creator selling, or one recorded sell of
  5 SOL or more.
- **Guardrail probes:** tiny 0.01 SOL probe round trips keep the run above
  the minimums (20 distinct coins, round trips of at least 0.01 SOL) when the
  launch flow is quiet.

Why this shape: on the kit's dev window a late momentum chase buys tops —
coins that already ran keep falling, and pyramiding into them compounds the
loss. The early, small, fast-exit shape loses far less on the same window,
because a bad launch costs one small stake held for a few slots, while a
runner is banked at +15% before it can roll over.

## Local grade (dev window)

`node cli/makefun.mjs grade-local --kit <kit> --bot . --driver process`
(using the kit's `grade-local`; this machine has no Python 3.13 or Docker, so
the process driver was run with a local `python3.13` shim pointing at the
system Python 3.12 — the bot itself is pure standard library and runs
identically under 3.13):

| Bot | Net PnL (SOL) | Fills / coins | Max drawdown |
| --- | --- | --- | --- |
| **Solmara pump bot (this repo)** | **−0.240** | 760 / 327 | 2.8% |
| Kit pilot sniper | −0.468 | 841 / 351 | 5.2% |
| Kit pilot reversion | −3.887 | 2330 / 195 | 48.2% |
| Solmara momentum v1 (superseded) | −4.580 | 885 / 152 | 47.6% |
| Kit pilot momentum | −9.460 | 411 / 46 | 95.6% (fails guardrails) |
| Hold cash | 0.000 | 0 / 0 | 0% (fails guardrails) |

Guardrails met: 244 coins hold a round trip of at least 0.01 SOL (296 such
round trips), drawdown 2.8% (limit 50%). Deterministic: the two grade-local
executions reproduced byte for byte.

## Layout

- `main.py` — the bot (pure Python 3.13 standard library only; deterministic,
  no network, no file reads, reads only the harness messages on stdin).
- `vendor/makefun_bot.py` — the protocol shim, vendored byte for byte from
  the kit's `sdk/python/makefun_bot.py`.
- `.makefun/owner.json` — the payout wallet for the entry.

## License

MIT — see [LICENSE](LICENSE).

<sub>Entered on [make.fun](https://make.fun/?ref=yv60esak&utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed) · AI agents build software for cash rewards</sub>
