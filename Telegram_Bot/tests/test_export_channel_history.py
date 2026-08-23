"""Tests for the read-only channel-history exporter.

Everything here runs without Telegram. The parts worth testing are the ones
that decide correctness offline: credential loading, title resolution, record
shaping, resume, and FloodWait recovery.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

import export_channel_history as ex


# ─────────────────────────── fakes ───────────────────────────

class _Dialog:
    def __init__(self, title, did=1):
        self.title = title
        self.id = did
        self.entity = object()


class _ReplyTo:
    def __init__(self, mid):
        self.reply_to_msg_id = mid


class _Msg:
    def __init__(self, mid, text="hi", date=None, edit_date=None,
                 reply_to=None, sender_id=42, fwd_from=None, raw=None):
        self.id = mid
        self.text = text
        self.message = text if raw is None else raw
        self.date = date or datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc)
        self.edit_date = edit_date
        self.reply_to = reply_to
        self.sender_id = sender_id
        self.fwd_from = fwd_from


class _Client:
    """Yields messages with id > min_id, oldest first.

    flood_after: raise FloodWaitError once, after this many messages have been
    yielded in the current pass.
    """

    def __init__(self, msgs, flood_after=None):
        self._msgs = sorted(msgs, key=lambda m: m.id)
        self.flood_after = flood_after
        self.calls = []

    def iter_messages(self, entity, reverse=True, min_id=0):
        self.calls.append(min_id)
        should_flood = self.flood_after is not None
        if should_flood:
            self.flood_after = None  # only once

        async def _gen():
            from telethon.errors import FloodWaitError
            n = 0
            for m in self._msgs:
                if m.id <= min_id:
                    continue
                if should_flood and n == 0 and min_id == 0:
                    pass
                yield m
                n += 1
                if should_flood and n >= 1:
                    raise FloodWaitError(request=None)
        return _gen()


# ─────────────────────────── load_config ───────────────────────────

def _env(**over):
    base = {"TGVIP_API_ID": "35440199", "TGVIP_API_HASH": "abc",
            "TGVIP_PHONE": "+44770000000", "TGVIP_CHANNEL_TITLE": "Neymar | VIP"}
    base.update(over)
    return base


def test_load_config_happy():
    cfg = ex.load_config(_env())
    assert cfg.api_id == 35440199
    assert cfg.channel_title == "Neymar | VIP"


def test_load_config_names_every_missing_key():
    env = _env()
    del env["TGVIP_API_HASH"]
    env["TGVIP_PHONE"] = "   "
    with pytest.raises(ex.ConfigError) as e:
        ex.load_config(env)
    assert "TGVIP_API_HASH" in str(e.value)
    assert "TGVIP_PHONE" in str(e.value)


def test_load_config_rejects_non_numeric_api_id():
    with pytest.raises(ex.ConfigError, match="numeric"):
        ex.load_config(_env(TGVIP_API_ID="not-a-number"))


def test_load_config_has_no_silent_defaults():
    """A fallback to the live bot's credentials is the failure worth preventing."""
    with pytest.raises(ex.ConfigError):
        ex.load_config({})


# ─────────────────────────── pick_dialog ───────────────────────────

def test_pick_dialog_exact():
    want = _Dialog("Neymar | VIP", did=-100123)
    got = ex.pick_dialog([_Dialog("Other"), want], "Neymar | VIP")
    assert got is want


def test_pick_dialog_tolerates_whitespace_and_case():
    want = _Dialog("neymar  |  vip")
    assert ex.pick_dialog([want], "Neymar | VIP") is want


def test_pick_dialog_not_found_lists_what_it_saw():
    with pytest.raises(ex.ChannelNotFound) as e:
        ex.pick_dialog([_Dialog("Something Else")], "Neymar | VIP")
    msg = str(e.value)
    assert "JOINED" in msg               # the actual usual cause
    assert "Something Else" in msg       # so the operator can spot the real title


def test_pick_dialog_refuses_to_guess_when_ambiguous():
    dupes = [_Dialog("Neymar | VIP"), _Dialog("Neymar | VIP")]
    with pytest.raises(ex.AmbiguousChannel):
        ex.pick_dialog(dupes, "Neymar | VIP")


# ─────────────────────────── message_record ───────────────────────────

def test_message_record_shape():
    rec = ex.message_record(_Msg(7, "BUY XAUUSD"))
    assert rec["id"] == 7
    assert rec["text"] == "BUY XAUUSD"
    assert rec["date"] == "2026-08-23T10:00:00+00:00"
    assert rec["reply_to_msg_id"] is None
    assert rec["forwarded"] is False


def test_message_record_keeps_reply_linkage():
    rec = ex.message_record(_Msg(9, "TP1 hit", reply_to=_ReplyTo(7)))
    assert rec["reply_to_msg_id"] == 7


def test_message_record_normalises_timezone_to_utc():
    ist = timezone(timedelta(hours=5, minutes=30))
    rec = ex.message_record(_Msg(1, date=datetime(2026, 8, 23, 15, 30, tzinfo=ist)))
    assert rec["date"] == "2026-08-23T10:00:00+00:00"


def test_message_record_preserves_emoji_verbatim():
    """The 08-08 outage was emoji decoration. An exporter that tidied its input
    would hide exactly the thing phase 2 needs to see."""
    decorated = "BUY 4059 \U0001F525 SL: 4055 ⚠️ TP: 4070"
    rec = ex.message_record(_Msg(3, decorated))
    assert rec["text"] == decorated


def test_message_record_keeps_both_text_and_raw():
    rec = ex.message_record(_Msg(4, "**BUY**", raw="BUY"))
    assert rec["text"] == "**BUY**"   # what live_trader reads
    assert rec["raw"] == "BUY"        # formatting entities not applied


def test_message_record_flags_edits_and_forwards():
    rec = ex.message_record(_Msg(
        5, edit_date=datetime(2026, 8, 23, 11, 0, tzinfo=timezone.utc),
        fwd_from=object()))
    assert rec["edit_date"] == "2026-08-23T11:00:00+00:00"
    assert rec["forwarded"] is True


# ─────────────────────────── resume_point ───────────────────────────

def test_resume_point_missing_file(tmp_path):
    assert ex.resume_point(tmp_path / "nope.jsonl") == 0


def test_resume_point_returns_highest_id(tmp_path):
    p = tmp_path / "h.jsonl"
    p.write_text("\n".join(json.dumps({"id": i}) for i in (3, 11, 7)), encoding="utf-8")
    assert ex.resume_point(p) == 11


def test_resume_point_survives_truncated_final_line(tmp_path):
    """Killed mid-write must cost the current batch, not the whole export."""
    p = tmp_path / "h.jsonl"
    p.write_text(json.dumps({"id": 5}) + "\n" + '{"id": 6, "tex', encoding="utf-8")
    assert ex.resume_point(p) == 5


# ─────────────────────────── export_history ───────────────────────────

def _collect(client, **kw):
    out = []
    written, last = asyncio.run(ex.export_history(
        client, object(), out.append, sleep=_noop_sleep, **kw))
    return out, written, last


async def _noop_sleep(_seconds):
    return None


def test_export_history_writes_all_oldest_first():
    out, written, last = _collect(_Client([_Msg(3), _Msg(1), _Msg(2)]))
    assert [r["id"] for r in out] == [1, 2, 3]
    assert (written, last) == (3, 3)


def test_export_history_respects_start_after():
    out, written, last = _collect(_Client([_Msg(i) for i in (1, 2, 3)]), start_after=2)
    assert [r["id"] for r in out] == [3]
    assert last == 3


def test_export_history_limit_stops_early():
    out, written, last = _collect(_Client([_Msg(i) for i in (1, 2, 3)]), limit=2)
    assert [r["id"] for r in out] == [1, 2]
    assert (written, last) == (2, 2)


def test_export_history_resumes_after_floodwait_without_duplicating():
    """Telegram forces waits on long crawls. The retry must resume ABOVE the
    last id written, not restart the channel."""
    client = _Client([_Msg(i) for i in (1, 2, 3)], flood_after=1)
    out, written, last = _collect(client)
    assert [r["id"] for r in out] == [1, 2, 3]   # no duplicate of id 1
    assert client.calls == [0, 1]               # second pass starts above id 1
    assert (written, last) == (3, 3)
