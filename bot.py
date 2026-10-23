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
    target_tp_pct: float = 0.018      # هدف ربح 1.8%
    stop_loss_pct: float = 0.015      # وقف خسارة 1.5%
    excluded_symbols: list = []

app = FastAPI(title="OKX Professional Scalping Bot", version="4.2")

BOT_STATE = {
    "config": BotConfig(),
    "stats": {
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "total_profit_usdt": 0.0,
        "active_positions_count": 0,
        "market_warnings": {}
    },
    "active_position": None
}

# 🔐 بيانات الـ API الخاصة بمنصة OKX (وضع التجربة مفعل بأمان)
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
        print(f"[{get_riyadh_time()}] ⚠️ خطأ في سحب بيانات {symbol}: {e}", flush=True, file=sys.stdout)
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

def advanced_freqtrade_strategy_logic(candles):
    try:
        if not candles or len(candles) < 205:
            return False, "بيانات الشموع غير كافية لـ EMA 200"

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
            return True, f"شروط الاستراتيجية محققة | EMA9 تقاطع للأعلى | RSI: {rsi_val:.1f}"

        return False, f"السوق مستقر | RSI: {rsi_val:.1f} | بانتظار إشارة تقاطع"
    except Exception as e:
        return False, f"خطأ في التحليل: {str(e)}"

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
                    print(f"[{get_riyadh_time()}] 🛒 تم تنفيذ طلب {side} بنجاح على منصة OKX لـ {symbol}", flush=True, file=sys.stdout)
                    return True, res_data
                else:
                    print(f"[{get_riyadh_time()}] ⚠️ رفض الطلب من OKX: {res_data}", flush=True, file=sys.stdout)
    except Exception as e:
        print(f"[{get_riyadh_time()}] ❌ خطأ في الاتصال لتنفيذ الطلب: {e}", flush=True, file=sys.stdout)
    return False, None

async def background_scalping_bot():
    print(f"[{get_riyadh_time()}] 🤖 [تشغيل تام] بوت السكالبينج متصل بمنصة OKX ويعمل بانتظام...", flush=True, file=sys.stdout)
    
    async with aiohttp.ClientSession() as session:
        while True:
            config = BOT_STATE["config"]
            if not config.is_running:
                print(f"[{get_riyadh_time()}] ⏸️ [مؤقت] البوت متوقف حالياً بناءً على إعداداتك...", flush=True, file=sys.stdout)
                await asyncio.sleep(10)
                continue

            try:
                print(f"[{get_riyadh_time()}] 🔄 [دورة فحص جديدة] جاري فحص الأسواق عبر API OKX...", flush=True, file=sys.stdout)

                active_pos = BOT_STATE["active_position"]
                
                # إدارة الصفقة المفتوحة
                if active_pos and active_pos["in_pos"]:
                    symbol = active_pos["symbol"]
                    candles = await fetch_okx_candles(session, symbol)
                    if candles:
                        curr_price = candles[-1][4]
                        entry = active_pos["entry_price"]
                        target = active_pos["target_price"]
                        amount = active_pos["amount"]
                        profit_ratio = (curr_price - entry) / entry

                        if curr_price > active_pos["highest_price"]:
                            active_pos["highest_price"] = curr_price

                        elapsed_minutes = (datetime.now(RIYADH_TZ) - active_pos["entry_time"]).total_seconds() / 60
                        drop_from_peak = (active_pos["highest_price"] - curr_price) / active_pos["highest_price"]

                        print(f"[{get_riyadh_time()}] 📊 [متابعة صفقة مفتوحة] {symbol} | السعر: {curr_price} | الدخول: {entry} | الربح: {profit_ratio*100:.2f}%", flush=True, file=sys.stdout)

                        is_target_hit = profit_ratio >= config.target_tp_pct
                        is_trailing_hit = (profit_ratio >= 0.005) and (drop_from_peak >= 0.004)
                        is_stop_loss = profit_ratio <= -config.stop_loss_pct
                        is_timeout = elapsed_minutes >= 45

                        if is_target_hit or is_trailing_hit or is_stop_loss or is_timeout:
                            print(f"[{get_riyadh_time()}] 🔴 [إغلاق صفقة] تنفيذ البيع لـ {symbol} عند السعر {curr_price}", flush=True, file=sys.stdout)
                            await execute_okx_order(session, symbol, "sell", amount)
                            
                            profit_usdt = (curr_price - entry) * (amount * curr_price)
                            BOT_STATE["active_position"] = None
                            
                            BOT_STATE["stats"]["total_trades"] += 1
                            if profit_usdt > 0:
                                BOT_STATE["stats"]["winning_trades"] += 1
                                print(f"[{get_riyadh_time()}] 💰 [ربح محقق] +{profit_usdt:.4f} USDT", flush=True, file=sys.stdout)
                            else:
                                BOT_STATE["stats"]["losing_trades"] += 1
                                print(f"[{get_riyadh_time()}] 📉 [خسارة مسجلة] {profit_usdt:.4f} USDT", flush=True, file=sys.stdout)

                            BOT_STATE["stats"]["total_profit_usdt"] += profit_usdt
                            BOT_STATE["stats"]["active_positions_count"] = 0

                    await asyncio.sleep(10)
                    continue

                # فحص العملات الكبرى
                for symbol in MAJOR_COINS:
                    if symbol in config.excluded_symbols:
                        continue

                    candles = await fetch_okx_candles(session, symbol)
                    if not candles or len(candles) < 205:
                        print(f"[{get_riyadh_time()}] ⏳ العملة {symbol}: جاري تجميع الشموع الكافية عبر الـ API...", flush=True, file=sys.stdout)
                        continue

                    is_signal, reason = advanced_freqtrade_strategy_logic(candles)
                    BOT_STATE["stats"]["market_warnings"][symbol] = reason
                    curr_p = candles[-1][4]

                    print(f"[{get_riyadh_time()}] 🔍 فحص [{symbol}] | السعر: {curr_p} | الحالة: {reason}", flush=True, file=sys.stdout)

                    if is_signal:
                        synchronized_price = curr_p
                        sz_amount = round(config.trade_amount_usdt / synchronized_price, 4)
                        target_price = round(synchronized_price * (1 + config.target_tp_pct), 4)
                        trade_time = get_riyadh_time()

                        print("="*60, flush=True, file=sys.stdout)
                        print(f"🚀 [إشارة دخول استراتيجية جديدة عبر OKX API]", flush=True, file=sys.stdout)
                        print(f"🪙 العملة: {symbol}", flush=True, file=sys.stdout)
                        print(f"⏰ الوقت: {trade_time}", flush=True, file=sys.stdout)
                        print(f"💵 سعر الدخول: {synchronized_price} USDT", flush=True, file=sys.stdout)
                        print(f"🎯 الهدف: {target_price} USDT", flush=True, file=sys.stdout)
                        print(f"💡 السبب الفني: {reason}", flush=True, file=sys.stdout)
                        print("="*60, flush=True, file=sys.stdout)

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
                print(f"[{get_riyadh_time()}] ⚠️ خطأ عام في دورة العمل: {e}", flush=True, file=sys.stdout)

            print(f"[{get_riyadh_time()}] ⏳ [انتظار] الانتقال للدورة القادمة...", flush=True, file=sys.stdout)
            await asyncio.sleep(15)

@app.get("/")
def health_check():
    return {"status": "ok", "service": "OKX Strategy Bot Alive"}

@app.get("/api/status")
def get_status():
    return BOT_STATE

@app.post("/api/config")
def update_config(new_config: BotConfig):
    BOT_STATE["config"] = new_config
    return {"success": True, "message": "تم تحديث الإعدادات", "new_config": new_config}

@app.post("/api/toggle")
def toggle_bot():
    BOT_STATE["config"].is_running = not BOT_STATE["config"].is_running
    status_text = "Running" if BOT_STATE["config"].is_running else "Paused"
    return {"success": True, "is_running": BOT_STATE["config"].is_running, "message": f"حالة البوت: {status_text}"}

@app.on_event("startup")
async def startup_event():
    loop = asyncio.get_event_loop()
    loop.create_task(background_scalping_bot())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("bot:app", host="0.0.0.0", port=port)
