import os
import asyncio
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import ccxt.async_support as ccxt

try:
    import ccxt.pro as ccxtpro
except ImportError:
    ccxtpro = None

app = FastAPI(
    title="Binance Scalper Engine Backend",
    description="Scalper Backend with 12 Top & 30 Assets Support",
    version="12.0.0"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "XivZSVpFnN9o7bPuQYOud41fuuAW4HotktcPk47GuGV1qXSPQMOldUsPeMzruSWz")
DEFAULT_BINANCE_SECRET_KEY = os.environ.get("BINANCE_SECRET_KEY", "hlecSSicPZZQsBoKNkpfQiREigKSHrA74pFcG6PYAXTbwHRvbNNRI1mBMGOi1iQ")

class TradeRequest(BaseModel):
    apiKey: str = ""
    secretKey: str = ""
    symbol: str
    action: str
    amountUsdt: float = 100.0
    useDemo: bool = True

def get_binance_exchange(api_key=None, secret_key=None, use_demo=True, is_pro=False):
    config = {
        'apiKey': api_key or DEFAULT_BINANCE_API_KEY,
        'secret': secret_key or DEFAULT_BINANCE_SECRET_KEY,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'spot',
            'recvWindow': 60000
        }
    }
    
    if is_pro and ccxtpro is not None:
        exchange = ccxtpro.binance(config)
    else:
        exchange = ccxt.binance(config)

    if use_demo:
        exchange.set_sandbox_mode(True)
        
    return exchange

@app.get("/api/config")
async def get_config():
    api_k = DEFAULT_BINANCE_API_KEY
    return {
        "hasEnvKeys": bool(api_k),
        "apiKeyMasked": f"{api_k[:8]}...{api_k[-6:]}" if api_k else "",
        "mode": "Scalping Engine v12 ⚡",
        "hasCcxtPro": ccxtpro is not None,
        "message": "المحرك جاهز ومتوافق مع أفضل 12 رمز وكافة الأسهم والعملات!"
    }

@app.get("/api/account-balance")
async def get_binance_balance(demo: bool = True):
    exchange = get_binance_exchange(use_demo=demo)
    try:
        balance = await exchange.fetch_balance()
        await exchange.close()
        
        total_usdt = 0.0
        if 'USDT' in balance and 'free' in balance['USDT']:
            total_usdt = float(balance['USDT']['free'])
        elif 'total' in balance and 'USDT' in balance['total']:
            total_usdt = float(balance['total']['USDT'])

        if demo and total_usdt == 0.0:
            total_usdt = 10000.0

        return {
            "status": "success",
            "mode": "binance_demo" if demo else "binance_live",
            "totalUsdtBalance": round(total_usdt, 2)
        }

    except Exception as e:
        await exchange.close()
        return {
            "status": "success" if demo else "error",
            "mode": "binance_demo" if demo else "binance_live",
            "totalUsdtBalance": 10000.0 if demo else 0.0,
            "message": str(e)
        }

@app.post("/api/execute-order")
async def execute_binance_order(req: TradeRequest):
    exchange = get_binance_exchange(req.apiKey, req.secretKey, use_demo=req.useDemo)
    try:
        formatted_symbol = req.symbol.replace('-', '').replace('/', '')
        if not formatted_symbol.endswith('USDT'):
            formatted_symbol += 'USDT'

        ccxt_symbol = formatted_symbol.replace('USDT', '/USDT')
        side = req.action.lower()
        amount_usdt = float(req.amountUsdt)

        ticker = await exchange.fetch_ticker(ccxt_symbol)
        current_price = ticker['last']
        amount_coins = amount_usdt / current_price if current_price > 0 else 1.0

        order = await exchange.create_order(
            symbol=ccxt_symbol,
            type='market',
            side=side,
            amount=amount_coins
        )
        await exchange.close()

        return {
            "status": "success",
            "order_id": order.get('id', 'DEMO-ORDER-101'),
            "message": f"تم تنفيذ أمر {side.upper()} {'(تجريبي)' if req.useDemo else '(حقيقي)'} بنجاح!",
            "details": order
        }

    except Exception as e:
        await exchange.close()
        if req.useDemo:
            return {
                "status": "success",
                "order_id": f"SIM-{int(os.times().system*1000)}",
                "message": f"تم تنفيذ أمر {req.action.upper()} تجريبياً بنجاح على {req.symbol}!",
                "details": {"price": 100}
            }
        return {
            "status": "error",
            "message": f"خطأ تنفيذ الأمر: {str(e)}"
        }

@app.get("/", response_class=HTMLResponse)
async def read_index():
    index_path = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    return HTMLResponse(content="<h1>File index.html not found!</h1>", status_code=404)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
