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
API_KEY = '783be60d-4840-4346-836b-4ea98f72c7...'  # مفتاح الـ API الخاص بك
SECRET_KEY = '7A4D46A13D6259AF127AC10B525C879'
PASSPHRASE = 'Aa11223344##'

# العملات المشمولة (BTC, ETH, SOL)
SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
TRADE_AMOUNT = 0.001  # حجم الشراء التجريبي

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
            'options': {'defaultType': 'spot'}  # تداول فوري Spot
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
                    
                    ema_values = df_15m['ema_50']
                    ema_15m = ema_values.iloc[-1] if not ema_values.empty and not pd.isna(ema_values.iloc[-1]) else current_close

                    is_uptrend = current_close > ema_15m

                    # طباعة متابعة حالة السوق في السجلات
                    print(f"📊 [{symbol}] السعر: {current_close} | صاعد: {is_uptrend}", flush=True)

                    if not positions[symbol]:
                        # شرط الشراء الفوري
                        if is_uptrend and current_close > current_open:
                            print(f"🚀 [إرسال أمر شراء فوري (Spot) إلى OKX] للرمز {symbol}...", flush=True)
                            
                            order = await exchange.create_market_buy_order(symbol, TRADE_AMOUNT)
                            print(f"✅ [تم تنفيذ الشراء الفوري بنجاح]: {order.get('id', 'OK')}", flush=True)
                            positions[symbol] = True
                    else:
                        # شرط البيع الفوري
                        if not is_uptrend:
                            print(f"🎯 [إرسال أمر بيع فوري إلى OKX] للرمز {symbol}...", flush=True)
                            order = await exchange.create_market_sell_order(symbol, TRADE_AMOUNT)
                            print(f"✅ [تم بيع الأصول بنجاح]: {order.get('id', 'OK')}", flush=True)
                            positions[symbol] = False

                except Exception as e:
                    print(f"⚠️ خطأ أثناء التداول على {symbol}: {e}", flush=True)

            await asyncio.sleep(15)

    except Exception as e:
        print(f"❌ خطأ رئيسي في الاتصال بـ OKX: {e}", flush=True)
    finally:
        try:
            await exchange.close()
        except UnboundLocalError:
            pass

async def main():
    await start_web_server()
    await run_price_action_bot()

if __name__ == '__main__':
    asyncio.run(main())
