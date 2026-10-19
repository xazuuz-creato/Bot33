import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
import asyncio
import os
from aiohttp import web

# --- خادم ويب وهمي لإرضاء منصة Render ومنع إغلاق البوت ---
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
    print(f"🌐 [خادم الويب الوهمي] يعمل على المنفذ {port}")

# --- 1. الإعدادات والبيانات الأساسية ---
API_KEY = '783be60d-4840-4346-836b-4ea98f72c7...'
SECRET_KEY = '7A4D46A13D6259AF127AC10B525C879'
PASSPHRASE = 'your_passphrase'

SYMBOLS = ['BTC/USDT:USDT', 'ETH/USDT:USDT']
RISK_PER_TRADE = 0.01  
PORTFOLIO_RISK_PCT = 0.02 
ESTIMATED_FEE = 0.0008 

# --- 2. دوال حساب متطلبات Price Action و ATR ---
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

# --- 3. المحرك الرئيسي للبوت ---
async def run_price_action_bot():
    try:
        exchange = ccxt.okx({
            'apiKey': API_KEY, 
            'secret': SECRET_KEY,
            'password': PASSPHRASE, 
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'}
        })

        exchange.set_sandbox_mode(True)  
        await exchange.load_markets()

        print("🛡️ [تشغيل محرك Price Action و ATR] التداول بناءً على الهيكلي والارتداد...")

        positions = {sym: False for sym in SYMBOLS}
        take_profit_prices = {sym: 0.0 for sym in SYMBOLS}
        stop_loss_prices = {sym: 0.0 for sym in SYMBOLS}

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
                    current_atr = df_1m['atr'].iloc[-1]
                    ema_15m = df_15m['ema_50'].iloc[-1]

                    is_uptrend = current_close > ema_15m

                    if not positions[symbol]:
                        if is_uptrend and current_close > df_1m['open'].iloc[-1]:
                            positions[symbol] = True
                            stop_loss_prices[symbol] = current_close - (1.5 * current_atr)
                            take_profit_prices[symbol] = current_close + (2.5 * current_atr)
                            print(f"🚀 [دخول صفقة شراء] {symbol} بسعر {current_close}")
                    else:
                        if current_close >= take_profit_prices[symbol] or current_close <= stop_loss_prices[symbol]:
                            print(f"🎯 [إغلاق الصفقة] {symbol} بسعر {current_close}")
                            positions[symbol] = False

                except Exception as e:
                    print(f"⚠️ خطأ في تحليل العملة {symbol}: {e}")

            await asyncio.sleep(10)

    except Exception as e:
        print(f"❌ خطأ رئيسي: {e}")
    finally:
        try:
            await exchange.close()
        except UnboundLocalError:
            pass

# تشغيل خادم الويب الوهمي مع البوت معاً في نفس الوقت
async def main():
    await start_web_server()
    await run_price_action_bot()

if __name__ == '__main__':
    asyncio.run(main())
