"""Export a Telegram channel's full message history to JSONL.

Phase 1 of adding "Neymar | VIP" as a second signal source. Deliberately
READ-ONLY: this module imports none of the trading code (no metaapi_orders, no
db_persist, no apis_persist), so it cannot place an order or write to Postgres
however it is invoked.

Spec: docs/superpowers/specs/2026-08-23-neymar-vip-history-export-design.md

Run (interactive the first time — Telegram sends a login code):

    cd Telegram_Bot
    python export_channel_history.py                  # full history
    python export_channel_history.py --limit 50       # smoke test
    python export_channel_history.py --list-dialogs   # just show what we can see

Credentials come from Telegram_Bot/.env under the TGVIP_ prefix. That prefix is
NOT TG2_: in live_trader.py, TG2_* configures a second *broker* account fed by
the same channel, which is a different thing entirely.

The session is written as kronos_tg_vip.session, distinct from the live bot's
kronos_tg.session so the two logins can never overwrite each other. It stays
local — never scp a *.session file to the box.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path

from dotenv import load_dotenv

_HERE = Path(__file__).resolve().parent
load_dotenv(_HERE / ".env", override=False)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tg-export")

SESSION = str(_HERE / "kronos_tg_vip")
EXPORT_DIR = _HERE / "exports"

_WS = re.compile(r"\s+")


class ConfigError(RuntimeError):
    """A required credential is missing. Fail loudly — never fall back."""


class ChannelNotFound(RuntimeError):
    """No dialog matched the requested title."""


class AmbiguousChannel(RuntimeError):
    """More than one dialog matched; refuse to guess."""


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    phone: str
    channel_title: str


def load_config(env: dict | None = None) -> Config:
    """Read TGVIP_* credentials. Raises ConfigError naming every missing key.

    There are deliberately no defaults: a silent fallback to the live bot's
    credentials is exactly the failure mode worth preventing here.
    """
    env = os.environ if env is None else env
    required = ("TGVIP_API_ID", "TGVIP_API_HASH", "TGVIP_PHONE", "TGVIP_CHANNEL_TITLE")
    missing = [k for k in required if not str(env.get(k, "")).strip()]
    if missing:
        raise ConfigError(
            f"missing in Telegram_Bot/.env: {', '.join(missing)}")
    raw_id = str(env["TGVIP_API_ID"]).strip()
    if not raw_id.isdigit():
        raise ConfigError(f"TGVIP_API_ID must be numeric, got {raw_id!r}")
    return Config(int(raw_id), str(env["TGVIP_API_HASH"]).strip(),
                  str(env["TGVIP_PHONE"]).strip(),
                  str(env["TGVIP_CHANNEL_TITLE"]).strip())


def _norm(title: str) -> str:
    """Fold case and collapse whitespace, so 'Neymar | VIP' still matches
    'Neymar  |  VIP'. Used only as a fallback after exact matching."""
    return _WS.sub(" ", (title or "").strip()).casefold()


def pick_dialog(dialogs, title: str):
    """Find the one dialog whose title is `title`.

    Telethon cannot resolve a display title the way it resolves a username or a
    numeric id, so discovery goes through the account's dialog list. Exact match
    wins; a normalised match is the fallback. Zero or many both raise rather
    than guess — picking the wrong channel here would silently poison every
    later phase.
    """
    exact = [d for d in dialogs if getattr(d, "title", None) == title]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise AmbiguousChannel(f"{len(exact)} dialogs titled {title!r}")

    want = _norm(title)
    loose = [d for d in dialogs if _norm(getattr(d, "title", "")) == want]
    if len(loose) == 1:
        return loose[0]
    if len(loose) > 1:
        raise AmbiguousChannel(f"{len(loose)} dialogs matching {title!r}")

    raise ChannelNotFound(
        f"no dialog titled {title!r}. The account must have JOINED the channel. "
        f"Visible: {[getattr(d, 'title', '?') for d in dialogs]}")


def message_record(msg) -> dict:
    """Shape one Telethon message into a lossless JSONL record.

    `text` is msg.text — the same field live_trader.py reads (lines 733, 826) —
    so a parser written against this export sees what the live bot would see.
    `raw` is msg.message, the same content without formatting entities applied,
    kept so phase 2 can tell whether markdown matters.

    Nothing is normalised here. No emoji stripping, no whitespace collapsing:
    the 08-08 outage was caused by emoji decoration, and an exporter that tidied
    its input would have concealed exactly that.
    """
    date = getattr(msg, "date", None)
    edit = getattr(msg, "edit_date", None)
    reply_to = getattr(msg, "reply_to", None)
    return {
        "id": msg.id,
        "date": date.astimezone(timezone.utc).isoformat() if date else None,
        "text": getattr(msg, "text", None),
        "raw": getattr(msg, "message", None),
        "edit_date": edit.astimezone(timezone.utc).isoformat() if edit else None,
        "reply_to_msg_id": getattr(reply_to, "reply_to_msg_id", None),
        "sender_id": getattr(msg, "sender_id", None),
        "forwarded": getattr(msg, "fwd_from", None) is not None,
    }


def resume_point(path: Path) -> int:
    """Highest message id already exported, or 0.

    Telethon's min_id is exclusive, so this doubles as the resume cursor. A
    truncated final line (killed mid-write) is skipped rather than fatal.
    """
    if not Path(path).exists():
        return 0
    highest = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                mid = int(json.loads(line)["id"])
            except (ValueError, KeyError, TypeError):
                continue
            highest = max(highest, mid)
    return highest


async def export_history(client, entity, write_record, *, start_after: int = 0,
                         limit: int | None = None, sleep=asyncio.sleep) -> tuple[int, int]:
    """Walk history oldest-first, calling write_record(dict) per message.

    Returns (written, last_id).

    FloodWaitError is retried, not fatal: Telegram demands a wait on long
    crawls. An async generator cannot be resumed after an exception, so the
    iteration is restarted from the last id written — which is why last_id is
    tracked outside the loop.
    """
    from telethon.errors import FloodWaitError

    written = 0
    last_id = start_after
    while True:
        try:
            async for msg in client.iter_messages(entity, reverse=True, min_id=last_id):
                write_record(message_record(msg))
                last_id = msg.id
                written += 1
                if limit is not None and written >= limit:
                    return written, last_id
            return written, last_id
        except FloodWaitError as e:
            wait = int(getattr(e, "seconds", 0)) + 1
            log.warning("FloodWait: sleeping %ss, resuming above id %s", wait, last_id)
            await sleep(wait)


async def _run(args) -> int:
    from telethon import TelegramClient

    cfg = load_config()
    title = args.title or cfg.channel_title

    client = TelegramClient(SESSION, cfg.api_id, cfg.api_hash)
    await client.start(phone=cfg.phone)
    log.info("Logged in. Session: %s.session", SESSION)

    dialogs = await client.get_dialogs()

    if args.list_dialogs:
        for d in dialogs:
            log.info("  id=%-16s %s", getattr(d, "id", "?"), getattr(d, "title", "?"))
        await client.disconnect()
        return 0

    dialog = pick_dialog(dialogs, title)
    channel_id = getattr(dialog, "id", None)
    log.info("Resolved %r -> numeric id %s  <-- pin this, titles change",
             title, channel_id)

    EXPORT_DIR.mkdir(exist_ok=True)
    out = Path(args.out) if args.out else EXPORT_DIR / f"neymar_vip_{channel_id}.jsonl"

    start_after = resume_point(out)
    if start_after:
        log.info("Resuming above message id %s (%s)", start_after, out.name)

    with open(out, "a", encoding="utf-8") as fh:
        def write_record(rec: dict) -> None:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if rec["id"] % 200 == 0:
                fh.flush()

        written, last_id = await export_history(
            client, dialog.entity, write_record,
            start_after=start_after, limit=args.limit)

    await client.disconnect()
    log.info("Wrote %d messages (through id %s) -> %s", written, last_id, out)
    if written == 0 and start_after == 0:
        log.warning("Zero messages. Either the channel is empty or history is "
                    "restricted for this account.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Export a Telegram channel's history to JSONL")
    p.add_argument("--title", help="Channel display title (default: TGVIP_CHANNEL_TITLE)")
    p.add_argument("--out", help="Output JSONL path (default: exports/neymar_vip_<id>.jsonl)")
    p.add_argument("--limit", type=int, help="Stop after N messages (smoke test)")
    p.add_argument("--list-dialogs", action="store_true",
                   help="List every visible chat and exit — use to find the exact title")
    args = p.parse_args()
    try:
        return asyncio.run(_run(args))
    except (ConfigError, ChannelNotFound, AmbiguousChannel) as e:
        log.error("%s", e)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
