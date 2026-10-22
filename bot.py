import os
import sys
import aiohttp
import asyncio
import hmac
import hashlib
import base64
import json
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

RIYADH_TZ = timezone(timedelta(hours=3))

def get_riyadh_time():
    return datetime.now(RIYADH_TZ).strftime('%Y-%m-%d %I:%M:%S %p')

# --- إعدادات التطبيق القابلة للتعديل عبر الـ API الخارجي ---
class BotConfig(BaseModel):
    is_running: bool = True
    trade_amount_usdt: float = 100.0
    target_tp_pct: float = 0.03       # هدف ربح 3%
    min_secure_tp: float = 0.008      # جني أرباح آمن 0.8%
    stop_loss_pct: float = 0.006      # وقف خسارة 0.6%
    excluded_symbols: list = []

app = FastAPI(title="Advanced OKX Scalping Bot API", version="2.0")

# الحالة العامة للبوت
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

# بيانات الاعتماد لمنصة OKX
API_KEY = '2bf148cb-c010-49fb-bffc-79f72720dfae'
SECRET_KEY = '1FBA3210BF6500BB1A1570EE9EC41A5C'
PASSPHRASE = 'Aa11223344##'

MAJOR_COINS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "ADA-USDT"]

# --- توقيع الطلبات الآمن لمنصة OKX ---
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
        "x-simulated-trading": "1"  # وضع التجربة التجريبي (Sandbox)
    }

# --- سحب الشموع والأسعار المتزامنة من OKX ---
async def fetch_okx_candles(session, symbol):
    url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar=15m&limit=30"
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
    except Exception:
        pass
    return None

# --- الاستراتيجية المتقدمة: اصطياد القيعان، تحديد القمم، وفلترة التذبذب العرضي ---
def advanced_market_analysis(data):
    try:
        if not data or len(data) < 20:
            return False, "بيانات غير كافية"
            
        c = [r[4] for r in data]
        h = [r[2] for r in data]
        l = [r[3] for r in data]
        o = [r[1] for r in data]
        
        # 1. فلتر الأسواق المتذبذبة والغير واضحة (Choppy Market Filter)
        # نقيس مدى تباعد نطاق الشموع (Average True Range البديل أو قياس التذبذب العرضي)
        recent_ranges = [h[i] - l[i] for i in range(-5, 0)]
        avg_range = sum(recent_ranges) / 5
        price_movement = abs(c[-1] - c[-5])
        
        # إذا كان التذبذب العشوائي عالي ولا يوجد اتجاه سعري حقيقي (سوق متذبذب جانبي مضلل)
        if avg_range > (c[-1] * 0.015) and price_movement < (avg_range * 0.8):
            return False, "⚠️ تحذير: سوق متذبذب وعرضي غير واضح (تم التجنب)"

        # 2. المتوسطات المتحركة لتحديد الاتجاه العام
        sma_fast = sum(c[-7:]) / 7
        sma_slow = sum(c[-20:]) / 20
        is_bullish_trend = sma_fast > sma_slow
        
        # 3. حساب مؤشر القوة النسبية RSI لاصطياد الانخفاضات (Dips)
        gains = 0.0
        losses = 0.0
        for i in range(-14, 0):
            diff = c[i] - c[i-1]
            if diff > 0:
                gains += diff
            else:
                losses -= diff
                
        avg_gain = gains / 14
        avg_loss = losses / 14 if losses != 0 else 0.001
        rs = avg_gain / avg_loss
        rsi_val = 100 - (100 / (1 + rs))
        
        # شروط اصطياد الانخفاض القاعي الصحي (Dip Buying) مع تأكيد الشمعة الخضراء
        recent_low = min(l[-6:-1])
        curr_low = l[-1]
        curr_open = o[-1]
        curr_close = c[-1]
        
        is_green_candle = curr_close > curr_open
        body_size = abs(curr_close - curr_open)
        total_range = h[-1] - curr_low
        
        if total_range == 0:
            return False, "مدى الشمعة صفر"

        # الارتداد من منطقة دعم قاعية مع RSI صحي وغير متضخم
        is_bouncing_from_dip = (curr_low <= recent_low * 1.004) and is_green_candle and ((body_size / total_range) > 0.35)
        is_rsi_dip_zone = 35 <= rsi_val <= 60  # شراء من الانخفاضات الصحية وليس التشبعات الوهمية

        if is_bullish_trend and is_bouncing_from_dip and is_rsi_dip_zone:
            return True, "🟢 فرصة شراء ممتازة عند الانخفاض (Dip Entry مؤكد)"

        return False, "السوق غير مناسب حالياً أو لا توجد إشارة واضحة"
    except Exception as e:
        return False, f"خطأ في التحليل: {str(e)}"

# --- تنفيذ الأوامر عبر API OKX ---
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
                    return True, res_data
    except Exception as e:
        print(f"Order error: {e}", flush=True, file=sys.stdout)
    return False, None

# --- حلقة التداول الخلفية المستمرة ---
async def background_scalping_bot():
    print(f"[{get_riyadh_time()}] 🚀 [البوت الذكي المتقدم] بدأ العمل بنجاح...", flush=True, file=sys.stdout)
    
    async with aiohttp.ClientSession() as session:
        while True:
            config = BOT_STATE["config"]
            if not config.is_running:
                await asyncio.sleep(5)
                continue

            try:
                active_pos = BOT_STATE["active_position"]
                
                # --- إدارة ومتابعة الصفقة النشطة ---
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

                        # شروط الخروج عند القمة، الهدف، أو جني الأرباح الآمن
                        is_safe_exit = (profit_ratio >= config.min_secure_tp) and (drop_from_peak >= 0.0025)
                        is_max_target = profit_ratio >= config.target_tp_pct
                        is_timeout = elapsed_minutes >= 45

                        if is_safe_exit or is_max_target or is_timeout or (profit_ratio <= -config.stop_loss_pct):
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

                    await asyncio.sleep(5)
                    continue

                # --- فحص السوق واصطياد الانخفاضات ---
                for symbol in MAJOR_COINS:
                    if symbol in config.excluded_symbols:
                        continue

                    candles = await fetch_okx_candles(session, symbol)
                    if not candles or len(candles) < 25:
                        continue

                    is_signal, reason = advanced_market_analysis(candles)
                    
                    # تسجيل حالة السوق للتنبيهات والتحكم الخارجي
                    BOT_STATE["stats"]["market_warnings"][symbol] = reason

                    if is_signal:
                        synchronized_price = candles[-1][4]
                        sz_amount = round(config.trade_amount_usdt / synchronized_price, 4)

                        success, _ = await execute_okx_order(session, symbol, "buy", sz_amount)
                        if success:
                            BOT_STATE["active_position"] = {
                                "in_pos": True,
                                "symbol": symbol,
                                "entry_price": synchronized_price,
                                "highest_price": synchronized_price,
                                "amount": sz_amount,
                                "entry_time": datetime.now(RIYADH_TZ)
                            }
                            BOT_STATE["stats"]["active_positions_count"] = 1
                            break

            except Exception as e:
                print(f"⚠️ خطأ في دورة البوت: {e}", flush=True, file=sys.stdout)

            await asyncio.sleep(10)

# --- واجهات الـ API للتحكم بالبوت من تطبيق خارجي ---
@app.get("/")
def read_root():
    return {"status": "online", "message": "OKX Advanced Scalping Bot API is running perfectly!"}

@app.get("/api/status")
def get_status():
    return BOT_STATE

@app.post("/api/config")
def update_config(new_config: BotConfig):
    BOT_STATE["config"] = new_config
    return {"success": True, "message": "تم تحديث إعدادات البوت بنجاح عبر الـ API", "new_config": new_config}

@app.post("/api/toggle")
def toggle_bot():
    BOT_STATE["config"].is_running = not BOT_STATE["config"].is_running
    status_text = "تشغيل" if BOT_STATE["config"].is_running else "إيقاف مؤقت"
    return {"success": True, "is_running": BOT_STATE["config"].is_running, "message": f"تم تغيير حالة البوت إلى: {status_text}"}

# --- تشغيل البوت مع السيرفر بالتزامن ---
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(background_scalping_bot())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("bot:app", host="0.0.0.0", port=port)
