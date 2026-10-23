import os
import sys
import aiohttp
import asyncio
import hmac
import hashlib
import base64
import json
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

RIYADH_TZ = timezone(timedelta(hours=3))

def get_riyadh_time():
    return datetime.now(RIYADH_TZ).strftime('%Y-%m-%d %I:%M:%S %p')

class BotConfig(BaseModel):
    is_running: bool = True
    trade_amount_usdt: float = 100.0
    target_tp_pct: float = 0.018
    stop_loss_pct: float = 0.015
    excluded_symbols: list = []

app = FastAPI(title="OKX Multi-Engine Backend Scalper", version="6.3")

BOT_STATE = {
    "config": BotConfig(),
    "stats": {
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "total_profit_usdt": 0.0,
        "active_positions_count": 0,
        "engine_logs": {}
    },
    "active_position": None
}

API_KEY = '2bf148cb-c010-49fb-bffc-79f72720dfae'
SECRET_KEY = '1FBA3210BF6500BB1A1570EE9EC41A5C'
PASSPHRASE = 'Aa11223344##'

MAJOR_COINS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "ADA-USDT"]

def get_okx_headers(method, request_path, body=""):
    timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    message = timestamp + method.upper() + request_path + body
    mac = hmac.new(SECRET_KEY.encode('utf-8'), message.encode('utf-8'), hashlib.sha256)
    signature = base64.b64encode(mac.digest()).decode('utf-8')
    return {
        "Content-Type": "application/json",
        "OK-ACCESS-KEY": API_KEY,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": PASSPHRASE,
        "x-simulated-trading": "1"
    }

async def fetch_okx_candles(session, symbol):
    url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar=5m&limit=210"
    try:
        async with session.get(url, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                if data.get("code") == "0" and data.get("data"):
                    raw = data["data"]
                    candles = []
                    for c in reversed(raw):
                        candles.append([int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
                    return candles
    except Exception as e:
        print(f"[{get_riyadh_time()}] ⚠️ [شبكة] خطأ سحب {symbol}: {e}", flush=True, file=sys.stdout)
    return None

def calculate_ema(data, period):
    multiplier = 2 / (period + 1)
    ema_list = []
    for i, val in enumerate(data):
        if i == 0:
            ema_list.append(val)
        else:
            ema_list.append((val - ema_list[-1]) * multiplier + ema_list[-1])
    return ema_list

def technical_analyst_bot(candles):
    try:
        if not candles or len(candles) < 205:
            return False, "بيانات غير كافية"

        closes = [r[4] for r in candles]
        volumes = [r[5] for r in candles]

        ema9_list = calculate_ema(closes, 9)
        ema21_list = calculate_ema(closes, 21)
        ema200_list = calculate_ema(closes, 200)

        curr_close = closes[-1]
        curr_ema200 = ema200_list[-1]
        curr_ema9 = ema9_list[-1]
        prev_ema9 = ema9_list[-2]
        curr_ema21 = ema21_list[-1]
        prev_ema21 = ema21_list[-2]

        is_above_ema200 = curr_close > curr_ema200
        is_ema_crossed = (prev_ema9 <= prev_ema21) and (curr_ema9 > curr_ema21)

        gains, losses = 0.0, 0.0
        for i in range(-14, 0):
            diff = closes[i] - closes[i-1]
            if diff > 0:
                gains += diff
            else:
                losses -= diff
        avg_gain = gains / 14
        avg_loss = losses / 14 if losses != 0 else 0.001
        rs = avg_gain / avg_loss
        rsi_val = 100 - (100 / (1 + rs))

        is_rsi_healthy = 40 < rsi_val < 65
        avg_volume_20 = sum(volumes[-20:]) / 20
        is_volume_ok = volumes[-1] > avg_volume_20

        if is_above_ema200 and is_ema_crossed and is_rsi_healthy and is_volume_ok:
            return True, f"إشارة شراء | تقاطع EMA9 صاعد | RSI: {rsi_val:.1f}"

        return False, f"تحليل عادي | RSI: {rsi_val:.1f}"
    except Exception as e:
        return False, f"خطأ تحليل: {str(e)}"

async def execute_okx_order(session, symbol, side, sz):
    url = "https://www.okx.com/api/v5/trade/order"
    body_dict = {
        "instId": symbol,
        "tdMode": "cash",
        "side": side,
        "ordType": "market",
        "sz": str(sz)
    }
    body_str = json.dumps(body_dict)
    headers = get_okx_headers("POST", "/api/v5/trade/order", body_str)
    try:
        async with session.post(url, headers=headers, data=body_str, timeout=10) as resp:
            if resp.status == 200:
                res_data = await resp.json()
                if res_data.get("code") == "0":
                    print(f"[{get_riyadh_time()}] 🛒 تنفيذ طلب {side} لـ {symbol} بنجاح", flush=True, file=sys.stdout)
                    return True, res_data
    except Exception as e:
        print(f"[{get_riyadh_time()}] ❌ خطأ تنفيذ: {e}", flush=True, file=sys.stdout)
    return False, None

# وظيفة الحفاظ على استمرار السيرفر لمنع إغلاقه من Render
async def self_ping_keep_alive():
    await asyncio.sleep(10)
    port = os.environ.get("PORT", "10000")
    url = f"http://127.0.0.1:{port}/"
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(url, timeout=5) as resp:
                    pass
            except Exception:
                pass
            await asyncio.sleep(25) # إرسال نبضة كل 25 ثانية لمنع الخمول

async def background_multi_bot_supervisor():
    print(f"[{get_riyadh_time()}] 🤖 [تشغيل مستمر] نظام البوتات الخلفية يعمل الآن دون انقطاع...", flush=True, file=sys.stdout)
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                config = BOT_STATE["config"]
                if not config.is_running:
                    await asyncio.sleep(10)
                    continue

                active_pos = BOT_STATE["active_position"]
                if active_pos and active_pos["in_pos"]:
                    symbol = active_pos["symbol"]
                    candles = await fetch_okx_candles(session, symbol)
                    if candles:
                        curr_price = candles[-1][4]
                        entry = active_pos["entry_price"]
                        amount = active_pos["amount"]
                        profit_ratio = (curr_price - entry) / entry

                        if curr_price > active_pos["highest_price"]:
                            active_pos["highest_price"] = curr_price

                        elapsed_minutes = (datetime.now(RIYADH_TZ) - active_pos["entry_time"]).total_seconds() / 60
                        drop_from_peak = (active_pos["highest_price"] - curr_price) / active_pos["highest_price"]

                        print(f"[{get_riyadh_time()}] 🛡️ [متابعة صفقة] {symbol} | الربح: {profit_ratio*100:.2f}%", flush=True, file=sys.stdout)

                        if profit_ratio >= config.target_tp_pct or ((profit_ratio >= 0.005) and (drop_from_peak >= 0.004)) or profit_ratio <= -config.stop_loss_pct or elapsed_minutes >= 45:
                            print(f"[{get_riyadh_time()}] 🔴 [إغلاق صفقة] بيع {symbol} عند السعر {curr_price}", flush=True, file=sys.stdout)
                            await execute_okx_order(session, symbol, "sell", amount)
                            
                            profit_usdt = (curr_price - entry) * (amount * curr_price)
                            BOT_STATE["active_position"] = None
                            BOT_STATE["stats"]["total_trades"] += 1
                            if profit_usdt > 0:
                                BOT_STATE["stats"]["winning_trades"] += 1
                            else:
                                BOT_STATE["stats"]["losing_trades"] += 1
                            BOT_STATE["stats"]["total_profit_usdt"] += profit_usdt
                            BOT_STATE["stats"]["active_positions_count"] = 0

                    await asyncio.sleep(10)
                    continue

                for symbol in MAJOR_COINS:
                    if symbol in config.excluded_symbols:
                        continue

                    candles = await fetch_okx_candles(session, symbol)
                    if not candles or len(candles) < 205:
                        continue

                    is_signal, tech_reason = technical_analyst_bot(candles)
                    BOT_STATE["stats"]["engine_logs"][symbol] = tech_reason
                    curr_p = candles[-1][4]

                    print(f"[{get_riyadh_time()}] 📊 [{symbol}] | السعر: {curr_p} | الحالة: {tech_reason}", flush=True, file=sys.stdout)

                    if is_signal:
                        synchronized_price = curr_p
                        sz_amount = round(config.trade_amount_usdt / synchronized_price, 4)
                        target_price = round(synchronized_price * (1 + config.target_tp_pct), 4)

                        print("="*50, flush=True, file=sys.stdout)
                        print(f"🚀 [إشارة دخول] عملة: {symbol} | السعر: {synchronized_price}", flush=True, file=sys.stdout)
                        print("="*50, flush=True, file=sys.stdout)

                        success, _ = await execute_okx_order(session, symbol, "buy", sz_amount)
                        if success:
                            BOT_STATE["active_position"] = {
                                "in_pos": True,
                                "symbol": symbol,
                                "entry_price": synchronized_price,
                                "target_price": target_price,
                                "highest_price": synchronized_price,
                                "amount": sz_amount,
                                "entry_time": datetime.now(RIYADH_TZ)
                            }
                            BOT_STATE["stats"]["active_positions_count"] = 1
                            break

            except Exception as e:
                print(f"[{get_riyadh_time()}] ⚠️ خطأ في حلقة التشغيل: {e}", flush=True, file=sys.stdout)

            await asyncio.sleep(15)

@app.get("/")
def health_check():
    return {"status": "ok", "system": "Running Continuously"}

@app.get("/api/status")
def get_status():
    return BOT_STATE

@app.post("/api/toggle")
def toggle_bot():
    BOT_STATE["config"].is_running = not BOT_STATE["config"].is_running
    return {"success": True, "is_running": BOT_STATE["config"].is_running}

@app.on_event("startup")
async def startup_event():
    loop = asyncio.get_event_loop()
    loop.create_task(background_multi_bot_supervisor())
    loop.create_task(self_ping_keep_alive()) # تفعيل نظام منع الخمول الذاتي

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("bot:app", host="0.0.0.0", port=port)
