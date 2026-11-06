import os
import time
import uuid
import sqlite3
import asyncio
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import ccxt.async_support as ccxt

try:
    import ccxt.pro as ccxtpro
except ImportError:
    ccxtpro = None

app = FastAPI(
    title="Institutional Micro-Momentum Scalping Engine",
    description="Micro-Momentum V2 Engine with signalToken, OFI & Persistence",
    version="2.0.0"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "positions.db")
ALLOW_LIVE_TRADING = os.environ.get("ALLOW_LIVE_TRADING", "false").lower() == "true"

# ---- Initialize DB Persistence ----
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY,
            entry_price REAL,
            amount REAL,
            usdt_value REAL,
            peak_price REAL,
            entry_time REAL,
            stop_loss REAL,
            take_profit REAL,
            max_duration INTEGER
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ---- State Storage ----
valid_tokens: Dict[str, float] = {}  # token -> expiration timestamp
latest_market_data: Dict[str, Dict[str, Any]] = {}

class MarketBookData(BaseModel):
    symbol: str
    bids: list
    asks: list
    timestamp: Optional[int] = None

class EvaluateEntryRequest(BaseModel):
    symbol: str

class ExecuteOrderRequest(BaseModel):
    symbol: str
    action: str  # BUY or SELL
    amountUsdt: float = 100.0
    signalToken: Optional[str] = None
    useDemo: bool = True

class EvaluateExitRequest(BaseModel):
    symbol: str
    currentPrice: float

# Helper DB Functions
def save_position_db(pos: dict):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO positions (symbol, entry_price, amount, usdt_value, peak_price, entry_time, stop_loss, take_profit, max_duration)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (pos['symbol'], pos['entryPrice'], pos['amount'], pos['usdtValue'], pos['peakPrice'], pos['entryTime'], pos['stopLoss'], pos['takeProfit'], pos['maxDuration']))
    conn.commit()
    conn.close()

def delete_position_db(symbol: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM positions WHERE symbol = ?', (symbol,))
    conn.commit()
    conn.close()

def get_position_db(symbol: str) -> Optional[dict]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT symbol, entry_price, amount, usdt_value, peak_price, entry_time, stop_loss, take_profit, max_duration FROM positions WHERE symbol = ?', (symbol,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "symbol": row[0], "entryPrice": row[1], "amount": row[2],
            "usdtValue": row[3], "peakPrice": row[4], "entryTime": row[5],
            "stopLoss": row[6], "takeProfit": row[7], "maxDuration": row[8]
        }
    return None

@app.post("/api/market-data/book")
async def receive_market_book(data: MarketBookData):
    if not data.bids or not data.asks:
        return {"status": "ignored"}
    
    bids_vol = sum(float(b[1]) for b in data.bids[:5])
    asks_vol = sum(float(a[1]) for a in data.asks[:5])
    imbalance = bids_vol / asks_vol if asks_vol > 0 else 1.0

    latest_market_data[data.symbol] = {
        "bestBid": float(data.bids[0][0]),
        "bestAsk": float(data.asks[0][0]),
        "imbalanceRatio": imbalance,
        "spread": float(data.asks[0][0]) - float(data.bids[0][0]),
        "timestamp": time.time()
    }
    return {"status": "ok"}

@app.post("/api/evaluate-entry")
async def evaluate_entry(req: EvaluateEntryRequest):
    m_data = latest_market_data.get(req.symbol)
    if not m_data or (time.time() - m_data["timestamp"] > 3.0):
        return {"enter": False, "score": 0, "reasons": ["بيانات السوق غير متوفرة أو قديمة"]}

    if get_position_db(req.symbol):
        return {"enter": False, "score": 0, "reasons": ["توجد صفقة مفتوحة بالفعل لهذا الرمز"]}

    imbalance = m_data["imbalanceRatio"]
    spread_pct = (m_data["spread"] / m_data["bestAsk"]) * 100

    # تقييم الشروط المؤسسية V2
    if spread_pct > 0.08:
        return {"enter": False, "score": 40, "reasons": [f"السبريد مرتفع جداً ({spread_pct:.3f}%)"]}

    if imbalance >= 3.0:
        # إصدار signalToken بصلاحية 1.5 ثانية فقط لتقليل الانزلاق
        token = f"ST-{uuid.uuid4().hex[:12]}"
        valid_tokens[token] = time.time() + 1.5
        return {
            "enter": True,
            "score": 92,
            "reasons": [f"تدفق سيولة ممتاز (Imbalance: {imbalance:.2f}x)"],
            "signalToken": token
        }

    return {"enter": False, "score": int(imbalance * 20), "reasons": ["اندفاع السيولة غير كافٍ"]}

@app.post("/api/execute-order")
async def execute_order(req: ExecuteOrderRequest):
    now = time.time()
    
    if req.action.upper() == "BUY":
        if not req.signalToken or req.signalToken not in valid_tokens:
            raise HTTPException(status_code=400, detail="توكين الإشارة مفقود أو غير صالح")
        
        exp_time = valid_tokens.pop(req.signalToken)
        if now > exp_time:
            raise HTTPException(status_code=400, detail="انتهت صلاحية signalToken (تجاوز 1.5 ثانية)")

        if not req.useDemo and not ALLOW_LIVE_TRADING:
            raise HTTPException(status_code=403, detail="التداول الحقيقي معطل بمتغير البيئة ALLOW_LIVE_TRADING")

        m_data = latest_market_data.get(req.symbol, {"bestAsk": 100.0})
        entry_price = m_data["bestAsk"]
        amount = req.amountUsdt / entry_price

        pos = {
            "symbol": req.symbol,
            "entryPrice": entry_price,
            "amount": amount,
            "usdtValue": req.amountUsdt,
            "peakPrice": entry_price,
            "entryTime": now,
            "stopLoss": entry_price * 0.996,
            "takeProfit": entry_price * 1.0035,
            "maxDuration": 180
        }
        save_position_db(pos)
        return {"status": "success", "action": "BUY", "position": pos}

    elif req.action.upper() == "SELL":
        pos = get_position_db(req.symbol)
        delete_position_db(req.symbol)
        return {"status": "success", "action": "SELL", "closed_position": pos}

@app.post("/api/position/evaluate-exit")
async def evaluate_exit(req: EvaluateExitRequest):
    pos = get_position_db(req.symbol)
    if not pos:
        return {"exit": False, "reason": "No active position"}

    current_price = req.currentPrice
    now = time.time()
    elapsed = now - pos["entryTime"]

    if current_price > pos["peakPrice"]:
        pos["peakPrice"] = current_price
        save_position_db(pos)

    pnl_pct = ((current_price - pos["entryPrice"]) / pos["entryPrice"]) * 100

    if current_price >= pos["takeProfit"]:
        return {"exit": True, "reason": "TARGET_REACHED", "pnlPct": pnl_pct}
    if current_price <= pos["stopLoss"]:
        return {"exit": True, "reason": "STOP_LOSS", "pnlPct": pnl_pct}
    if elapsed >= pos["maxDuration"]:
        return {"exit": True, "reason": "TIMEOUT", "pnlPct": pnl_pct}

    return {"exit": False, "currentPnlPct": pnl_pct, "elapsedSec": int(elapsed)}

@app.get("/api/diagnostics")
async def get_diagnostics():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM positions")
    active_count = cursor.fetchone()[0]
    conn.close()

    return {
        "status": "healthy",
        "engine": "Micro-Momentum V2",
        "activePositionsCount": active_count,
        "liveTradingAllowed": ALLOW_LIVE_TRADING,
        "tokenStoreSize": len(valid_tokens)
    }

@app.get("/api/account-balance")
async def account_balance(demo: bool = True):
    return {"status": "success", "totalUsdtBalance": 10000.0 if demo else 0.0}

@app.get("/", response_class=HTMLResponse)
async def read_index():
    index_path = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    return HTMLResponse(content="<h1>index.html Not Found</h1>", status_code=404)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
