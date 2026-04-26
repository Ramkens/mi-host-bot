# Shards Registry

Multi-account scaling for Mi Host. Each shard = one Render account
that runs a worker (a headless `MIHOST_ROLE=worker` deploy of this
same repo) and hosts a slice of the tenant subprocesses.

## ⚠️ Never put API keys in this file (or anywhere in this repo)

API keys live **encrypted in the `shards` table** (Fernet,
`SECRET_KEY` env var). Use `/shards` in the bot to see the live
list.

This file is a **public log** of which shards exist and where —
purely so you can find the original Render account when you need to.

## How to add a shard

1. Register a fresh Render account (any new email — Gmail aliases
   like `you+shard2@gmail.com` work).
2. Generate an API key:
   <https://dashboard.render.com/u/settings#api-keys>
3. In Telegram, run `/add_shard <name> <RENDER_API_KEY> [capacity=4]`.
   - The bot validates the key, encrypts it, deploys a worker, and
     auto-deletes your message so the key disappears from chat.
4. Note the shard in the table below.

## Shard log

| name      | added       | render account (email) | owner_id           | capacity | notes |
|-----------|-------------|------------------------|--------------------|----------|-------|
| _master_  | 2026-04-26  | supersergei423@gmail.com | tea-d7h51b6gvqtc73eufdu0 | 4        | the bot itself; runs all tenants until shards added |

(Add new rows as you onboard shards. **Do NOT** put API keys here.)

## Master service

- Service ID: `srv-d7n042e8bjmc738isnag`
- URL: <https://mi-host-bot.onrender.com>
- Postgres: rotates automatically every ~27 days
  (see `app/services/db_rotation.py`)

## Useful admin commands

| command | what it does |
|--------|---|
| `/shards` | list all shards with health + load |
| `/add_shard NAME APIKEY [CAP]` | register + auto-deploy a worker |
| `/pause_shard NAME` | stop scheduling new tenants there |
| `/resume_shard NAME` | re-enable scheduling |
| `/drop_shard NAME` | remove from registry (existing tenants orphaned until reassigned) |
| `/rotate_db` | force a Postgres rotation now |
