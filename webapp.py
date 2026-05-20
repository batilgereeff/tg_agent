from __future__ import annotations

import json
from aiohttp import web

import database as db

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Дашборд задач</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  :root {
    --bg: #1c1c1e;
    --card: #2c2c2e;
    --text: #ffffff;
    --muted: #8e8e93;
    --done: #30d158;
    --progress: #0a84ff;
    --new: #636366;
    --review: #ff9f0a;
    --overdue: #ff453a;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--tg-theme-bg-color, var(--bg));
    color: var(--tg-theme-text-color, var(--text));
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    padding: 16px;
    min-height: 100vh;
  }
  h2 { font-size: 20px; font-weight: 700; margin-bottom: 4px; }
  .subtitle { font-size: 13px; color: var(--muted); margin-bottom: 20px; }
  .card {
    background: var(--tg-theme-secondary-bg-color, var(--card));
    border-radius: 14px;
    padding: 16px;
    margin-bottom: 14px;
  }
  .card-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 12px;
  }
  .totals-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
    margin-bottom: 14px;
  }
  .stat-box {
    background: var(--tg-theme-secondary-bg-color, var(--card));
    border-radius: 12px;
    padding: 12px 8px;
    text-align: center;
  }
  .stat-num { font-size: 28px; font-weight: 700; }
  .stat-label { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .done-color   { color: var(--done); }
  .prog-color   { color: var(--progress); }
  .over-color   { color: var(--overdue); }
  .new-color    { color: var(--new); }
  .review-color { color: var(--review); }
  .chart-wrap { position: relative; height: 220px; }
  .emp-row { margin-bottom: 14px; }
  .emp-row:last-child { margin-bottom: 0; }
  .emp-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px; }
  .emp-name { font-size: 15px; font-weight: 500; }
  .emp-pct { font-size: 15px; font-weight: 700; color: var(--done); }
  .bar-bg { background: rgba(255,255,255,0.08); border-radius: 6px; height: 8px; overflow: hidden; }
  .bar-fill { height: 100%; border-radius: 6px; transition: width 0.6s ease;
              background: linear-gradient(90deg, #0a84ff, #30d158); }
  .emp-meta { font-size: 12px; color: var(--muted); margin-top: 4px; }
  .loading { text-align: center; padding: 40px; color: var(--muted); }
  .refresh-btn {
    width: 100%;
    padding: 13px;
    border: none;
    border-radius: 12px;
    background: var(--tg-theme-button-color, #0a84ff);
    color: var(--tg-theme-button-text-color, #fff);
    font-size: 15px;
    font-weight: 600;
    cursor: pointer;
    margin-top: 4px;
  }
</style>
</head>
<body>
<h2>Дашборд задач</h2>
<div class="subtitle" id="updated">Загрузка...</div>

<div class="totals-grid">
  <div class="stat-box"><div class="stat-num" id="t-total">—</div><div class="stat-label">Всего</div></div>
  <div class="stat-box"><div class="stat-num done-color" id="t-done">—</div><div class="stat-label">Выполнено</div></div>
  <div class="stat-box"><div class="stat-num over-color" id="t-over">—</div><div class="stat-label">Просрочено</div></div>
</div>
<div class="totals-grid">
  <div class="stat-box"><div class="stat-num new-color" id="t-new">—</div><div class="stat-label">Новые</div></div>
  <div class="stat-box"><div class="stat-num prog-color" id="t-prog">—</div><div class="stat-label">В работе</div></div>
  <div class="stat-box"><div class="stat-num review-color" id="t-rev">—</div><div class="stat-label">На проверке</div></div>
</div>

<div class="card">
  <div class="card-title">Распределение задач</div>
  <div class="chart-wrap"><canvas id="barchart"></canvas></div>
</div>

<div class="card">
  <div class="card-title">По сотрудникам</div>
  <div id="emp-list"><div class="loading">Загрузка...</div></div>
</div>

<button class="refresh-btn" onclick="loadData()">Обновить</button>

<script>
let chart = null;

async function loadData() {
  try {
    const r = await fetch('/api/stats');
    const data = await r.json();
    renderStats(data);
  } catch(e) {
    document.getElementById('updated').textContent = 'Ошибка загрузки';
  }
}

function renderStats(data) {
  const t = data.totals;
  document.getElementById('t-total').textContent = t.total;
  document.getElementById('t-done').textContent  = t.done;
  document.getElementById('t-over').textContent  = t.overdue;
  document.getElementById('t-new').textContent   = t.new;
  document.getElementById('t-prog').textContent  = t.in_progress;
  document.getElementById('t-rev').textContent   = t.review;

  const pct = t.total > 0 ? Math.round(t.done / t.total * 100) : 0;
  document.getElementById('updated').textContent =
    `Выполнено ${pct}% · обновлено ${new Date().toLocaleTimeString('ru')}`;

  renderDonut(t);
  renderEmployees(data.by_employee);
}

function renderDonut(t) {
  const ctx = document.getElementById('barchart').getContext('2d');
  const labels = ['Выполнена','В работе','Новая','На проверке','Просрочена'];
  const values = [t.done, t.in_progress, t.new, t.review, t.overdue];
  const colors = ['#30d158','#0a84ff','#636366','#ff9f0a','#ff453a'];
  const total  = values.reduce((a,b) => a+b, 0);

  if (chart) { chart.destroy(); }
  chart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: colors,
        borderRadius: 8,
        borderSkipped: false,
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const pct = total > 0 ? Math.round(ctx.parsed.x / total * 100) : 0;
              return ` ${ctx.parsed.x} задач (${pct}%)`;
            }
          }
        }
      },
      scales: {
        x: {
          ticks: { color: '#8e8e93', stepSize: 1 },
          grid: { color: 'rgba(255,255,255,0.06)' },
        },
        y: {
          ticks: { color: '#ffffff', font: { size: 13 } },
          grid: { display: false },
        }
      }
    }
  });
}

function renderEmployees(employees) {
  const el = document.getElementById('emp-list');
  if (!employees || employees.length === 0) {
    el.innerHTML = '<div class="loading">Нет данных</div>';
    return;
  }
  el.innerHTML = employees.map(e => {
    const total = e.total || 0;
    const done  = e.done_count || 0;
    const pct   = total > 0 ? Math.round(done / total * 100) : 0;
    return `
      <div class="emp-row">
        <div class="emp-header">
          <span class="emp-name">${e.name}</span>
          <span class="emp-pct">${pct}%</span>
        </div>
        <div class="bar-bg"><div class="bar-fill" style="width:${pct}%"></div></div>
        <div class="emp-meta">${done} из ${total} · в работе ${e.in_progress_count || 0} · просрочено ${e.overdue_count || 0}</div>
      </div>`;
  }).join('');
}

if (window.Telegram && window.Telegram.WebApp) {
  window.Telegram.WebApp.ready();
  window.Telegram.WebApp.expand();
}
loadData();
</script>
</body>
</html>"""


async def handle_index(request: web.Request) -> web.Response:
    return web.Response(text=DASHBOARD_HTML, content_type="text/html")


async def handle_stats(request: web.Request) -> web.Response:
    stats = await db.get_team_stats()
    return web.Response(
        text=json.dumps(stats, ensure_ascii=False),
        content_type="application/json",
    )


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/stats", handle_stats)
    return app
