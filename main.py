"""Rapid Guard Scalper: server-authoritative, demo-first Binance Spot monitor.

LIVE order submission is intentionally NOT implemented in this release. This avoids
pretending that local persistence alone represents actual Binance executions.
"""
from __future__ import annotations

import asyncio
import math
import os
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
import httpx
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DATABASE_PATH", str(BASE / "scalper.sqlite3")))
DEMO_START_BALANCE = float(os.getenv("DEMO_START_BALANCE", "10000"))
# These are CONFIGURABLE estimates, not a claim of the actual user's Binance commission.
FEE_RATE = float(os.getenv("DEMO_FEE_RATE", "0.001"))  # 0.10% per side
SLIPPAGE_RATE = float(os.getenv("DEMO_SLIPPAGE_RATE", "0.0002"))  # 0.02% per side
MIN_NET_TARGET_PCT = float(os.getenv("MIN_NET_TARGET_PCT", "0.10"))
TARGET_GROSS_PCT = float(os.getenv("TARGET_GROSS_PCT", "0.45"))
STOP_GROSS_PCT = float(os.getenv("STOP_GROSS_PCT", "0.30"))
MAX_SPREAD_PCT = float(os.getenv("MAX_SPREAD_PCT", "0.05"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS_USDT", "25"))
MIN_BUY_RATIO = float(os.getenv("MIN_BUY_RATIO", "0.60"))
MIN_IMBALANCE = float(os.getenv("MIN_IMBALANCE", "1.5"))
MIN_SAMPLES = int(os.getenv("MIN_BOOK_SAMPLES", "6"))
MAX_HOLD = int(os.getenv("DEFAULT_MAX_HOLD_SECONDS", "180"))
TOKEN_LIFETIME = 3.0
MARKET_MAX_AGE = 2.5
TRADE_MAX_AGE = 5.0
SUPPORTED_CANDIDATES = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT",
    "ADAUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT", "DOTUSDT", "TRXUSDT",
    "SUIUSDT", "NEARUSDT", "APTUSDT", "ATOMUSDT", "UNIUSDT", "ETCUSDT",
    "BCHUSDT", "XLMUSDT", "FILUSDT", "ARBUSDT", "OPUSDT", "AAVEUSDT",
    "INJUSDT", "PEPEUSDT", "SHIBUSDT", "ICPUSDT", "ALGOUSDT", "HBARUSDT",
]

if not 0 <= FEE_RATE < 0.02 or not 0 <= SLIPPAGE_RATE < 0.02:
    raise RuntimeError("Invalid demo fee/slippage environment configuration")

books: dict[str, dict] = {}
book_hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=40))
trades: dict[str, deque] = defaultdict(lambda: deque(maxlen=500))
tokens: dict[str, dict] = {}
verified_symbols: set[str] = set()
symbols_cache_time = 0.0
state_lock = asyncio.Lock()


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value REAL NOT NULL)""")
        con.execute("INSERT OR IGNORE INTO meta(name,value) VALUES ('cash',?)", (DEMO_START_BALANCE,))
        con.execute("""CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY, entry_price REAL NOT NULL, quantity REAL NOT NULL,
            entry_cost REAL NOT NULL, entry_fee REAL NOT NULL, entry_time REAL NOT NULL,
            peak REAL NOT NULL, stop_price REAL NOT NULL, target_price REAL NOT NULL,
            max_duration INTEGER NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS closed_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
            entry_price REAL NOT NULL, exit_price REAL NOT NULL, quantity REAL NOT NULL,
            gross_pnl REAL NOT NULL, entry_fee REAL NOT NULL, exit_fee REAL NOT NULL,
            net_pnl REAL NOT NULL, opened_at REAL NOT NULL, closed_at REAL NOT NULL,
            reason TEXT NOT NULL
        )""")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_db()
    yield


app = FastAPI(title="Rapid Guard: Demo-first Micro Momentum", version="3.0.0", lifespan=lifespan)


class BookRequest(BaseModel):
    symbol: str
    bids: list[list]
    asks: list[list]
    timestamp: Optional[int] = None


class TradeRequest(BaseModel):
    symbol: str
    price: float = Field(gt=0)
    quantity: float = Field(gt=0)
    isBuyerMaker: bool
    timestamp: Optional[int] = None


class TradeBatch(BaseModel):
    trades: list[TradeRequest] = Field(min_length=1, max_length=200)


class SymbolRequest(BaseModel):
    symbol: str


class OrderRequest(BaseModel):
    symbol: str
    action: Literal["BUY", "SELL"]
    amountUsdt: float = Field(default=100, ge=10, le=100000)
    signalToken: Optional[str] = None
    useDemo: bool = True
    maxHoldSeconds: int = Field(default=MAX_HOLD, ge=30, le=600)
    reason: Optional[str] = None


class ExitRequest(BaseModel):
    symbol: str


def normalized(symbol: str) -> str:
    symbol = symbol.upper().strip().replace("/", "")
    if symbol not in SUPPORTED_CANDIDATES:
        raise HTTPException(400, "الرمز خارج نطاق العملات الرقمية المسموحة")
    if verified_symbols and symbol not in verified_symbols:
        raise HTTPException(400, "الزوج غير نشط أو غير مدعوم في Binance Spot")
    return symbol


def get_position(con, symbol):
    row = con.execute("SELECT * FROM positions WHERE symbol=?", (symbol,)).fetchone()
    return dict(row) if row else None


def state():
    with connect() as con:
        cash = float(con.execute("SELECT value FROM meta WHERE name='cash'").fetchone()[0])
        positions = [dict(r) for r in con.execute("SELECT * FROM positions ORDER BY entry_time DESC")]
        history = [dict(r) for r in con.execute("SELECT * FROM closed_trades ORDER BY id DESC LIMIT 150")]
    value = cash + sum(p["quantity"] * books.get(p["symbol"], {}).get("bid", p["entry_price"]) for p in positions)
    return {"cashUsdt": cash, "equityUsdt": value, "positions": positions, "history": history}


def day_loss(con, now):
    # Rolling UTC calendar day, net realised PnL. Block only when net loss threshold is reached.
    day = int(now // 86400) * 86400
    total = con.execute("SELECT COALESCE(SUM(net_pnl),0) FROM closed_trades WHERE closed_at>=?", (day,)).fetchone()[0]
    return max(0.0, -float(total))


def features(symbol):
    now = time.time()
    b = books.get(symbol)
    hist = [x for x in book_hist[symbol] if now - x["t"] <= 8]
    recent = [t for t in trades[symbol] if now - t["t"] < 5]
    if not b or now - b["t"] > MARKET_MAX_AGE or len(hist) < MIN_SAMPLES:
        return None, ["دفتر الطلبات قديم أو العينات غير كافية"]
    if not recent or now - max(t["t"] for t in recent) > TRADE_MAX_AGE:
        return None, ["لا توجد صفقات منفذة حديثة"]
    buy = sum(t["qty"] * t["price"] for t in recent if not t["isBuyerMaker"])
    sell = sum(t["qty"] * t["price"] for t in recent if t["isBuyerMaker"])
    total = buy + sell
    if total <= 0:
        return None, ["حجم التداول المنفذ غير كافٍ"]
    buy_ratio = buy / total
    spread_pct = (b["ask"] - b["bid"]) / b["ask"] * 100
    window = hist[-min(15, len(hist)):]
    velocity = (window[-1]["mid"] / window[0]["mid"] - 1) * 100 if window[0]["mid"] else 0
    durations = max(window[-1]["t"] - window[0]["t"], 0.01)
    velocity_per_sec = velocity / durations
    halfway = len(window) // 2
    first = window[0]
    middle = window[halfway]
    last = window[-1]
    v1 = (middle["mid"] / first["mid"] - 1) / max(middle["t"] - first["t"], 0.01)
    v2 = (last["mid"] / middle["mid"] - 1) / max(last["t"] - middle["t"], 0.01)
    acceleration = (v2 - v1) * 100
    sustained = sum(h["imbalance"] >= MIN_IMBALANCE for h in window) / len(window)
    # The ratio below is order-book depth imbalance, NOT formal event-level OFI.
    out = {
        "imbalanceRatio": round(b["imbalance"], 3), "sustainedImbalance": round(sustained, 3),
        "buyRatio": round(buy_ratio, 3), "spreadPct": round(spread_pct, 4),
        "velocityPctPerSec": round(velocity_per_sec, 5),
        "accelerationPctPerSec2": round(acceleration, 5),
        "tradeNotional5s": round(total, 2), "bestBid": b["bid"], "bestAsk": b["ask"],
    }
    return out, []


def cost_metrics(f):
    spread = f["spreadPct"]
    fees = (2 * FEE_RATE) * 100
    slippage = (2 * SLIPPAGE_RATE) * 100
    total_cost_pct = fees + slippage + spread
    return {
        "roundTripFeePctEstimate": round(fees, 4),
        "roundTripSlippagePctEstimate": round(slippage, 4),
        "spreadPct": round(spread, 4),
        "estimatedTotalFrictionPct": round(total_cost_pct, 4),
        "estimatedNetAtTargetPct": round(TARGET_GROSS_PCT - total_cost_pct, 4),
    }


@app.get("/api/config")
def config():
    return {"liveEnabled": False, "executionMode": "DEMO_ONLY",
            "feeRatePerSide": FEE_RATE, "slippageRatePerSide": SLIPPAGE_RATE,
            "grossTargetPct": TARGET_GROSS_PCT, "minNetTargetPct": MIN_NET_TARGET_PCT,
            "maxPositions": MAX_POSITIONS, "maxDailyLossUsdt": MAX_DAILY_LOSS,
            "symbols": SUPPORTED_CANDIDATES,
            "note": "قائمة مرشحة وليست توثيقاً لحالة كل زوج؛ يجب التحقق من Binance exchangeInfo قبل الاشتراك."}


@app.get("/api/symbols")
async def available_symbols():
    """Fail closed if Binance exchangeInfo cannot be verified."""
    global symbols_cache_time, verified_symbols
    if verified_symbols and time.time() - symbols_cache_time < 1800:
        return {"symbols": sorted(verified_symbols), "verified": True}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get("https://api.binance.com/api/v3/exchangeInfo")
            res.raise_for_status()
            info = res.json()
        eligible = {x["symbol"] for x in info.get("symbols", [])
                    if x.get("status") == "TRADING" and x.get("isSpotTradingAllowed", False)
                    and x.get("symbol") in SUPPORTED_CANDIDATES}
        if not eligible:
            raise ValueError("no eligible spot pairs")
        verified_symbols = eligible
        symbols_cache_time = time.time()
        return {"symbols": sorted(eligible), "verified": True}
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(503, "تعذر التحقق من أزواج Binance Spot؛ لن يبدأ مسح الأصول") from exc


@app.post("/api/market-data/book")
async def receive_book(req: BookRequest):
    symbol = normalized(req.symbol)
    try:
        bids = sorted([(float(x[0]), float(x[1])) for x in req.bids[:5]], reverse=True)
        asks = sorted([(float(x[0]), float(x[1])) for x in req.asks[:5]])
    except (ValueError, TypeError, IndexError) as exc:
        raise HTTPException(422, "بيانات دفتر الطلبات غير صالحة") from exc
    if not bids or not asks or any(not math.isfinite(x) or x <= 0 for pair in bids + asks for x in pair):
        raise HTTPException(422, "أسعار أو كميات غير صالحة")
    if bids[0][0] >= asks[0][0]:
        raise HTTPException(422, "دفتر الطلبات متقاطع أو غير صالح")
    bv = sum(p*q for p, q in bids)
    av = sum(p*q for p, q in asks)
    ratio = min(bv / av, 100) if av > 0 else 0
    now = time.time()
    mid = (bids[0][0] + asks[0][0]) / 2
    books[symbol] = {"bid": bids[0][0], "ask": asks[0][0], "imbalance": ratio, "t": now}
    book_hist[symbol].append({"t": now, "mid": mid, "imbalance": ratio})
    return {"status": "ok"}


@app.post("/api/market-data/trade")
async def receive_trade(req: TradeRequest):
    symbol = normalized(req.symbol)
    if not math.isfinite(req.price) or not math.isfinite(req.quantity):
        raise HTTPException(422, "قيم غير صالحة")
    trades[symbol].append({"price": req.price, "qty": req.quantity,
                           "isBuyerMaker": req.isBuyerMaker, "t": time.time()})
    return {"status": "ok"}


@app.post("/api/market-data/trades")
async def receive_trades(req: TradeBatch):
    now = time.time()
    for t in req.trades:
        symbol = normalized(t.symbol)
        if not math.isfinite(t.price) or not math.isfinite(t.quantity):
            raise HTTPException(422, "قيم صفقات غير صالحة")
        trades[symbol].append({"price": t.price, "qty": t.quantity,
                               "isBuyerMaker": t.isBuyerMaker, "t": now})
    return {"status": "ok", "count": len(req.trades)}


@app.post("/api/evaluate-entry")
async def evaluate_entry(req: SymbolRequest):
    symbol = normalized(req.symbol)
    f, reasons = features(symbol)
    if f is None:
        return {"enter": False, "score": 0, "reasons": reasons}
    m = cost_metrics(f)
    checks = [
        (f["spreadPct"] <= MAX_SPREAD_PCT, "السبريد مرتفع"),
        (f["imbalanceRatio"] >= MIN_IMBALANCE, "عدم توازن دفتر الطلبات ضعيف"),
        (f["sustainedImbalance"] >= 0.7, "السيولة غير مستقرة"),
        (f["buyRatio"] >= MIN_BUY_RATIO, "ضغط الشراء المنفذ غير كافٍ"),
        (f["velocityPctPerSec"] > 0, "السعر لا يرتفع فعلياً"),
        (f["accelerationPctPerSec2"] >= -0.002, "الزخم يتباطأ بسرعة"),
        (m["estimatedNetAtTargetPct"] >= MIN_NET_TARGET_PCT, "الهدف لا يغطي تكاليف التنفيذ بهامش كافٍ"),
    ]
    reasons = [reason for passed, reason in checks if not passed]
    async with state_lock:
        with connect() as con:
            if get_position(con, symbol):
                reasons.append("المركز مفتوح بالفعل")
            if con.execute("SELECT COUNT(*) FROM positions").fetchone()[0] >= MAX_POSITIONS:
                reasons.append("تم بلوغ الحد الأقصى للمراكز")
            if day_loss(con, time.time()) >= MAX_DAILY_LOSS:
                reasons.append("تم بلوغ حد الخسارة اليومي")
    score = round(sum(ok for ok, _ in checks) / len(checks) * 100)
    response = {"enter": not reasons, "score": score, "reasons": reasons or ["استوفت الإشارة شروط الفلترة"],
                "features": f, "costEstimate": m}
    if not reasons:
        token = secrets.token_urlsafe(24)
        tokens[token] = {"symbol": symbol, "expiry": time.time() + TOKEN_LIFETIME,
                         "ask": f["bestAsk"]}
        response["signalToken"] = token
    if len(tokens) > 3000:
        now = time.time()
        for tok, val in list(tokens.items()):
            if val["expiry"] < now:
                tokens.pop(tok, None)
    return response


def current_book(symbol):
    b = books.get(symbol)
    if not b or time.time() - b["t"] > MARKET_MAX_AGE:
        raise HTTPException(409, "بيانات السوق غير حديثة؛ لا يمكن التنفيذ")
    return b


@app.post("/api/execute-order")
async def execute_order(req: OrderRequest):
    if not req.useDemo:
        raise HTTPException(403, "التداول الحقيقي غير مفعّل في هذه النسخة؛ لا توجد أوامر Live")
    symbol = normalized(req.symbol)
    async with state_lock:
        now = time.time()
        with connect() as con:
            if req.action == "BUY":
                token = tokens.pop(req.signalToken, None) if req.signalToken else None
                if not token or token["symbol"] != symbol or now > token["expiry"]:
                    raise HTTPException(400, "رمز الإشارة غير صالح أو منتهي الصلاحية")
                b = current_book(symbol)
                if abs(b["ask"] / token["ask"] - 1) > 0.001:
                    raise HTTPException(409, "السعر تغير كثيراً بعد إصدار الإشارة")
                # Recheck affordability/market data before executing (avoid stale tokens).
                f, _ = features(symbol)
                if not f or f["buyRatio"] < MIN_BUY_RATIO or f["spreadPct"] > MAX_SPREAD_PCT:
                    raise HTTPException(409, "الإشارة لم تعد مستوفية للشروط")
                if get_position(con, symbol):
                    raise HTTPException(409, "هناك مركز مفتوح بالفعل")
                if con.execute("SELECT COUNT(*) FROM positions").fetchone()[0] >= MAX_POSITIONS:
                    raise HTTPException(409, "تجاوز الحد الأقصى للمراكز")
                if day_loss(con, now) >= MAX_DAILY_LOSS:
                    raise HTTPException(409, "حد الخسارة اليومي")
                cash = float(con.execute("SELECT value FROM meta WHERE name='cash'").fetchone()[0])
                if cash + 1e-7 < req.amountUsdt:
                    raise HTTPException(409, "رصيد تجريبي غير كافٍ")
                # Budget includes entry fee. Demo models adverse ask slippage.
                fill = b["ask"] * (1 + SLIPPAGE_RATE)
                order_notional = req.amountUsdt / (1 + FEE_RATE)
                entry_fee = order_notional * FEE_RATE
                qty = order_notional / fill
                stop = fill * (1 - STOP_GROSS_PCT / 100)
                target = fill * (1 + TARGET_GROSS_PCT / 100)
                con.execute("UPDATE meta SET value=value-? WHERE name='cash'", (req.amountUsdt,))
                con.execute("""INSERT INTO positions(symbol,entry_price,quantity,entry_cost,entry_fee,entry_time,peak,stop_price,target_price,max_duration)
                               VALUES (?,?,?,?,?,?,?,?,?,?)""",
                            (symbol, fill, qty, order_notional, entry_fee, now, fill, stop, target, req.maxHoldSeconds))
                position = get_position(con, symbol)
                return {"status": "success", "mode": "DEMO", "action": "BUY", "position": position,
                        "fillPrice": fill, "feeUsdt": entry_fee}
            else:
                pos = get_position(con, symbol)
                if not pos:
                    raise HTTPException(404, "لا يوجد مركز مفتوح")
                b = current_book(symbol)
                fill = b["bid"] * (1 - SLIPPAGE_RATE)
                proceeds = pos["quantity"] * fill
                exit_fee = proceeds * FEE_RATE
                net_cash = proceeds - exit_fee
                gross_pnl = proceeds - pos["entry_cost"]
                net_pnl = net_cash - pos["entry_cost"] - pos["entry_fee"]
                con.execute("UPDATE meta SET value=value+? WHERE name='cash'", (net_cash,))
                con.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
                con.execute("""INSERT INTO closed_trades(symbol,entry_price,exit_price,quantity,gross_pnl,entry_fee,exit_fee,net_pnl,opened_at,closed_at,reason)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                            (symbol, pos["entry_price"], fill, pos["quantity"], gross_pnl,
                             pos["entry_fee"], exit_fee, net_pnl, pos["entry_time"], now,
                             (req.reason or "MANUAL")[:80]))
                row = dict(con.execute("SELECT * FROM closed_trades ORDER BY id DESC LIMIT 1").fetchone())
                return {"status": "success", "mode": "DEMO", "action": "SELL", "closedTrade": row,
                        "fillPrice": fill, "feeUsdt": exit_fee}


@app.post("/api/position/evaluate-exit")
async def evaluate_exit(req: ExitRequest):
    symbol = normalized(req.symbol)
    async with state_lock:
        with connect() as con:
            pos = get_position(con, symbol)
            if not pos:
                return {"exit": False, "reason": "NO_POSITION"}
            elapsed = time.time() - pos["entry_time"]
            try:
                bid = current_book(symbol)["bid"]
            except HTTPException:
                # Never fabricate a price or delete a position on feed failure.
                return {"exit": False, "reason": "STALE_DATA", "elapsedSec": int(elapsed),
                        "warning": "انقطع تدفق الأسعار؛ يحتاج المركز متابعة عاجلة"}
            if bid > pos["peak"]:
                pos["peak"] = bid
                con.execute("UPDATE positions SET peak=? WHERE symbol=?", (bid, symbol))
            # Conservative trailing stop only after the unrealised gross gain covers estimated friction.
            friction_pct = 2 * (FEE_RATE + SLIPPAGE_RATE) * 100
            if (pos["peak"] / pos["entry_price"] - 1) * 100 > friction_pct + 0.15:
                trailing = pos["peak"] * (1 - 0.0015)
                protected = max(pos["stop_price"], trailing)
                if protected > pos["stop_price"]:
                    pos["stop_price"] = protected
                    con.execute("UPDATE positions SET stop_price=? WHERE symbol=?", (protected, symbol))
            reason = None
            if bid <= pos["stop_price"]:
                reason = "STOP_OR_TRAIL"
            elif bid >= pos["target_price"]:
                reason = "TAKE_PROFIT"
            elif elapsed >= pos["max_duration"]:
                reason = "TIMEOUT"
            # Gross mark-to-market and net liquidation estimate are distinct.
            gross_pct = (bid / pos["entry_price"] - 1) * 100
            liquidation = pos["quantity"] * bid * (1 - SLIPPAGE_RATE) * (1 - FEE_RATE)
            net_usdt = liquidation - pos["entry_cost"] - pos["entry_fee"]
            return {"exit": bool(reason), "reason": reason or "HOLD", "elapsedSec": int(elapsed),
                    "grossPnlPct": round(gross_pct, 4), "estimatedNetPnlUsdt": round(net_usdt, 5),
                    "stopPrice": pos["stop_price"], "targetPrice": pos["target_price"], "peakPrice": pos["peak"]}


@app.get("/api/state")
def get_state():
    return state()


@app.get("/api/account-balance")
def account_balance(demo: bool = True):
    if not demo:
        raise HTTPException(403, "لا يدعم هذا الإصدار رصيد الحساب الحقيقي")
    s = state()
    return {"status": "success", "totalUsdtBalance": s["cashUsdt"], "equityUsdt": s["equityUsdt"]}


@app.get("/api/diagnostics")
def diagnostics():
    s = state()
    history = s["history"]
    wins = sum(x["net_pnl"] > 0 for x in history)
    return {"status": "healthy", "engine": "Micro Momentum Demo V3", "liveTradingAllowed": False,
            "activePositionsCount": len(s["positions"]), "recentClosedTrades": len(history),
            "recentWinRatePct": round(wins / len(history) * 100, 2) if history else None,
            "estimatedFeesPerSidePct": FEE_RATE * 100,
            "warning": "التحليل إحصائي وليس نموذج AI، والنتائج تجريبية فقط"}


@app.get("/frontend.js")
def frontend_script():
    return FileResponse(BASE / "frontend.js", media_type="text/javascript")


@app.get("/")
def home():
    page = BASE / "index.html"
    if not page.exists():
        raise HTTPException(404, "index.html missing")
    return FileResponse(page)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
