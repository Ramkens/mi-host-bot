# Mi Host

Production-grade Telegram bot for hosting **FunPay Cardinal** instances and
**custom Python scripts** as a paid subscription service. Designed for
maximum automation and near-passive operation.

## Highlights

- **Two products**
  - Хостинг FunPay Cardinal — 40 ₽/мес (auto golden_key, autoconfig, autostart, isolated subprocess instance)
  - Хостинг кастомных скриптов — 50 ₽/мес (.zip upload → AI/heuristic risk scan → autoconfig deps & start cmd → autostart)
- **CryptoBot only** — auto invoice (RUB→USDT), webhook + manual fallback, 30-day subscription
- **Mini-game** — every 12h while subscribed, +1 day to all active subs (with anti-abuse)
- **Channel autopilot** (when bot is admin) — auto avatar / title / description / pinned welcome, scheduled content (posts, reviews, cases, updates, sales triggers), random rotation, OpenAI-aware
- **Sales funnel** — expiry reminders, churn re-engagement, unpaid-invoice nudges
- **In-bot admin panel** — stats (revenue / conversion / retention), broadcast, in-bot promote/demote admin, channel branding & one-shot post
- **Referrals + levels + coins + caching + rate limit + anti-abuse**
- **24/7 keep-alive** via cron-job.org pinging `/healthz`
- **Tenant supervisor** — each user instance is an isolated subprocess with rlimits, log tail, auto-restart with backoff, golden_key live rotation

Time zone: Europe/Moscow (MSK, UTC+3) everywhere.

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for the full picture. TL;DR:

```
┌─────────────────────── Render Web Service ───────────────────────┐
│                                                                  │
│   FastAPI ─┬─ /healthz                                           │
│            ├─ /tg/webhook        ←  Telegram updates             │
│            └─ /webhooks/cryptobot ←  CryptoBot invoice paid      │
│                                                                  │
│   aiogram Dispatcher                                             │
│   ├─ DB middleware (per-update SQLAlchemy session)               │
│   ├─ Throttle middleware                                         │
│   └─ Routers: start, payment, instances, cardinal, script,       │
│               minigame, referral, admin, support                 │
│                                                                  │
│   APScheduler (MSK) — autoposts, funnel reminders                │
│                                                                  │
│   Tenant Supervisor (asyncio + subprocess)                       │
│   ├─ Cardinal #1 (env GOLDEN_KEY=…)  ← /var/data/mihost/cardinal/1/   │
│   ├─ Cardinal #2 (env GOLDEN_KEY=…)  ← /var/data/mihost/cardinal/2/   │
│   ├─ Script #3   (custom cmd)        ← /var/data/mihost/scripts/3/    │
│   └─ …                                                            │
│                                                                  │
│   Render PostgreSQL (separate service) — users, subs, payments,  │
│                                          instances, logs, content │
└──────────────────────────────────────────────────────────────────┘
        ↑                                   ↑
        │                                   │
   cron-job.org                       Telegram (set_webhook)
  (GET /healthz every 5m)
```

## Project layout

```
app/
├── main.py             FastAPI lifecycle & webhook entrypoints
├── bot.py              aiogram Bot/Dispatcher singletons
├── config.py           pydantic-settings (.env → typed config)
├── scheduler.py        APScheduler tasks (autoposts, funnel)
├── db/                 SQLAlchemy base + models + bootstrap
├── repos/              repository pattern (users, subs, payments, …)
├── services/
│   ├── supervisor.py   tenant subprocess supervisor (rlimits, restart, log tail)
│   ├── cardinal.py     FunPay Cardinal provisioning
│   ├── script_host.py  custom-script provisioning
│   ├── code_analyzer.py  signature scan + bandit
│   ├── auto_setup.py   derive build/start cmd + env from analysis
│   ├── payment.py      CryptoBot client (RUB→USDT, invoices, polling)
│   ├── render_api.py   Render API (services/postgres/deploys)
│   ├── cron.py         cron-job.org client
│   ├── content_gen.py  channel content generator (templates + optional OpenAI)
│   ├── images.py       Pillow brand assets
│   ├── channel.py      auto branding + scheduled posting
│   ├── funnel.py       expiry/churn/unpaid reminders
│   ├── admin.py        stats dashboard
│   ├── cache.py        in-mem TTL cache
│   ├── ratelimit.py    per-user token bucket
│   └── antiabuse.py    heuristics
├── handlers/           aiogram routers
├── keyboards/          inline keyboards
├── middlewares/        DB session, throttling
└── webhooks/           CryptoBot HTTP webhook
assets/                 generated brand images (menu/order/profile/notifications/minigame/avatar)
render.yaml             Render Blueprint (web + Postgres + persistent disk)
.env.example            full env reference
```

## Local run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in values
python -m app.scripts.gen_assets  # optional: pre-render brand images
python -m app.main
```

In a second shell, expose port 8000 and set the Telegram webhook to it
(or run with PUBLIC_URL=http://localhost:8000 to skip webhook setup —
in that case the bot is only reachable via long-polling, see scripts).

## Deploy on Render

The repo includes [`render.yaml`](render.yaml) so you can use Render
Blueprints (1-click). The bot is also deployable manually — see
[`docs/deploy.md`](docs/deploy.md).

After first deploy:
1. Set the secret env vars: `BOT_TOKEN`, `ADMIN_IDS`, `CRYPTOBOT_TOKEN`,
   `RENDER_API_KEY`, `CRONJOB_API_KEY`, optionally `CHANNEL_ID`.
2. Render will provision Postgres and inject `DATABASE_URL` automatically.
3. The bot sets the Telegram webhook on boot using `PUBLIC_URL`.
4. cron-job.org pings `/healthz` every 5 min to keep the instance warm.
5. `/start` in Telegram → main menu with inline buttons.

## Admin actions

- `/menu` — main menu
- `/stats` — quick stats
- `/addadmin <user_id>` — promote a user to admin (also available as a
  button in the in-bot admin panel)
- `/rmadmin <user_id>` — demote
- Admin panel: stats, broadcast, brand channel, post now, add admin

## Telegram channel autopilot

Make the bot an admin in your channel, set `CHANNEL_ID` (e.g. `-100…`).
On startup the admin panel button «Брендировать канал» sets the avatar,
title, description, and pinned welcome. APScheduler then publishes 3–5
posts/day (mix of selling posts, reviews, cases, updates, sale triggers)
in MSK timezone. With `OPENAI_API_KEY` set, content is generated via GPT;
otherwise from a templated rotation bank.

## Caveats

- 1000+ tenants on a single Render Starter is the soft ceiling; once it's
  reached, spin up a second Render service ("shard") and route new tenants
  to it (the DB models reserve the field).
- Cardinal's upstream code is cloned from
  https://github.com/sidor0912/FunPayCardinal at first start; pinned by
  HEAD by default (extend `app/services/cardinal.py` to pin a tag).
- The "AI risk scan" is a deliberately strict heuristic + bandit pipeline,
  not a full sandbox. Combine with subscription gating (only paying users
  can deploy) for an additional defense layer.

## License

Proprietary. © Mi Host.
