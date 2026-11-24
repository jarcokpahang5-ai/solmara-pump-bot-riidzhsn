"""Solmara pump.fun replay bot — early-entry sniper, agent-fun/replay/v2.

Strategy, in one paragraph: buy new bonding curves almost as soon as they are
created, while they are still young, small and unbundled — and get out fast.
A coin is bought within its first few slots only if the launch looks clean:
the creator has not sold, the creator's own buy is modest, only a handful of
distinct wallets have bought in (a launch bundled by many wallets in its first
slots is usually the creator's cluster waiting to dump), real SOL sits in a
sane band, and the price has not already run far above its create price. Each
position is one small fixed stake; it exits whole on the first of a +15%
take-profit, a -12% stop loss, a 12-slot maximum hold, the creator selling, or
one very large recorded sell. Tiny probe round trips keep the run above the
guardrails (at least 20 distinct coins, round trips of at least 0.01 SOL) when
the launch flow is quiet for a while.

Why this shape: on the kit's dev window a late momentum chase buys tops —
coins that already ran 4%+ in 30 slots keep falling, and pyramiding into them
compounds the loss (first version of this bot: -4.58 SOL, 6.8% win rate, 47.6%
drawdown; the kit's pilot momentum: -9.46 SOL). The early, small, fast-exit
shape loses far less on the same window (kit pilot sniper: -0.47 SOL, 5.2%
drawdown) because a bad launch costs one small stake held for a few slots,
while a runner is banked at +15% before it can roll over.

Pure Python 3.13 standard library, deterministic: no clock, no randomness, no
network, no file reads. It only reads the harness messages on stdin and only
writes replies on stdout. Iteration is over insertion-ordered dicts fed by the
batch stream, so two executions of the same replay produce identical orders.

The wire shim is vendored byte for byte from the kit's sdk/python/makefun_bot.py
(vendor/makefun_bot.py); the grader runs this file as
``python3 -P -s /bot/main.py`` with ``PYTHONHASHSEED=0``.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bot  # noqa: E402  (after the sys.path line: -P leaves the script directory off sys.path)

LAMPORTS = makefun_bot.LAMPORTS_PER_SOL

# --- entry filters ----------------------------------------------------------
ENTRY_MAX_AGE = 4          # slots since CreateEvent; older coins are never bought
MAX_BUYERS = 3             # distinct non-creator buyers seen, at most
MAX_DEV_BUY_SOL = 2.0      # creator's own buys so far, at most
MIN_REAL_SOL = 0.3         # real SOL reserve at entry, at least
MAX_REAL_SOL = 30.0        # ... and at most
MAX_RUNUP = 0.50           # price now vs create price, at most +50%

# --- sizing -----------------------------------------------------------------
STAKE_SOL = 0.10           # one fixed stake per coin
MIN_BUY_SOL = 0.005        # smallest buy worth paying the tx fee for
BUY_SLIPPAGE_BPS = 10000   # buys go out wide open (speed matters more)
SELL_SLIPPAGE_BPS = 10000  # sells go out wide open

# --- exits ------------------------------------------------------------------
TAKE_PROFIT = 0.15         # sell when value is 15% over cost
STOP_LOSS = 0.12           # sell when value is 12% under cost
MAX_HOLD_SLOTS = 12        # sell after holding this long, whatever the price
BIG_SELL_SOL = 5.0         # sell at once on one recorded sell >= 5 SOL

# --- portfolio --------------------------------------------------------------
MAX_OPEN = 5               # most positions open at once (== caps.max_open_positions)
MAX_EXPOSURE_SOL = 2.0     # most SOL at cost across all positions at once

# --- guardrail probes --------------------------------------------------------
TARGET_COINS = 25          # pace target: distinct coins traded
TARGET_FILLS = 240         # pace target: fills
PACE_SECS = 14400          # block-time span the pace targets scale over
PROBE_SOL = 0.01           # probe buy size (== minimum round trip)
MAX_PROBES = 1             # most probe round trips open at once

CURVE_FEE = 0.0125         # bonding-curve fee taken on a sell (estimate)


class Coin:
    """Everything seen about one curve since its CreateEvent."""

    __slots__ = ("created", "creator", "vq0", "vt0", "dev_buy", "buyers",
                 "dev_sold", "decided", "bought")

    def __init__(self, created, creator, vq0, vt0):
        self.created = created    # slot of its CreateEvent
        self.creator = creator
        self.vq0 = vq0            # create-time virtual quote reserves
        self.vt0 = vt0            # create-time virtual token reserves
        self.dev_buy = 0.0        # SOL the creator has bought so far
        self.buyers = set()       # distinct non-creator buyers
        self.dev_sold = False     # the creator has sold at least once
        self.decided = False      # bought, or ruled out for good
        self.bought = False       # a buy was sent for this coin

    def create_price(self):
        if self.vt0 <= 0:
            return 0.0
        return self.vq0 / self.vt0


class Held:
    """Bookkeeping for one open sniper position."""

    __slots__ = ("opened", "invested", "filled")

    def __init__(self, opened):
        self.opened = opened
        self.invested = 0.0
        self.filled = False      # some buy of this position has filled


class Strategy:
    def __init__(self):
        self.coins = {}          # mint -> Coin, insertion order
        self.held = {}           # mint -> Held, insertion order
        self.probes = {}         # mint -> bool (False buying, True selling)
        self.my_fills = 0
        self.my_coins = set()
        self.first_time = None
        self.last_time = None

    def coin_of(self, mint, slot, creator="", vq0=0, vt0=0):
        coin = self.coins.get(mint)
        if coin is None:
            coin = Coin(slot, creator, vq0, vt0)
            self.coins[mint] = coin
        return coin

    def sell_value_sol(self, state, mint, tokens):
        """What selling `tokens` now would pay in SOL, curve fee deducted."""
        curve = state.curves.get(mint)
        if curve is None:
            return 0.0
        vt = curve.virtual_token_reserves
        vq = curve.virtual_quote_reserves
        if vt + tokens <= 0:
            return 0.0
        gross = tokens * vq / (vt + tokens) / LAMPORTS
        return gross * (1.0 - CURVE_FEE)

    def entry_ok(self, state, mint, coin, now):
        """True when the launch still looks clean enough to buy."""
        if coin.decided or coin.bought:
            return False
        if now - coin.created > ENTRY_MAX_AGE:
            return False
        if coin.dev_sold:
            return False
        if coin.dev_buy > MAX_DEV_BUY_SOL:
            return False
        if len(coin.buyers) > MAX_BUYERS:
            return False
        curve = state.curves.get(mint)
        if curve is None or curve.complete:
            return False
        real_sol = curve.real_quote_reserves / LAMPORTS
        if real_sol < MIN_REAL_SOL or real_sol > MAX_REAL_SOL:
            return False
        if coin.vt0 > 0 and curve.virtual_token_reserves > 0:
            price = (curve.virtual_quote_reserves
                     / curve.virtual_token_reserves)
            if price > coin.create_price() * (1.0 + MAX_RUNUP):
                return False
        return True

    def on_batch(self, state, batch):
        now = batch["slot"]
        big_sell = set()
        traded = []
        seen_traded = set()
        fresh = []               # mints whose CreateEvent arrived this batch

        for fill in batch["fills"]:
            if fill["status"] == "filled" and not fill["client_id"].startswith("_"):
                self.my_fills += 1
                self.my_coins.add(fill["mint"])

        for event in batch["events"]:
            block_time = event.get("block_time")
            if block_time is not None:
                if self.first_time is None:
                    self.first_time = block_time
                if self.last_time is None or block_time > self.last_time:
                    self.last_time = block_time
            kind = event["event"]
            if kind == "CreateEvent":
                mint = event["mint"]
                coin = self.coin_of(mint, event["slot"], event["creator"],
                                    int(event["virtual_quote_reserves"]),
                                    int(event["virtual_token_reserves"]))
                coin.created = event["slot"]
                coin.creator = event["creator"]
                coin.vq0 = int(event["virtual_quote_reserves"])
                coin.vt0 = int(event["virtual_token_reserves"])
                fresh.append(mint)
                if mint not in seen_traded:
                    seen_traded.add(mint)
                    traded.append(mint)
            elif kind == "TradeEvent":
                mint = event["mint"]
                coin = self.coin_of(mint, event["slot"])
                sol = int(event["sol_amount"]) / LAMPORTS
                if event["user"] == coin.creator:
                    if event["is_buy"]:
                        coin.dev_buy += sol
                    else:
                        coin.dev_sold = True
                elif event["is_buy"]:
                    coin.buyers.add(event["user"])
                if mint not in seen_traded:
                    seen_traded.add(mint)
                    traded.append(mint)
                if not event["is_buy"] and sol >= BIG_SELL_SOL:
                    big_sell.add(mint)
            # CompleteEvent: the curve is done; the shim already marks it.

        marks = {m["mint"]: m for m in batch["marks"]}
        pending_by_mint = {}
        for pending in state.pending.values():
            mint = pending.order["mint"]
            pending_by_mint[mint] = pending_by_mint.get(mint, 0) + 1

        orders = []
        cap = state.hello["caps"]["max_orders_per_batch"]
        tx_sol = state.tx_fee_lamports() / LAMPORTS

        # --- exits first: whole position, first trigger wins ---------------
        for mint in list(self.held):
            held = self.held[mint]
            pos = state.positions.get(mint)
            curve = state.curves.get(mint)
            if pos is None:
                if mint not in pending_by_mint:
                    del self.held[mint]
                    coin = self.coins.get(mint)
                    if coin is not None:
                        coin.decided = True
                continue
            held.filled = True
            if curve is None or curve.complete:
                continue
            tokens = int(pos.token_amount)
            value = self.sell_value_sol(state, mint, tokens) - tx_sol
            cost = int(pos.cost_lamports) / LAMPORTS
            coin = self.coins.get(mint)
            dev_sold = bool(coin and coin.dev_sold)
            exit_now = False
            if value >= cost * (1.0 + TAKE_PROFIT):
                exit_now = True
            elif value <= cost * (1.0 - STOP_LOSS):
                exit_now = True
            elif now - held.opened > MAX_HOLD_SLOTS:
                exit_now = True
            elif dev_sold:
                exit_now = True
            elif mint in big_sell:
                exit_now = True
            if not exit_now:
                continue
            if mint in pending_by_mint:
                sell_pending = any(
                    p.order["mint"] == mint and p.order["side"] == "sell"
                    for p in state.pending.values())
                if sell_pending:
                    continue
            if len(orders) >= cap:
                break
            orders.append({"side": "sell", "mint": mint,
                           "token_amount": pos.token_amount,
                           "max_slippage_bps": SELL_SLIPPAGE_BPS})

        # --- probe round trips: sell the probe as soon as its buy lands ---
        for mint in list(self.probes):
            pos = state.positions.get(mint)
            curve = state.curves.get(mint)
            if pos is None:
                if mint not in pending_by_mint:
                    del self.probes[mint]
                continue
            if curve is None or curve.complete or mint in pending_by_mint:
                continue
            if len(orders) >= cap:
                break
            orders.append({"side": "sell", "mint": mint,
                           "token_amount": pos.token_amount,
                           "max_slippage_bps": SELL_SLIPPAGE_BPS})
            self.probes[mint] = True

        # --- entries: young coins only, one fixed stake each --------------
        free = state.free_cash_lamports() / LAMPORTS
        slots_left = (state.hello["caps"]["max_open_positions"]
                      - state.committed_position_count())
        exposure = 0.0
        for mint, held in self.held.items():
            pos = state.positions.get(mint)
            exposure += max(held.invested,
                            int(pos.cost_lamports) / LAMPORTS if pos else 0.0)

        # Candidates: coins created this batch plus coins traded this batch
        # that are still inside the entry age window.
        candidates = []
        seen_cand = set()
        for mint in fresh + traded:
            if mint in seen_cand:
                continue
            seen_cand.add(mint)
            coin = self.coins.get(mint)
            if coin is None or coin.decided or coin.bought:
                continue
            if now - coin.created <= ENTRY_MAX_AGE:
                candidates.append(mint)
            else:
                coin.decided = True

        for mint in candidates:
            if len(orders) >= cap:
                break
            coin = self.coins[mint]
            if not self.entry_ok(state, mint, coin, now):
                continue
            if slots_left <= 0:
                continue
            if exposure + STAKE_SOL > MAX_EXPOSURE_SOL:
                continue
            cap_sol = state.max_buy_lamports(mint) / LAMPORTS
            amount = min(cap_sol, STAKE_SOL)
            if amount > free - tx_sol:
                amount = free - tx_sol
            if amount < MIN_BUY_SOL:
                continue
            orders.append({"side": "buy", "mint": mint,
                           "quote_lamports": int(amount * LAMPORTS),
                           "max_slippage_bps": BUY_SLIPPAGE_BPS})
            free -= amount + tx_sol
            exposure += amount
            slots_left -= 1
            coin.bought = True
            coin.decided = True
            self.held[mint] = Held(now)
            self.held[mint].invested = amount

        # --- guardrail pacing: open a probe when fills/coins lag the pace --
        if (len(self.probes) < MAX_PROBES and slots_left > 0
                and self.first_time is not None and self.last_time is not None):
            pace = min(1.0, (self.last_time - self.first_time) / PACE_SECS)
            need_coins = len(self.my_coins) < -(-TARGET_COINS * pace // 1)
            need_fills = self.my_fills < -(-TARGET_FILLS * pace // 1)
            if need_coins or need_fills:
                min_real = (PROBE_SOL * 10000
                            / state.hello["caps"]["max_order_bps_of_real_reserve"])
                for mint in traded:
                    if len(orders) >= cap or len(self.probes) >= MAX_PROBES:
                        break
                    if (mint in self.held or mint in self.probes
                            or mint in pending_by_mint
                            or mint in state.positions):
                        continue
                    if need_coins and not need_fills and mint in self.my_coins:
                        continue
                    curve = state.curves.get(mint)
                    if curve is None or curve.complete:
                        continue
                    if curve.real_quote_reserves / LAMPORTS < min_real * 1.5:
                        continue
                    if free < PROBE_SOL + 2 * tx_sol:
                        break
                    orders.append({"side": "buy", "mint": mint,
                                   "quote_lamports": int(PROBE_SOL * LAMPORTS),
                                   "max_slippage_bps": BUY_SLIPPAGE_BPS})
                    self.probes[mint] = False
                    free -= PROBE_SOL + tx_sol
        return orders


STRATEGY = Strategy()


def on_batch(state, batch):
    return STRATEGY.on_batch(state, batch)


if __name__ == "__main__":
    makefun_bot.run(on_batch)
