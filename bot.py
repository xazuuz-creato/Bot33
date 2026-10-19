import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
import asyncio
import os
from aiohttp import web

# --- خادم ويب وهمي لإرضاء منصة Render ---
async def handle(request):
    return web.Response(text="Bot is running 24/7!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 [خادم الويب الوهمي] يعمل على المنفذ {port}", flush=True)

# --- 1. الإعدادات والبيانات الأساسية ---
API_KEY = '783be60d-4840-4346-836b-4ea98f72c7...'  # ضع مفتاحك الكامل هنا
SECRET_KEY = '7A4D46A13D6259AF127AC10B525C879'
PASSPHRASE = 'Aa11223344##'

# العملات المشمولة (تمت إضافة سولانا SOL كعملة تريند رئيسية)
SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
TRADE_AMOUNT = 0.001  # حجم الشراء التجريبي (يمكن تعديله حسب رصيدك التجريبي الفوري)

# --- 2. دوال حساب المؤشرات ---
def calculate_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    return true_range.rolling(period).mean()

def calculate_ema(closes, period=50):
    series = pd.Series(closes)
    return series.ewm(span=period, adjust=False).mean()

# --- 3. المحرك الرئيسي للبوت (التداول الفوري Spot) ---
async def run_price_action_bot():
    try:
        exchange = ccxt.okx({
            'apiKey': API_KEY, 
            'secret': SECRET_KEY,
            'password': PASSPHRASE, 
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}  # تم التعديل إلى التداول الفوري Spot
        })

        # تفعيل وضع التداول التجريبي (Demo Trading)
        exchange.set_sandbox_mode(True)  
        await exchange.load_markets()

        print("🛡️ [تشغيل محرك التداول الفوري التجريبي Spot على OKX] جاري فحص السوق...", flush=True)

        positions = {sym: False for sym in SYMBOLS}

        while True:
            for symbol in SYMBOLS:
                try:
                    ohlcv_1m = await exchange.fetch_ohlcv(symbol, timeframe='1m', limit=100)
                    ohlcv_15m = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)

                    if len(ohlcv_1m) < 50 or len(ohlcv_15m) < 50:
                        continue

                    df_1m = pd.DataFrame(ohlcv_1m, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
                    df_15m = pd.DataFrame(ohlcv_15m, columns=['time', 'open', 'high', 'low', 'close', 'volume'])

                    df_1m['atr'] = calculate_atr(df_1m, period=14)
                    df_15m['ema_50'] = calculate_ema(df_15m['close'], period=50)

                    current_close = df_1m['close'].iloc[-1]
                    current_open = df_1m['open'].iloc[-1]
                    ema_15m = df_15m['ema_50'].iloc|-1| if not df_15m['ema_50'].empty else current_close

                    is_uptrend = current_close > ema_15m

                    # طباعة متابعة حالة السوق في السجلات
                    print(f"📊 [{symbol}] السعر: {current_close} | صاعد: {is_uptrend}", flush=True)

                    if not positions[symbol]:
                        # شرط الشراء الفوري
                        if is_uptrend and current_close > current_open:
                            print(f"🚀 [إرسال أمر شراء فوري (Spot) إلى OKX]
