---
name: deploying-telegram-copytrader
description: Use when deploying, redeploying, or restarting the NeymarGoldTrader Telegram copy-trader (Telegram_Bot/live_trader.py, parse_signals.py, metaapi_orders.py) to the live algorobos AWS box, or when the live bot needs a code change pushed to production.
---

# Deploying the Telegram Copy-Trader to algorobos

## Overview
The `Telegram_Bot` copy-trader runs as a Docker Compose service on the prod box. Its code is **baked into the image at build time** — not bind-mounted — so a plain restart does NOT pick up code changes. You must sync the source files and **rebuild**. This is a live money-trading bot: confirm deploy scope with the operator before shipping behavioral changes.

## The box (verified facts)
- Host: `ubuntu@13.126.204.82` (Lightsail `algorobos`).
- SSH key: `~/.ssh/algobet-ssh.pem`. **NOT** `algobet-ls.pem` (that key errors `libcrypto`).
- Always pass `ssh -F /dev/null` — the local `~/.ssh/config` has an option this SSH build rejects (`Bad configuration option: serveralivecountinterval`), which aborts every connection otherwise.
- The box checkout `/home/ubuntu/KronosStrategies` is **NOT a git repo** → deploy by `scp`, not `git pull`.
- Compose project name is **`kronos`** (container `kronos-telegram_trader-1`). The default would be `kronosstrategies` → a **second, duplicate bot = double trading**. ALWAYS pass `-p kronos`.
- Service: `build: ./Telegram_Bot`, `COPY . .`. Telegram session is in the named volume `tg_session` (`/app/session`), so rebuild/recreate preserves the login.
- Live channel: `TG_CHANNEL=NeymarGoldTrader`. Live trading = `DRY_RUN=False`.

## Classifier / permission notes
- `Bash(ssh:*)` / `Bash(scp:*)` are pre-allowed and an autoMode pre-authorization names this host. Use **single clean** ssh commands — a multi-key `for`-loop ("trying multiple keys") gets denied.
- NEVER dump `/proc/<pid>/environ` or otherwise read the running process's env — it leaks live secrets (META/REDIS/TG2 creds) and is correctly blocked. You don't need them to deploy.
- NEVER scp `*.session*` or `.env` to the box.

## Deploy steps
```bash
# 0. Commit + push the branch (github remote is the tracked upstream)
git push github <branch>

# 1. From repo root: find which source files actually changed
cd Telegram_Bot && md5sum *.py init_schema.sql
ssh -F /dev/null -i ~/.ssh/algobet-ssh.pem ubuntu@13.126.204.82 \
  "cd /home/ubuntu/KronosStrategies/Telegram_Bot && md5sum *.py init_schema.sql"

# 2. scp ONLY the differing source files (example: 3 files)
scp -F /dev/null -i ~/.ssh/algobet-ssh.pem -o StrictHostKeyChecking=accept-new \
  parse_signals.py live_trader.py metaapi_orders.py \
  ubuntu@13.126.204.82:/home/ubuntu/KronosStrategies/Telegram_Bot/
# then re-md5 on the box to confirm they match local

# 3. Rebuild + recreate (ALWAYS -p kronos)
ssh -F /dev/null -i ~/.ssh/algobet-ssh.pem ubuntu@13.126.204.82 \
  "cd /home/ubuntu/KronosStrategies && sudo docker compose -p kronos build telegram_trader \
   && sudo docker compose -p kronos up -d telegram_trader"
```

## Verify (do not claim success without this)
```bash
ssh -F /dev/null -i ~/.ssh/algobet-ssh.pem ubuntu@13.126.204.82 \
  "cd /home/ubuntu/KronosStrategies && sudo docker compose -p kronos logs --tail=30 telegram_trader"
# Expect: "Listening to NeymarGoldTrader (DRY_RUN=False)" and
#         "Fanning out each signal to accounts: ['primary', 'neymar2']", no traceback.

# Prove the NEW code is the running code (not a cached image):
ssh -F /dev/null -i ~/.ssh/algobet-ssh.pem ubuntu@13.126.204.82 \
  "sudo docker exec kronos-telegram_trader-1 python -c \"<import + assert the change>\""
```

## Rollback
- Re-scp the previous file versions and rebuild, or `git checkout <prev>` locally then redeploy.
- Fast safety stop: uncomment `DRY_RUN: \"true\"` under the service in `compose.yml` and `up -d` — bot runs but places no real orders. (`down` stops it entirely.)

## Common mistakes
- Restarting without rebuilding → old code keeps running (code is baked in, not mounted).
- Omitting `-p kronos` → spins up a duplicate stack and double-trades.
- Using `algobet-ls.pem` or omitting `-F /dev/null` → connection fails before you reach the box.
- `git pull` on the box → fails (not a git repo); use scp.
- scp'ing the whole dir including `*.session` → can clobber the Telegram login.
