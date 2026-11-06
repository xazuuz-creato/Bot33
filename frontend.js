'use strict';

// Decisions and accounting are handled by the Python server.
let availableSymbols = [];
let activePairs = [];
let scope = 'top12';
let isDemoMode = true;
let tradeBudget = 100;
let maxTradeDurationSec = 180;
let activeTrades = [];
let completedTrades = [];
let ws = null;
let wsGeneration = 0;
let book = {};
let bookSendAt = {};
let lastEntryCheck = {};
let entryBusy = new Set();
let exitBusy = new Set();
let tradeBuffer = [];
let wsHealthy = false;
let config = null;
let refreshBusy = false;
let lastStateTime = 0;

const $ = id => document.getElementById(id);
const money = n => Number(n || 0).toFixed(3);
const pct = n => Number(n || 0).toFixed(3);

function logMessage(message, type = 'info') {
  const row = document.createElement('div');
  row.className = type === 'danger'
    ? 'text-rose-400'
    : type === 'success'
    ? 'text-emerald-400'
    : 'text-amber-400';

  row.textContent =
    `[${new Date().toLocaleTimeString('ar-SA')}] ${message}`;

  $('consoleLog').prepend(row);

  while ($('consoleLog').children.length > 100) {
    $('consoleLog').lastElementChild.remove();
  }
}

function clearConsole() {
  $('consoleLog').textContent = '';
}

async function api(path, data, method = 'POST') {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 6000);

  try {
    const res = await fetch(path, {
      method,
      headers: {
        'Content-Type': 'application/json'
      },
      ...(data === undefined
        ? {}
        : { body: JSON.stringify(data) }),
      signal: ctrl.signal
    });

    const json = await res.json();

    if (!res.ok) {
      throw new Error(
        typeof json.detail === 'string'
          ? json.detail
          : `HTTP ${res.status}`
      );
    }

    return json;
  } finally {
    clearTimeout(timer);
  }
}

function setMode(demo) {
  if (!demo) {
    logMessage(
      'التداول الحقيقي غير مدعوم في هذه النسخة.',
      'danger'
    );

    alert(
      'هذا الإصدار يدعم Demo فقط ولا يرسل أوامر حقيقية.'
    );
    return;
  }

  isDemoMode = true;
  $('modeBadge').textContent = 'تجريبي فقط (Demo)';
  $('liveBtn').disabled = true;

  $('liveBtn').className =
    'flex-1 py-1.5 bg-darkBg text-slate-500 ' +
    'border border-slate-800 font-bold rounded-xl ' +
    'text-xs opacity-50 cursor-not-allowed';

  $('demoBtn').className =
    'flex-1 py-1.5 bg-emerald-500 text-slate-950 ' +
    'font-bold rounded-xl text-xs';
}

function updateTimeoutValue(val) {
  maxTradeDurationSec = Number(val);

  $('timeoutDisplay').textContent =
    `${maxTradeDurationSec} ثانية ` +
    `(${Math.floor(maxTradeDurationSec / 60)} د)`;
}

function setScope(next) {
  scope = next;

  activePairs = next === 'top12'
    ? availableSymbols.slice(0, 12)
    : availableSymbols.slice(0, 30);

  const activeClass =
    'px-2.5 py-2 bg-emerald-500/20 text-emerald-400 ' +
    'border border-emerald-500/40 rounded-xl text-xs font-bold';

  const inactiveClass =
    'px-2.5 py-2 bg-darkBg text-slate-400 ' +
    'border border-slate-800 rounded-xl text-xs font-bold';

  $('scopeTopBtn').className =
    next === 'top12' ? activeClass : inactiveClass;

  $('scopeAllBtn').className =
    next === 'all' ? activeClass : inactiveClass;

  const monitored = [
    ...new Set([
      ...activePairs,
      ...activeTrades.map(x => x.symbol)
    ])
  ];

  reconnectSockets(monitored);

  logMessage(
    `نطاق المراقبة: ${activePairs.length} زوج Spot متحقق منه.`
  );
}

function reconnectSockets(symbols) {
  const generation = ++wsGeneration;
  wsHealthy = false;

  if (ws) {
    try {
      ws.onclose = null;
      ws.close();
    } catch (e) {}
  }

  ws = null;

  if (!symbols.length) {
    logMessage(
      'لا توجد أزواج Binance متحققة.',
      'danger'
    );
    return;
  }

  const streams = symbols.flatMap(s => [
    `${s.toLowerCase()}@depth5@100ms`,
    `${s.toLowerCase()}@aggTrade`
  ]);

  const url =
    'wss://stream.binance.com:9443/stream?streams=' +
    streams.join('/');

  let socket;

  try {
    socket = new WebSocket(url);
  } catch (err) {
    logMessage(
      'فشل فتح WebSocket: ' + err.message,
      'danger'
    );
    return;
  }

  ws = socket;

  socket.onopen = () => {
    if (generation !== wsGeneration) return;

    wsHealthy = true;

    logMessage(
      'متصل ببث Binance العام.',
      'success'
    );
  };

  socket.onmessage = e => {
    if (generation !== wsGeneration) return;

    let packet;

    try {
      packet = JSON.parse(e.data);
    } catch (_) {
      return;
    }

    const d = packet.data;
    if (!d) return;

    const symbol = (
      d.s ||
      packet.stream?.split('@')[0] ||
      ''
    ).toUpperCase();

    if (d.bids && d.asks) {
      if (!d.bids.length || !d.asks.length) return;

      const bid = Number(d.bids[0][0]);
      const ask = Number(d.asks[0][0]);

      if (!(bid > 0 && ask > bid)) return;

      book[symbol] = {
        bid,
        ask,
        time: Date.now()
      };

      const now = Date.now();

      if (now - (bookSendAt[symbol] || 0) > 300) {
        bookSendAt[symbol] = now;

        api('/api/market-data/book', {
          symbol,
          bids: d.bids,
          asks: d.asks,
          timestamp: now
        }).catch(err =>
          logSparingly(
            symbol + ' book',
            err.message
          )
        );
      }
    } else if (
      d.e === 'aggTrade' &&
      Number(d.p) > 0 &&
      Number(d.q) > 0
    ) {
      tradeBuffer.push({
        symbol,
        price: Number(d.p),
        quantity: Number(d.q),
        isBuyerMaker: Boolean(d.m),
        timestamp: d.T
      });

      if (tradeBuffer.length > 2000) {
        tradeBuffer.splice(
          0,
          tradeBuffer.length - 2000
        );
      }
    }
  };

  socket.onerror = () => {
    wsHealthy = false;
  };

  socket.onclose = () => {
    if (generation !== wsGeneration) return;

    wsHealthy = false;

    logSparingly(
      'websocket',
      'انقطع بث Binance. إعادة الاتصال خلال 3 ثوانٍ.'
    );

    setTimeout(() => {
      if (generation === wsGeneration) {
        reconnectSockets([
          ...new Set([
            ...activePairs,
            ...activeTrades.map(t => t.symbol)
          ])
        ]);
      }
    }, 3000);
  };
}

let lastLogged = {};

function logSparingly(key, msg) {
  if (Date.now() - (lastLogged[key] || 0) > 12000) {
    lastLogged[key] = Date.now();
    logMessage(msg, 'danger');
  }
}

async function flushTrades() {
  if (!tradeBuffer.length) return;

  const batch = tradeBuffer.splice(0, 200);

  try {
    await api('/api/market-data/trades', {
      trades: batch
    });
  } catch (e) {
    logSparingly(
      'trades',
      'فشل إرسال الصفقات المنفذة: ' + e.message
    );
  }
}

async function scanEntries() {
  if (
    !wsHealthy ||
    !config ||
    activeTrades.length >= config.maxPositions
  ) {
    return;
  }

  for (const symbol of activePairs) {
    if (
      activeTrades.some(t => t.symbol === symbol) ||
      entryBusy.has(symbol)
    ) {
      continue;
    }

    if (
      Date.now() - (lastEntryCheck[symbol] || 0) <
      1400
    ) {
      continue;
    }

    if (
      !book[symbol] ||
      Date.now() - book[symbol].time > 2500
    ) {
      continue;
    }

    lastEntryCheck[symbol] = Date.now();
    entryBusy.add(symbol);

    void (async () => {
      try {
        const signal = await api(
          '/api/evaluate-entry',
          { symbol }
        );

        if (!signal.enter || !signal.signalToken) {
          return;
        }

        const budget = Number(
          $('budgetInput').value
        );

        if (
          !(budget >= 10 && budget <= 100000)
        ) {
          logMessage(
            'الميزانية يجب أن تكون بين 10 و100000',
            'danger'
          );
          return;
        }

        const order = await api(
          '/api/execute-order',
          {
            symbol,
            action: 'BUY',
            amountUsdt: budget,
            useDemo: true,
            signalToken: signal.signalToken,
            maxHoldSeconds: maxTradeDurationSec
          }
        );

        if (order.status === 'success') {
          logMessage(
            `فتح تجريبي مؤكد ${symbol} ` +
            `بسعر ${money(order.fillPrice)} ` +
            `والرسوم ${money(order.feeUsdt)} USDT`,
            'success'
          );

          await refreshState();
        }
      } catch (e) {
        logSparingly(
          symbol + ' entry',
          `${symbol}: ${e.message}`
        );
      } finally {
        entryBusy.delete(symbol);
      }
    })();

    if (entryBusy.size >= 3) break;
  }
}

async function trackActiveTrades() {
  for (const trade of [...activeTrades]) {
    if (exitBusy.has(trade.symbol)) continue;

    exitBusy.add(trade.symbol);

    void (async () => {
      try {
        const decision = await api(
          '/api/position/evaluate-exit',
          { symbol: trade.symbol }
        );

        if (decision.reason === 'STALE_DATA') {
          logSparingly(
            trade.symbol + ' stale',
            `${trade.symbol}: بيانات السعر قديمة`
          );
          return;
        }

        if (decision.exit) {
          const sold = await api(
            '/api/execute-order',
            {
              symbol: trade.symbol,
              action: 'SELL',
              useDemo: true,
              reason: decision.reason
            }
          );

          if (sold.status === 'success') {
            const net =
              sold.closedTrade.net_pnl;

            logMessage(
              `إغلاق تجريبي مؤكد ${trade.symbol} ` +
              `بسبب ${decision.reason} | ` +
              `الصافي ${money(net)} USDT`,
              net >= 0 ? 'success' : 'danger'
            );

            await refreshState();
          }
        }
      } catch (e) {
        logSparingly(
          trade.symbol + ' exit',
          `${trade.symbol}: ${e.message}`
        );
      } finally {
        exitBusy.delete(trade.symbol);
      }
    })();
  }
}

async function refreshState() {
  if (refreshBusy) return;

  refreshBusy = true;

  try {
    const s = await api(
      '/api/state',
      undefined,
      'GET'
    );

    activeTrades = s.positions;
    completedTrades = s.history;

    $('balanceDisplay').textContent =
      '$' + Number(s.cashUsdt).toFixed(2);

    renderActiveTrades();
    renderHistoryTrades();
    runAIAnalysis();

    lastStateTime = Date.now();
  } catch (e) {
    logSparingly(
      'state',
      'تعذر مزامنة حالة السيرفر: ' + e.message
    );
  } finally {
    refreshBusy = false;
  }
}

async function fetchBalance() {
  await refreshState();
}

function renderActiveTrades() {
  $('activeCountBadge').textContent =
    activeTrades.length + ' صفقات';

  const tbody = $('activeTradesTbody');
  tbody.textContent = '';

  if (!activeTrades.length) {
    tbody.innerHTML =
      '<tr><td colspan="7" ' +
      'class="py-6 text-center text-slate-500">' +
      'لا توجد مراكز تجريبية مفتوحة.' +
      '</td></tr>';
    return;
  }

  for (const t of activeTrades) {
    const row = document.createElement('tr');
    row.className = 'border-b border-slate-800';

    const p = book[t.symbol];
    const bid = p?.bid || t.entry_price;

    const est =
      (bid / t.entry_price - 1) * 100;

    const cols = [
      t.symbol,
      '$' + money(t.entry_price),
      '$' + money(bid),
      '$' + money(t.entry_cost + t.entry_fee),
      pct(est) + '% إجمالي',
      Math.floor(
        Date.now() / 1000 - t.entry_time
      ) + 'ث / ' + t.max_duration + 'ث',
      'وقف ' + money(t.stop_price) +
      ' | هدف ' + money(t.target_price)
    ];

    cols.forEach((v, i) => {
      const td = document.createElement('td');
      td.className = 'py-3 px-3 font-mono';
      td.textContent = v;

      if (i === 4) {
        td.classList.add(
          est >= 0
            ? 'text-emerald-400'
            : 'text-rose-400'
        );
      }

      row.appendChild(td);
    });

    tbody.appendChild(row);
  }
}

function renderHistoryTrades() {
  const tbody = $('historyTradesTbody');
  tbody.textContent = '';

  const wins = completedTrades.filter(
    t => t.net_pnl > 0
  ).length;

  const sum = completedTrades.reduce(
    (a, t) => a + t.net_pnl,
    0
  );

  const winRate = completedTrades.length
    ? (100 * wins / completedTrades.length).toFixed(1)
    : '0.0';

  $('historyStatsBadge').textContent =
    `الصافي: $${money(sum)} | ` +
    `نسبة النجاح: ${winRate}% ` +
    `(${wins}/${completedTrades.length})`;

  if (!completedTrades.length) {
    tbody.innerHTML =
      '<tr><td colspan="7" ' +
      'class="py-6 text-center text-slate-500">' +
      'لا توجد صفقات مكتملة بعد.' +
      '</td></tr>';
    return;
  }

  for (const t of completedTrades) {
    const row = document.createElement('tr');
    row.className = 'border-b border-slate-800';

    const p =
      (t.exit_price / t.entry_price - 1) * 100;

    const cols = [
      t.symbol,
      '$' + money(t.entry_price),
      '$' + money(t.exit_price),
      pct(p) + '%',
      '$' + money(t.net_pnl),
      Math.round(
        t.closed_at - t.opened_at
      ) + 'ث',
      t.reason
    ];

    cols.forEach((v, i) => {
      const td = document.createElement('td');
      td.className = 'py-3 px-3 font-mono';
      td.textContent = v;

      if (i === 4) {
        td.classList.add(
          t.net_pnl > 0
            ? 'text-emerald-400'
            : 'text-rose-400'
        );
      }

      row.appendChild(td);
    });

    tbody.appendChild(row);
  }
}

function runAIAnalysis() {
  const losses = completedTrades.filter(
    t => t.net_pnl < 0
  ).length;

  const timeout = completedTrades.filter(
    t => t.reason === 'TIMEOUT'
  ).length;

  $('aiAlgoDesc').textContent =
    'الشراء يستلزم ضغط شراء منفذ + عدم توازن ' +
    'سيولة مستقر + زخم إيجابي + فلتر الرسوم ' +
    'والسبريد والانزلاق. الوضع التجريبي فقط.';

  $('aiTriggerDesc').textContent =
    'القرار النهائي للسيرفر. ظهور سيولة كبيرة ' +
    'وحده لا يكفي لدخول صفقة.';

  $('aiErrorsAudit').textContent =
    `التقرير الإحصائي: ${completedTrades.length} ` +
    `صفقة مكتملة؛ ${losses} خاسرة؛ ` +
    `${timeout} خروج زمني. ` +
    'لا يوجد نموذج ذكاء اصطناعي تنبؤي.';
}

function clearHistory() {
  logMessage(
    'السجل محفوظ في قاعدة البيانات ' +
    'ولا يُحذف من الواجهة.',
    'info'
  );
}

window.addEventListener('load', async () => {
  setMode(true);

  try {
    config = await api(
      '/api/config',
      undefined,
      'GET'
    );

    const pairs = await api(
      '/api/symbols',
      undefined,
      'GET'
    );

    availableSymbols = config.symbols.filter(
      s => pairs.symbols.includes(s)
    );

    setScope('top12');

    logMessage(
      `تم التحقق من ${availableSymbols.length} ` +
      'زوج Spot عبر Binance.',
      'success'
    );
  } catch (e) {
    logMessage(
      'لم يبدأ المحرك لأن التحقق من Binance فشل: ' +
      e.message,
      'danger'
    );
  }

  await refreshState();

  if (availableSymbols.length) {
    setScope(scope);
  }

  setInterval(flushTrades, 350);
  setInterval(scanEntries, 1400);
  setInterval(trackActiveTrades, 900);
  setInterval(refreshState, 4000);
  setInterval(renderActiveTrades, 1000);
});
