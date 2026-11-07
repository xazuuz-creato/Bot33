
"""Rapid Guard V4 — Demo trading and isolated self-tests."""
import asyncio
import math
import os
import secrets
import sqlite3
import tempfile
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
DB_FILE = os.getenv("DATABASE_PATH", str(ROOT / "demo.sqlite3"))
ACCESS_KEY = os.getenv("APP_ACCESS_KEY", "")

# Configurable estimates, not actual Binance account fees.
FEE = float(os.getenv("DEMO_FEE_RATE", "0.001"))
SLIP = float(os.getenv("DEMO_SLIPPAGE_RATE", "0.0002"))
TARGET = float(os.getenv("TARGET_PCT", "0.45"))
STOP = float(os.getenv("STOP_PCT", "0.30"))
MIN_NET = float(os.getenv("MIN_NET_TARGET_PCT", "0.10"))

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
    "BNBUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT",
    "AVAXUSDT", "LTCUSDT", "DOTUSDT", "TRXUSDT"
]

if not (0 <= FEE < .02 and 0 <= SLIP < .02):
    raise RuntimeError("Invalid fee/slippage settings")


class Engine:
    def __init__(self, path):
        self.path = str(path)
        self.books = {}
        self.history = defaultdict(lambda: deque(maxlen=30))
        self.trades = defaultdict(lambda: deque(maxlen=400))
        self.tokens = {}
        self.lock = asyncio.Lock()

        Path(self.path).parent.mkdir(parents=True, exist_ok=True)

        with self.db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS wallet (
                    id INTEGER PRIMARY KEY,
                    cash REAL
                )
            """)
            db.execute(
                "INSERT OR IGNORE INTO wallet VALUES (1,10000)"
            )
            db.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY,
                    entry REAL,
                    qty REAL,
                    cost REAL,
                    buy_fee REAL,
                    opened REAL,
                    peak REAL,
                    stop REAL,
                    target REAL,
                    hold INTEGER
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    entry REAL,
                    exit REAL,
                    qty REAL,
                    buy_fee REAL,
                    sell_fee REAL,
                    gross REAL,
                    net REAL,
                    opened REAL,
                    closed REAL,
                    reason TEXT
                )
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def position(self, symbol):
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM positions WHERE symbol=?",
                (symbol,)
            ).fetchone()
        return dict(row) if row else None

    def snapshot(self):
        with self.db() as db:
            cash = db.execute(
                "SELECT cash FROM wallet WHERE id=1"
            ).fetchone()[0]

            positions = [
                dict(x)
                for x in db.execute("SELECT * FROM positions")
            ]

            history = [
                dict(x)
                for x in db.execute(
                    "SELECT * FROM history ORDER BY id DESC LIMIT 100"
                )
            ]

        equity = cash + sum(
            p["qty"] * self.books.get(
                p["symbol"], {}
            ).get("bid", p["entry"])
            for p in positions
        )

        return {
            "cashUsdt": cash,
            "equityUsdt": equity,
            "positions": positions,
            "history": history
        }

    def book(self, symbol, bids, asks):
        try:
            b = sorted(
                [
                    (float(x[0]), float(x[1]))
                    for x in bids[:5]
                ],
                reverse=True
            )
            a = sorted(
                [
                    (float(x[0]), float(x[1]))
                    for x in asks[:5]
                ]
            )
        except (ValueError, TypeError, IndexError) as exc:
            raise ValueError("Invalid book") from exc

        if (
            not b or not a
            or any(
                not math.isfinite(v) or v <= 0
                for pair in b + a
                for v in pair
            )
            or b[0][0] >= a[0][0]
        ):
            raise ValueError("Invalid or crossed book")

        now = time.time()
        bid, ask = b[0][0], a[0][0]

        buy_depth = sum(p * q for p, q in b)
        sell_depth = sum(p * q for p, q in a)
        ratio = min(buy_depth / sell_depth, 100)

        self.books[symbol] = {
            "bid": bid,
            "ask": ask,
            "ratio": ratio,
            "time": now
        }

        self.history[symbol].append({
            "mid": (bid + ask) / 2,
            "ratio": ratio,
            "time": now
        })

    def trade(self, symbol, price, qty, is_buyer_maker):
        if not all(
            math.isfinite(v) and v > 0
            for v in (price, qty)
        ):
            raise ValueError("Invalid trade")

        self.trades[symbol].append({
            "price": price,
            "qty": qty,
            "seller": is_buyer_maker,
            "time": time.time()
        })

    def features(self, symbol):
        now = time.time()
        b = self.books.get(symbol)

        h = [
            x for x in self.history[symbol]
            if now - x["time"] <= 8
        ]
        t = [
            x for x in self.trades[symbol]
            if now - x["time"] <= 5
        ]

        if (
            not b
            or now - b["time"] > 2.5
            or len(h) < 6
            or not t
        ):
            return None

        buy = sum(
            x["price"] * x["qty"]
            for x in t
            if not x["seller"]
        )
        sell = sum(
            x["price"] * x["qty"]
            for x in t
            if x["seller"]
        )

        if buy + sell <= 0:
            return None

        mid = len(h) // 2

        v1 = (
            (h[mid]["mid"] / h[0]["mid"] - 1)
            / max(h[mid]["time"] - h[0]["time"], .01)
        )
        v2 = (
            (h[-1]["mid"] / h[mid]["mid"] - 1)
            / max(h[-1]["time"] - h[mid]["time"], .01)
        )

        return {
            "imbalanceRatio": round(b["ratio"], 4),
            "buyRatio": round(buy / (buy + sell), 4),
            "sustainedImbalance": round(
                sum(x["ratio"] >= 1.5 for x in h) / len(h),
                4
            ),
            "spreadPct": round(
                (b["ask"] - b["bid"]) / b["ask"] * 100,
                5
            ),
            "velocityPctPerSec": round(
                (h[-1]["mid"] / h[0]["mid"] - 1)
                * 100
                / max(h[-1]["time"] - h[0]["time"], .01),
                5
            ),
            "acceleration": round((v2 - v1) * 100, 5),
            "buyNotional5s": round(buy, 2),
            "sellNotional5s": round(sell, 2)
        }

    def entry(self, symbol):
        f = self.features(symbol)

        if f is None:
            return {
                "enter": False,
                "score": 0,
                "reasons": ["بيانات غير كافية أو قديمة"]
            }

        friction = 100 * (2 * FEE + 2 * SLIP)
        friction += f["spreadPct"]

        checks = [
            (
                f["imbalanceRatio"] >= 1.5,
                "ضعف سيولة الشراء"
            ),
            (
                f["sustainedImbalance"] >= .7,
                "السيولة غير مستقرة"
            ),
            (
                f["buyRatio"] >= .60,
                "ضغط الشراء المنفذ ضعيف"
            ),
            (
                f["velocityPctPerSec"] > 0,
                "السعر لا يرتفع"
            ),
            (
                f["acceleration"] >= -.002,
                "تباطؤ الزخم"
            ),
            (
                f["spreadPct"] <= .05,
                "السبريد مرتفع"
            ),
            (
                TARGET - friction >= MIN_NET,
                "الهدف لا يغطي التكاليف"
            )
        ]

        reasons = [
            msg for ok, msg in checks
            if not ok
        ]

        with self.db() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM positions"
            ).fetchone()[0]

            today = int(time.time() // 86400) * 86400

            pnl = db.execute(
                """
                SELECT COALESCE(SUM(net),0)
                FROM history WHERE closed>=?
                """,
                (today,)
            ).fetchone()[0]

        if self.position(symbol):
            reasons.append("مركز موجود")

        if count >= 3:
            reasons.append("الحد الأقصى للمراكز")

        if pnl <= -25:
            reasons.append("حد الخسارة اليومي")

        result = {
            "enter": not reasons,
            "score": round(
                100 * sum(ok for ok, _ in checks) / len(checks)
            ),
            "reasons": reasons or [
                "الإشارة مستوفية للشروط"
            ],
            "features": f,
            "costEstimate": {
                "estimatedRoundTripCostPct": round(
                    friction, 5
                ),
                "netIfTargetReachedPct": round(
                    TARGET - friction, 5
                )
            }
        }

        if not reasons:
            token = secrets.token_urlsafe(24)

            self.tokens[token] = (
                symbol,
                time.time() + 3,
                self.books[symbol]["ask"]
            )

            result["signalToken"] = token

        if len(self.tokens) > 1000:
            self.tokens = {
                k: v
                for k, v in self.tokens.items()
                if v[1] > time.time()
            }

        return result

    def fresh_book(self, symbol):
        b = self.books.get(symbol)

        if not b or time.time() - b["time"] > 2.5:
            raise ValueError("Stale market data")

        return b

    def execute(
        self,
        symbol,
        action,
        amount=100,
        token=None,
        hold=180,
        reason="MANUAL"
    ):
        now = time.time()
        b = self.fresh_book(symbol)

        with self.db() as db:
            if action == "BUY":
                record = self.tokens.pop(token, None)

                if (
                    not record
                    or record[0] != symbol
                    or record[1] < now
                    or abs(b["ask"] / record[2] - 1) > .001
                ):
                    raise ValueError(
                        "Invalid/expired signal token"
                    )

                if self.position(symbol):
                    raise ValueError("Duplicate position")

                if not self.features(symbol):
                    raise ValueError("Stale signal")

                count = db.execute(
                    "SELECT COUNT(*) FROM positions"
                ).fetchone()[0]

                if count >= 3:
                    raise ValueError("Too many positions")

                today = int(now // 86400) * 86400

                pnl = db.execute(
                    """
                    SELECT COALESCE(SUM(net),0)
                    FROM history WHERE closed>=?
                    """,
                    (today,)
                ).fetchone()[0]

                if pnl <= -25:
                    raise ValueError("Daily loss limit")

                cash = db.execute(
                    "SELECT cash FROM wallet WHERE id=1"
                ).fetchone()[0]

                if (
                    not 10 <= amount <= 100000
                    or amount > cash
                ):
                    raise ValueError(
                        "Insufficient cash / invalid budget"
                    )

                price = b["ask"] * (1 + SLIP)
                cost = amount / (1 + FEE)
                buy_fee = cost * FEE
                qty = cost / price

                db.execute(
                    "UPDATE wallet SET cash=cash-? WHERE id=1",
                    (amount,)
                )

                db.execute(
                    """
                    INSERT INTO positions
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        symbol,
                        price,
                        qty,
                        cost,
                        buy_fee,
                        now,
                        price,
                        price * (1 - STOP / 100),
                        price * (1 + TARGET / 100),
                        hold
                    )
                )

                saved = dict(
                    db.execute(
                        """
                        SELECT * FROM positions
                        WHERE symbol=?
                        """,
                        (symbol,)
                    ).fetchone()
                )

                return {
                    "status": "success",
                    "action": "BUY",
                    "position": saved,
                    "feeUsdt": buy_fee
                }

            pos = db.execute(
                "SELECT * FROM positions WHERE symbol=?",
                (symbol,)
            ).fetchone()

            if pos is None:
                raise ValueError("No open position")

            pos = dict(pos)
            price = b["bid"] * (1 - SLIP)
            proceeds = pos["qty"] * price
            sell_fee = proceeds * FEE

            gross = proceeds - pos["cost"]
            net = gross - pos["buy_fee"] - sell_fee

            db.execute(
                "UPDATE wallet SET cash=cash+? WHERE id=1",
                (proceeds - sell_fee,)
            )

            db.execute(
                "DELETE FROM positions WHERE symbol=?",
                (symbol,)
            )

            db.execute(
                """
                INSERT INTO history(
                    symbol,entry,exit,qty,buy_fee,
                    sell_fee,gross,net,opened,closed,reason
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    symbol,
                    pos["entry"],
                    price,
                    pos["qty"],
                    pos["buy_fee"],
                    sell_fee,
                    gross,
                    net,
                    pos["opened"],
                    now,
                    reason[:80]
                )
            )

            row = dict(
                db.execute(
                    """
                    SELECT * FROM history
                    ORDER BY id DESC LIMIT 1
                    """
                ).fetchone()
            )

            return {
                "status": "success",
                "action": "SELL",
                "closedTrade": row,
                "feeUsdt": sell_fee
            }

    def exit_decision(self, symbol):
        pos = self.position(symbol)

        if not pos:
            return {
                "exit": False,
                "reason": "NO_POSITION"
            }

        try:
            bid = self.fresh_book(symbol)["bid"]
        except ValueError:
            return {
                "exit": False,
                "reason": "STALE_DATA"
            }

        elapsed = time.time() - pos["opened"]
        peak = max(pos["peak"], bid)
        stop = pos["stop"]

        friction = 100 * (2 * FEE + 2 * SLIP)

        if (
            (peak / pos["entry"] - 1) * 100
            > friction + .15
        ):
            stop = max(stop, peak * (1 - .0015))

        if peak != pos["peak"] or stop != pos["stop"]:
            with self.db() as db:
                db.execute(
                    """
                    UPDATE positions
                    SET peak=?,stop=? WHERE symbol=?
                    """,
                    (peak, stop, symbol)
                )

        reason = (
            "STOP_OR_TRAIL"
            if bid <= stop
            else "TAKE_PROFIT"
            if bid >= pos["target"]
            else "TIMEOUT"
            if elapsed >= pos["hold"]
            else "HOLD"
        )

        estimate = (
            pos["qty"]
            * bid
            * (1 - SLIP)
            * (1 - FEE)
            - pos["cost"]
            - pos["buy_fee"]
        )

        return {
            "exit": reason != "HOLD",
            "reason": reason,
            "elapsedSec": int(elapsed),
            "estimatedNetPnlUsdt": round(estimate, 5),
            "stopPrice": stop,
            "targetPrice": pos["target"]
        }


engine = Engine(DB_FILE)


@asynccontextmanager
async def lifespan(app):
    yield


app = FastAPI(
    title="Rapid Guard V4 - Demo & Self Test",
    lifespan=lifespan
)


def authorize(key):
    if not ACCESS_KEY:
        raise HTTPException(
            503,
            "Set APP_ACCESS_KEY on the server before use"
        )

    if not key or not secrets.compare_digest(
        key, ACCESS_KEY
    ):
        raise HTTPException(
            401,
            "Invalid access key"
        )


def check_symbol(symbol):
    s = symbol.upper().strip().replace("/", "")

    if s not in SYMBOLS:
        raise HTTPException(
            400,
            "Unsupported candidate symbol"
        )

    return s


class BookIn(BaseModel):
    symbol: str
    bids: list[list]
    asks: list[list]


class TradeIn(BaseModel):
    symbol: str
    price: float = Field(gt=0)
    quantity: float = Field(gt=0)
    isBuyerMaker: bool


class TradesIn(BaseModel):
    trades: list[TradeIn] = Field(
        min_length=1,
        max_length=200
    )


class SymbolIn(BaseModel):
    symbol: str


class OrderIn(BaseModel):
    symbol: str
    action: str
    amountUsdt: float = 100
    signalToken: str | None = None
    useDemo: bool = True
    maxHoldSeconds: int = 180
    reason: str = "MANUAL"


@app.get("/api/config")
def config():
    return {
        "executionMode": "DEMO_ONLY",
        "liveEnabled": False,
        "symbols": SYMBOLS,
        "feeRatePerSide": FEE,
        "slippageRatePerSide": SLIP,
        "grossTargetPct": TARGET,
        "maxPositions": 3
    }


@app.get("/api/symbols")
async def symbols():
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.get(
                "https://api.binance.com/api/v3/exchangeInfo"
            )
            r.raise_for_status()

            valid = {
                x["symbol"]
                for x in r.json()["symbols"]
                if x.get("status") == "TRADING"
                and x.get("isSpotTradingAllowed")
            }

        return {
            "verified": True,
            "symbols": [
                s for s in SYMBOLS if s in valid
            ]
        }
    except Exception:
        raise HTTPException(
            503,
            "Could not verify Binance Spot symbols"
        )


@app.get("/api/state")
def get_state(x_access_key: str | None = Header(None)):
    authorize(x_access_key)
    return engine.snapshot()


@app.post("/api/market-data/book")
async def market_book(
    req: BookIn,
    x_access_key: str | None = Header(None)
):
    authorize(x_access_key)

    async with engine.lock:
        try:
            engine.book(
                check_symbol(req.symbol),
                req.bids,
                req.asks
            )
        except ValueError as e:
            raise HTTPException(422, str(e))

    return {"status": "ok"}


@app.post("/api/market-data/trades")
async def market_trades(
    req: TradesIn,
    x_access_key: str | None = Header(None)
):
    authorize(x_access_key)

    async with engine.lock:
        for t in req.trades:
            try:
                engine.trade(
                    check_symbol(t.symbol),
                    t.price,
                    t.quantity,
                    t.isBuyerMaker
                )
            except ValueError as e:
                raise HTTPException(422, str(e))

    return {
        "status": "ok",
        "count": len(req.trades)
    }


@app.post("/api/evaluate-entry")
async def evaluate(
    req: SymbolIn,
    x_access_key: str | None = Header(None)
):
    authorize(x_access_key)

    async with engine.lock:
        return engine.entry(
            check_symbol(req.symbol)
        )


@app.post("/api/execute-order")
async def order(
    req: OrderIn,
    x_access_key: str | None = Header(None)
):
    authorize(x_access_key)

    if not req.useDemo:
        raise HTTPException(
            403,
            "Live trading is disabled"
        )

    if req.action not in ("BUY", "SELL"):
        raise HTTPException(422, "Invalid action")

    if not 30 <= req.maxHoldSeconds <= 600:
        raise HTTPException(
            422,
            "Invalid hold seconds"
        )

    async with engine.lock:
        try:
            return engine.execute(
                check_symbol(req.symbol),
                req.action,
                req.amountUsdt,
                req.signalToken,
                req.maxHoldSeconds,
                req.reason
            )
        except ValueError as e:
            raise HTTPException(409, str(e))


@app.post("/api/position/evaluate-exit")
async def exit_eval(
    req: SymbolIn,
    x_access_key: str | None = Header(None)
):
    authorize(x_access_key)

    async with engine.lock:
        return engine.exit_decision(
            check_symbol(req.symbol)
        )


# ========================================
# LIVE CONNECTIVITY CHECKS (READ ONLY)
# ========================================

async def binance_connectivity():
    checks = []

    try:
        start = time.perf_counter()

        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                "https://api.binance.com/api/v3/time"
            )
            r.raise_for_status()

            assert isinstance(
                r.json()["serverTime"], int
            )

        elapsed = (
            time.perf_counter() - start
        ) * 1000

        checks.append({
            "name": "Binance REST",
            "status": "PASS",
            "detail": f"{elapsed:.0f} ms"
        })

    except Exception as e:
        checks.append({
            "name": "Binance REST",
            "status": "UNAVAILABLE",
            "detail": str(e)[:160]
        })

    try:
        import websockets

        async with asyncio.timeout(6):
            async with websockets.connect(
                "wss://stream.binance.com:9443/ws/btcusdt@trade",
                open_timeout=5
            ) as socket:
                packet = await socket.recv()

                assert (
                    '"e":"trade"' in packet
                    or '"e": "trade"' in packet
                )

        checks.append({
            "name": "Binance WebSocket",
            "status": "PASS",
            "detail": "Public trade event received"
        })

    except Exception as e:
        checks.append({
            "name": "Binance WebSocket",
            "status": "UNAVAILABLE",
            "detail": str(e)[:160]
        })

    return checks


# ========================================
# ISOLATED TEST MARKET
# ========================================

def seed(e):
    base = time.time() - 0.7

    for i in range(7):
        mid = 100 + .006 * i * i

        e.book(
            "BTCUSDT",
            [
                [mid - .01, 15],
                [mid - .02, 10]
            ],
            [
                [mid + .01, 2],
                [mid + .02, 2]
            ]
        )

        e.history["BTCUSDT"][-1]["time"] = (
            base + .1 * i
        )

    e.trade("BTCUSDT", 100.2, 20, False)
    e.trade("BTCUSDT", 100.2, 2, True)


def offline_checks():
    results = []

    def run(name, fn):
        try:
            detail = fn()

            results.append({
                "name": name,
                "status": "PASS",
                "detail": detail
            })

        except Exception as e:
            results.append({
                "name": name,
                "status": "FAIL",
                "detail": (
                    f"{type(e).__name__}: {e}"
                )
            })

    def with_engine(fn):
        with tempfile.TemporaryDirectory(
            prefix="scalper_selftest_"
        ) as folder:
            e = Engine(
                Path(folder) / "isolated.sqlite3"
            )
            return fn(e)

    def indicator(e):
        seed(e)
        f = e.features("BTCUSDT")

        assert f is not None
        assert f["buyRatio"] > .8
        assert f["imbalanceRatio"] > 1.5
        assert f["velocityPctPerSec"] > 0

        return (
            f"buyRatio={f['buyRatio']}, "
            f"imbalance={f['imbalanceRatio']}"
        )

    def signals(e):
        assert (
            e.entry("BTCUSDT")["enter"] is False
        )

        seed(e)
        sig = e.entry("BTCUSDT")

        assert sig["enter"]
        assert sig.get("signalToken")

        return (
            "Rejects missing data; issues valid "
            "signal on synthetic momentum"
        )

    def buy_sell(e):
        seed(e)

        token = e.entry(
            "BTCUSDT"
        )["signalToken"]

        buy = e.execute(
            "BTCUSDT",
            "BUY",
            100,
            token
        )

        assert buy["status"] == "success"
        assert len(e.snapshot()["positions"]) == 1

        try:
            e.execute(
                "BTCUSDT", "BUY", 100, token
            )
        except ValueError:
            pass
        else:
            raise AssertionError(
                "Token replay accepted"
            )

        sold = e.execute(
            "BTCUSDT",
            "SELL",
            reason="SELFTEST"
        )

        assert sold["status"] == "success"
        assert len(e.snapshot()["positions"]) == 0
        assert len(e.snapshot()["history"]) == 1

        try:
            e.execute("BTCUSDT", "SELL")
        except ValueError:
            pass
        else:
            raise AssertionError(
                "Double sell accepted"
            )

        return (
            "BUY, SELL, token replay and "
            "double-sell checks passed"
        )

    def fees(e):
        seed(e)

        token = e.entry(
            "BTCUSDT"
        )["signalToken"]

        e.execute(
            "BTCUSDT",
            "BUY",
            100,
            token
        )

        p = e.position("BTCUSDT")

        out = e.execute(
            "BTCUSDT", "SELL"
        )["closedTrade"]

        independent = (
            out["qty"] * out["exit"] * (1 - FEE)
            - p["cost"]
            - p["buy_fee"]
        )

        assert abs(
            independent - out["net"]
        ) < 1e-8

        assert abs(
            e.snapshot()["cashUsdt"]
            - (10000 + out["net"])
        ) < 1e-8

        return (
            f"Net={out['net']:.6f} USDT; "
            "wallet reconciled"
        )

    def recovery(e):
        seed(e)

        token = e.entry(
            "BTCUSDT"
        )["signalToken"]

        e.execute(
            "BTCUSDT",
            "BUY",
            100,
            token
        )

        restored = Engine(e.path)

        assert restored.position(
            "BTCUSDT"
        ) is not None

        assert abs(
            restored.snapshot()["cashUsdt"] - 9900
        ) < 1e-8

        return (
            "Position and cash restored "
            "from isolated SQLite file"
        )

    checks = [
        ("المؤشرات", indicator),
        ("الإشارات", signals),
        ("الشراء والبيع", buy_sell),
        ("الرسوم والرصيد", fees),
        ("الاستعادة", recovery)
    ]

    for name, fn in checks:
        run(
            name,
            lambda fn=fn: with_engine(fn)
        )

    return results


@app.post("/api/self-test")
async def self_test(
    x_access_key: str | None = Header(None)
):
    authorize(x_access_key)

    # No production engine/database mutations.
    offline = await asyncio.to_thread(
        offline_checks
    )

    network = await binance_connectivity()
    all_results = network + offline

    return {
        "isolated": True,
        "liveOrdersSent": 0,
        "productionDbTouched": False,
        "results": all_results,
        "passed": sum(
            x["status"] == "PASS"
            for x in all_results
        ),
        "failed": sum(
            x["status"] == "FAIL"
            for x in all_results
        ),
        "unavailable": sum(
            x["status"] == "UNAVAILABLE"
            for x in all_results
        )
    }


@app.get("/frontend.js")
def script():
    return FileResponse(
        ROOT / "frontend.js",
        media_type="text/javascript"
    )


@app.get("/")
def home():
    return FileResponse(
        ROOT / "index.html"
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000"))
    )
