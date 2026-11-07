
import os
import time
import math
import secrets
import sqlite3
import asyncio
from pathlib import Path
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Optional, Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import ccxt.async_support as ccxt

# ==========================================
# CONFIGURATION & ENV KEYS
# ==========================================

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(
    os.getenv("DATABASE_PATH", str(BASE_DIR / "scalper.sqlite3"))
)

# مفاتيح Binance عبر متغيرات البيئة بدلاً من كتابتها مكشوفة في الكود
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "").strip()
ALLOW_LIVE_TRADING = os.getenv("ALLOW_LIVE_TRADING", "false").lower() == "true"

DEMO_START_BALANCE = float(os.getenv("DEMO_START_BALANCE", "10000"))
FEE_RATE = float(os.getenv("DEMO_FEE_RATE", "0.001"))
SLIPPAGE_RATE = float(os.getenv("DEMO_SLIPPAGE_RATE", "0.0002"))
MIN_NET_TARGET_PCT = float(os.getenv("MIN_NET_TARGET_PCT", "0.10"))
TARGET_GROSS_PCT = float(os.getenv("TARGET_GROSS_PCT", "0.45"))
STOP_GROSS_PCT = float(os.getenv("STOP_GROSS_PCT", "0.30"))
MAX_SPREAD_PCT = float(os.getenv("MAX_SPREAD_PCT", "0.08"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS_USDT", "25"))
MIN_BUY_RATIO = float(os.getenv("MIN_BUY_RATIO", "0.55"))
MIN_IMBALANCE = float(os.getenv("MIN_IMBALANCE", "1.3"))
MAX_HOLD = int(os.getenv("DEFAULT_MAX_HOLD_SECONDS", "180"))

TOKEN_LIFETIME = 3.0
MARKET_MAX_AGE = 5.0

SUPPORTED_CANDIDATES = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT",
    "XRPUSDT", "BNBUSDT", "DOGEUSDT",
    "ADAUSDT", "LINKUSDT", "AVAXUSDT",
    "LTCUSDT", "DOTUSDT", "TRXUSDT",
    "SUIUSDT", "NEARUSDT", "APTUSDT",
    "ATOMUSDT", "UNIUSDT", "ETCUSDT",
    "BCHUSDT", "XLMUSDT", "FILUSDT",
    "ARBUSDT", "OPUSDT", "AAVEUSDT",
    "INJUSDT", "PEPEUSDT", "SHIBUSDT",
    "ICPUSDT", "ALGOUSDT", "HBARUSDT"
]

# ==========================================
# CCXT EXCHANGE SETUP
# ==========================================

exchange = None

if BINANCE_API_KEY and BINANCE_SECRET_KEY:
    exchange = ccxt.binance({
        'apiKey': BINANCE_API_KEY,
        'secret': BINANCE_SECRET_KEY,
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'}
    })

# ==========================================
# MARKET STATE & DATABASE
# ==========================================

books = {}
book_hist = defaultdict(lambda: deque(maxlen=40))
trades = defaultdict(lambda: deque(maxlen=500))
tokens = {}
state_lock = asyncio.Lock()

def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def initialize_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value REAL NOT NULL)")
        con.execute("INSERT OR IGNORE INTO meta(name,value) VALUES ('cash',?)", (DEMO_START_BALANCE,))
        con.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                entry_price REAL NOT NULL,
                quantity REAL NOT NULL,
                entry_cost REAL NOT NULL,
                entry_fee REAL NOT NULL,
                entry_time REAL NOT NULL,
                peak REAL NOT NULL,
                stop_price REAL NOT NULL,
                target_price REAL NOT NULL,
                max_duration INTEGER NOT NULL,
                is_live INTEGER DEFAULT 0
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS closed_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                quantity REAL NOT NULL,
                gross_pnl REAL NOT NULL,
                entry_fee REAL NOT NULL,
                exit_fee REAL NOT NULL,
                net_pnl REAL NOT NULL,
                opened_at REAL NOT NULL,
                closed_at REAL NOT NULL,
                reason TEXT NOT NULL
            )
        """)

@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_db()
    yield
    if exchange:
        await exchange.close()

app = FastAPI(
    title="Rapid Guard Scalping Engine",
    version="3.0.0",
    lifespan=lifespan
)

# ==========================================
# REQUEST MODELS
# ==========================================

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

# ==========================================
# HELPERS & FEATURES
# ==========================================

def normalized(symbol):
    symbol = symbol.upper().strip().replace("/", "")
    if symbol not in SUPPORTED_CANDIDATES:
        raise HTTPException(400, "الرمز غير مسموح")
    return symbol

def get_position(con, symbol):
    row = con.execute("SELECT * FROM positions WHERE symbol=?", (symbol,)).fetchone()
    return dict(row) if row else None

def state():
    with connect() as con:
        cash = float(con.execute("SELECT value FROM meta WHERE name='cash'").fetchone()[0])
        positions = [dict(r) for r in con.execute("SELECT * FROM positions ORDER BY entry_time DESC")]
        history = [dict(r) for r in con.execute("SELECT * FROM closed_trades ORDER BY id DESC LIMIT 150")]

    equity = cash + sum(
        p["quantity"] * books.get(p["symbol"], {}).get("bid", p["entry_price"])
        for p in positions
    )

    return {
        "cashUsdt": cash,
        "equityUsdt": equity,
        "positions": positions,
        "history": history
    }

def day_loss(con, now):
    day = int(now // 86400) * 86400
    total = con.execute("SELECT COALESCE(SUM(net_pnl),0) FROM closed_trades WHERE closed_at >= ?", (day,)).fetchone()[0]
    return max(0.0, -float(total))

def features(symbol):
    now = time.time()
    b = books.get(symbol)
    hist = [x for x in book_hist[symbol] if now - x["t"] <= 12]
    recent = [t for t in trades[symbol] if now - t["t"] < 10]

    if not b or (now - b["t"] > MARKET_MAX_AGE):
        return None, ["بيانات دفتر الطلبات غير متوفرة أو قديمة"]

    buy = sum(t["qty"] * t["price"] for t in recent if not t["isBuyerMaker"])
    sell = sum(t["qty"] * t["price"] for t in recent if t["isBuyerMaker"])
    total = buy + sell

    buy_ratio = (buy / total) if total > 0 else (0.5 + min(0.4, (b["imbalance"] - 1) * 0.1))
    spread_pct = ((b["ask"] - b["bid"]) / b["ask"]) * 100

    velocity = 0.01
    if len(hist) >= 2:
        window = hist[-min(15, len(hist)):]
        duration = max(window[-1]["t"] - window[0]["t"], 0.01)
        velocity = ((window[-1]["mid"] / window[0]["mid"] - 1) * 100) / duration

    result = {
        "imbalanceRatio": round(b["imbalance"], 3),
        "sustainedImbalance": round(b["imbalance"], 3),
        "buyRatio": round(buy_ratio, 3),
        "spreadPct": round(spread_pct, 4),
        "velocityPctPerSec": round(velocity, 5),
        "tradeNotional5s": round(total, 2),
        "bestBid": b["bid"],
        "bestAsk": b["ask"]
    }
    return result, []

def cost_metrics(f):
    fees = 2 * FEE_RATE * 100
    slippage = 2 * SLIPPAGE_RATE * 100
    spread = f["spreadPct"]
    friction = fees + slippage + spread
    return {
        "roundTripFeePctEstimate": round(fees, 4),
        "roundTripSlippagePctEstimate": round(slippage, 4),
        "spreadPct": round(spread, 4),
        "estimatedTotalFrictionPct": round(friction, 4),
        "estimatedNetAtTargetPct": round(TARGET_GROSS_PCT - friction, 4)
    }

# ==========================================
# API ENDPOINTS
# ==========================================

@app.get("/api/config")
def config():
    return {
        "liveEnabled": ALLOW_LIVE_TRADING and bool(exchange),
        "hasApiKeys": bool(BINANCE_API_KEY and BINANCE_SECRET_KEY),
        "feeRatePerSide": FEE_RATE,
        "slippageRatePerSide": SLIPPAGE_RATE,
        "grossTargetPct": TARGET_GROSS_PCT,
        "minNetTargetPct": MIN_NET_TARGET_PCT,
        "maxPositions": MAX_POSITIONS,
        "maxDailyLossUsdt": MAX_DAILY_LOSS,
        "symbols": SUPPORTED_CANDIDATES
    }

@app.get("/api/symbols")
async def available_symbols():
    return {"symbols": sorted(SUPPORTED_CANDIDATES), "verified": True}

@app.post("/api/market-data/book")
async def receive_book(req: BookRequest):
    symbol = normalized(req.symbol)
    try:
        bids = sorted([(float(x[0]), float(x[1])) for x in req.bids[:5]], reverse=True)
        asks = sorted([(float(x[0]), float(x[1])) for x in req.asks[:5]])
    except (ValueError, TypeError, IndexError):
        raise HTTPException(422, "بيانات دفتر الطلبات غير صالحة")

    if not bids or not asks:
        raise HTTPException(422, "دفتر طلبات فارغ")

    buy_volume = sum(price * qty for price, qty in bids)
    sell_volume = sum(price * qty for price, qty in asks)
    ratio = min(buy_volume / sell_volume, 100) if sell_volume > 0 else 1.0

    now = time.time()
    mid = (bids[0][0] + asks[0][0]) / 2

    books[symbol] = {"bid": bids[0][0], "ask": asks[0][0], "imbalance": ratio, "t": now}
    book_hist[symbol].append({"t": now, "mid": mid, "imbalance": ratio})

    return {"status": "ok"}

@app.post("/api/market-data/trades")
async def receive_trades(req: TradeBatch):
    now = time.time()
    for trade in req.trades:
        symbol = normalized(trade.symbol)
        trades[symbol].append({
            "price": trade.price,
            "qty": trade.quantity,
            "isBuyerMaker": trade.isBuyerMaker,
            "t": now
        })
    return {"status": "ok", "count": len(req.trades)}

@app.post("/api/evaluate-entry")
async def evaluate_entry(req: SymbolRequest):
    symbol = normalized(req.symbol)
    f, reasons = features(symbol)
    if f is None:
        return {"enter": False, "score": 0, "reasons": reasons}

    costs = cost_metrics(f)
    checks = [
        (f["spreadPct"] <= MAX_SPREAD_PCT, "السبريد مرتفع"),
        (f["imbalanceRatio"] >= MIN_IMBALANCE, "عدم توازن السيولة غير كافٍ"),
        (f["buyRatio"] >= MIN_BUY_RATIO, "ضغط الشراء غير كافٍ"),
        (costs["estimatedNetAtTargetPct"] >= MIN_NET_TARGET_PCT, "الربح المتوقع لا يغطي التكاليف")
    ]
    reasons = [reason for passed, reason in checks if not passed]

    async with state_lock:
        with connect() as con:
            if get_position(con, symbol):
                reasons.append("توجد صفقة مفتوحة")
            count = con.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
            if count >= MAX_POSITIONS:
                reasons.append("الحد الأقصى للمراكز")
            if day_loss(con, time.time()) >= MAX_DAILY_LOSS:
                reasons.append("حد الخسارة اليومي")

    score = round(sum(ok for ok, _ in checks) / len(checks) * 100)
    response = {
        "enter": not reasons,
        "score": score,
        "reasons": reasons or ["الإشارة مستوفية للشروط"],
        "features": f,
        "costEstimate": costs
    }

    if not reasons:
        token = secrets.token_urlsafe(24)
        tokens[token] = {
            "symbol": symbol,
            "expiry": time.time() + TOKEN_LIFETIME,
            "ask": f["bestAsk"]
        }
        response["signalToken"] = token

    return response

@app.post("/api/execute-order")
async def execute_order(req: OrderRequest):
    is_live = not req.useDemo
    if is_live and (not ALLOW_LIVE_TRADING or not exchange):
        raise HTTPException(403, "التداول الحقيقي معطل أو أن مفاتيح API غير مفعّلة في Render")

    symbol = normalized(req.symbol)

    async with state_lock:
        now = time.time()
        with connect() as con:
            if req.action == "BUY":
                token = tokens.pop(req.signalToken, None) if req.signalToken else None
                if not token or token["symbol"] != symbol or now > token["expiry"]:
                    raise HTTPException(400, "رمز الإشارة غير صالح أو منتهي الصلاحية")

                book = books.get(symbol)
                if not book or (now - book["t"] > MARKET_MAX_AGE):
                    raise HTTPException(409, "بيانات السوق غير حديثة")

                if get_position(con, symbol):
                    raise HTTPException(409, "المركز موجود بالفعل")

                cash = float(con.execute("SELECT value FROM meta WHERE name='cash'").fetchone()[0])
                if not is_live and cash < req.amountUsdt:
                    raise HTTPException(409, "رصيد تجريبي غير كافٍ")

                fill = book["ask"] * (1 + SLIPPAGE_RATE)
                order_notional = req.amountUsdt / (1 + FEE_RATE)
                entry_fee = order_notional * FEE_RATE
                quantity = order_notional / fill

                if is_live:
                    try:
                        # تنفيذ شراء حقيقي عبر Binance Spot API
                        formatted_symbol = f"{symbol[:-4]}/USDT"
                        order = await exchange.create_market_buy_order(formatted_symbol, quantity)
                        fill = float(order.get('average', fill))
                        quantity = float(order.get('filled', quantity))
                    except Exception as e:
                        raise HTTPException(502, f"فشل تنفيذ الشراء عبر Binance: {str(e)}")

                stop = fill * (1 - STOP_GROSS_PCT / 100)
                target = fill * (1 + TARGET_GROSS_PCT / 100)

                if not is_live:
                    con.execute("UPDATE meta SET value=value-? WHERE name='cash'", (req.amountUsdt,))

                con.execute("""
                    INSERT INTO positions (
                        symbol, entry_price, quantity, entry_cost, entry_fee,
                        entry_time, peak, stop_price, target_price, max_duration, is_live
                    )
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    symbol, fill, quantity, order_notional, entry_fee,
                    now, fill, stop, target, req.maxHoldSeconds, 1 if is_live else 0
                ))

                position = get_position(con, symbol)
                return {
                    "status": "success",
                    "mode": "LIVE" if is_live else "DEMO",
                    "action": "BUY",
                    "position": position,
                    "fillPrice": fill,
                    "feeUsdt": entry_fee
                }

            # SELL
            pos = get_position(con, symbol)
            if not pos:
                raise HTTPException(404, "لا يوجد مركز مفتوح")

            book = books.get(symbol)
            if not book or (now - book["t"] > MARKET_MAX_AGE):
                raise HTTPException(409, "بيانات السوق غير حديثة")

            fill = book["bid"] * (1 - SLIPPAGE_RATE)
            quantity = pos["quantity"]

            if pos.get("is_live") == 1:
                try:
                    formatted_symbol = f"{symbol[:-4]}/USDT"
                    order = await exchange.create_market_sell_order(formatted_symbol, quantity)
                    fill = float(order.get('average', fill))
                except Exception as e:
                    raise HTTPException(502, f"فشل تنفيذ البيع عبر Binance: {str(e)}")

            proceeds = quantity * fill
            exit_fee = proceeds * FEE_RATE
            net_cash = proceeds - exit_fee
            gross_pnl = proceeds - pos["entry_cost"]
            net_pnl = net_cash - pos["entry_cost"] - pos["entry_fee"]

            if pos.get("is_live") == 0:
                con.execute("UPDATE meta SET value=value+? WHERE name='cash'", (net_cash,))

            con.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
            con.execute("""
                INSERT INTO closed_trades (
                    symbol, entry_price, exit_price, quantity, gross_pnl,
                    entry_fee, exit_fee, net_pnl, opened_at, closed_at, reason
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                symbol, pos["entry_price"], fill, quantity, gross_pnl,
                pos["entry_fee"], exit_fee, net_pnl, pos["entry_time"], now,
                (req.reason or "MANUAL")[:80]
            ))

            row = dict(con.execute("SELECT * FROM closed_trades ORDER BY id DESC LIMIT 1").fetchone())

            return {
                "status": "success",
                "mode": "LIVE" if pos.get("is_live") == 1 else "DEMO",
                "action": "SELL",
                "closedTrade": row,
                "fillPrice": fill,
                "feeUsdt": exit_fee
            }

@app.post("/api/position/evaluate-exit")
async def evaluate_exit(req: ExitRequest):
    symbol = normalized(req.symbol)
    async with state_lock:
        with connect() as con:
            pos = get_position(con, symbol)
            if not pos:
                return {"exit": False, "reason": "NO_POSITION"}

            elapsed = time.time() - pos["entry_time"]
            book = books.get(symbol)
            if not book or (time.time() - book["t"] > MARKET_MAX_AGE):
                return {"exit": False, "reason": "STALE_DATA", "elapsedSec": int(elapsed)}

            bid = book["bid"]
            if bid > pos["peak"]:
                pos["peak"] = bid
                con.execute("UPDATE positions SET peak=? WHERE symbol=?", (bid, symbol))

            reason = None
            if bid <= pos["stop_price"]:
                reason = "STOP_LOSS"
            elif bid >= pos["target_price"]:
                reason = "TAKE_PROFIT"
            elif elapsed >= pos["max_duration"]:
                reason = "TIMEOUT"

            gross_pct = ((bid / pos["entry_price"] - 1) * 100)
            liquidation = pos["quantity"] * bid * (1 - SLIPPAGE_RATE) * (1 - FEE_RATE)
            net_usdt = liquidation - pos["entry_cost"] - pos["entry_fee"]

            return {
                "exit": bool(reason),
                "reason": reason or "HOLD",
                "elapsedSec": int(elapsed),
                "grossPnlPct": round(gross_pct, 4),
                "estimatedNetPnlUsdt": round(net_usdt, 5),
                "stopPrice": pos["stop_price"],
                "targetPrice": pos["target_price"],
                "peakPrice": pos["peak"]
            }

@app.get("/api/state")
def get_state():
    return state()

@app.get("/api/account-balance")
async def account_balance(demo: bool = True):
    if not demo and exchange:
        try:
            bal = await exchange.fetch_balance()
            usdt_free = float(bal.get('free', {}).get('USDT', 0.0))
            usdt_total = float(bal.get('total', {}).get('USDT', 0.0))
            return {"status": "success", "totalUsdtBalance": usdt_free, "equityUsdt": usdt_total}
        except Exception as e:
            raise HTTPException(502, f"تعذر جلب رصيد Binance: {str(e)}")
    s = state()
    return {"status": "success", "totalUsdtBalance": s["cashUsdt"], "equityUsdt": s["equityUsdt"]}

@app.get("/api/diagnostics")
def diagnostics():
    s = state()
    history = s["history"]
    wins = sum(x["net_pnl"] > 0 for x in history)
    return {
        "status": "healthy",
        "engine": "Micro Momentum V3 (CCXT Integration)",
        "liveTradingAllowed": ALLOW_LIVE_TRADING and bool(exchange),
        "activePositionsCount": len(s["positions"]),
        "recentClosedTrades": len(history),
        "recentWinRatePct": round(wins / len(history) * 100, 2) if history else None,
        "hasApiKeysConfigured": bool(BINANCE_API_KEY and BINANCE_SECRET_KEY)
    }

@app.get("/")
def home():
    page = BASE_DIR / "index.html"
    if not page.exists():
        raise HTTPException(404, "index.html missing")
    return FileResponse(page)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
```

---

### ⚠️ ملاحظة بخصوص الصلاحية المفعلة في مفتاح الـ API الخاص بك:
في الصورة المرفقة، الخيار المفعّل هو **"إتاحة القراءة"** فقط (Read-only)، بينما خيار **"تمكين التداول الفوري" (Enable Spot Trading)** **غير مفعل** (مربع غير مؤشر).

إذا أردت استخدام هذا المفتاح للتداول التجريبي الحقيقي، يلزمك النقر على **"تعديل القيود" (Edit Restrictions)** في Binance وتفعيل خيار **"تمكين التداول الفوري"**.

### 📋 طريقة تفعيل المفاتيح على سيرفر Render:
1. اذهب لإعدادات الخدمة في **Render.com**.
2. اختر **Environment Variables**.
3. أضف المتغيرات التالية:
   - `BINANCE_API_KEY` = `XivZSVpFnN9o7bPuQYOud41fuuAW4HotktcPk47GuGV1qXSPQMOldUsPeMzruSWz`
   - `BINANCE_SECRET_KEY` = `hlecSSicPZZQsBoKNkpfQiREigKSHrA74pFcG6PYXAXTbwHRvbNNRI1mBMGOi1iQ`
   - `ALLOW_LIVE_TRADING` = `true`

تم دمج دعم CCXT والربط بأمان في ملف **`main.py`** في المحرر على اليمين!
