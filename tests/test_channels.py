"""Channels, and the Telegram bot's authorization policy.

Two things are being defended here.

The first is the bug that started this: ``routines._deliver`` imported
``.channels`` inside a bare ``except Exception: pass`` and the module did not
exist, so ``output: telegram`` silently delivered nothing. A test that only
checked "``_deliver`` doesn't crash" would have passed the entire time the
feature was broken — swallowing the failure *is* the bug. So the test below
asserts the delivery function was actually **called**.

The second is authorization. This is the only code in Milo where a mistake
hands a stranger the agent, so each rule Hermes learned the hard way gets an
explicit case, including the negative ones.
"""

from __future__ import annotations

import pytest

from miloctl import bot, channels

# ── chunking ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("limit", [50, 300, 4096])
def test_chunks_never_exceed_the_limit(limit):
    # Telegram rejects the *whole* message when it is one character over, so
    # "close enough" is indistinguishable from not sending at all.
    text = "\n\n".join(f"paragraph {i} " + "word " * 40 for i in range(30))
    pieces = channels.chunk(text, limit)
    assert pieces, "chunking produced nothing"
    assert all(len(p) <= limit for p in pieces)


def test_chunking_keeps_every_word():
    text = " ".join(f"w{i}" for i in range(2000))
    joined = " ".join(channels.chunk(text, 100)).split()
    assert joined == text.split()


def test_short_text_is_not_split():
    assert channels.chunk("hi", 4096) == ["hi"]


def test_chunking_prefers_a_paragraph_boundary():
    a, b = "A" * 40, "B" * 40
    assert channels.chunk(f"{a}\n\n{b}", 60) == [a, b]


def test_unbreakable_token_still_gets_split():
    # A 500-character URL has no space to break on; refusing to split would
    # push an over-limit chunk to the API.
    pieces = channels.chunk("x" * 500, 100)
    assert all(len(p) <= 100 for p in pieces)
    assert "".join(pieces) == "x" * 500


# ── configured / skipped semantics ────────────────────────────────────────────


def test_unconfigured_channel_is_skipped_not_failed(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(channels, "load_dotenv_get", None, raising=False)
    monkeypatch.setattr(channels.env, "get", lambda k, d="": "")

    res = channels.get("telegram").deliver("hello")
    assert res.skipped is True
    assert res.ok is False
    assert res.status == "skipped"


def test_unconfigured_channel_makes_no_network_call(monkeypatch):
    """A missing token must not produce a request to api.telegram.org/bot/."""
    monkeypatch.setattr(channels.env, "get", lambda k, d="": "")
    calls = []
    monkeypatch.setattr(channels, "_post", lambda *a, **k: calls.append(a) or (True, "", "{}"))

    channels.get("telegram").deliver("hello")
    assert calls == []


def test_send_reports_each_chunk_as_one_delivery(monkeypatch):
    sent = []

    def fake_post(url, payload=None, **kw):
        sent.append(payload)
        return True, "", '{"ok": true}'

    monkeypatch.setattr(channels.env, "get",
                        lambda k, d="": {"TELEGRAM_BOT_TOKEN": "123456:abcdefghijklmnop",
                                         "TELEGRAM_CHAT_ID": "42"}.get(k, ""))
    monkeypatch.setattr(channels, "_post", fake_post)

    res = channels.get("telegram").deliver("word " * 3000)
    assert res.ok
    assert res.parts == len(sent) > 1
    # Multi-part messages are numbered so a dropped middle chunk is visible.
    assert "(1/" in sent[0]["text"]


def test_telegram_ok_false_body_is_a_failure(monkeypatch):
    """HTTP 200 with {"ok": false} is Telegram's way of saying 'chat not found'."""
    monkeypatch.setattr(channels.env, "get",
                        lambda k, d="": {"TELEGRAM_BOT_TOKEN": "123456:abcdefghijklmnop",
                                         "TELEGRAM_CHAT_ID": "42"}.get(k, ""))
    monkeypatch.setattr(channels, "_post",
                        lambda *a, **k: (True, "", '{"ok": false, "description": "chat not found"}'))

    res = channels.get("telegram").deliver("hello")
    assert res.ok is False
    assert "chat not found" in res.detail


# ── redaction ─────────────────────────────────────────────────────────────────


def test_bot_token_never_appears_in_an_error():
    # The token lives in the Telegram URL, so any error echoing the request
    # leaks it straight into a log file.
    token = "8123456789:AAHrandomlookingsecrettokenvalue123"
    leaked = f"failed to POST https://api.telegram.org/bot{token}/sendMessage"
    assert token not in channels._redact(leaked)
    assert "<redacted>" in channels._redact(leaked)


# ── the original bug: routines never reached Telegram ─────────────────────────


def test_routine_with_telegram_output_actually_calls_the_channel(monkeypatch, milo_home):
    """Regression guard for the silent-import bug.

    ``_deliver`` swallows every exception, so if ``miloctl/channels.py`` is
    deleted or ``send_telegram`` is renamed, the ImportError is caught and the
    routine reports success having sent nothing. Asserting the call happened is
    the only version of this test that can fail.
    """
    from miloctl import routines

    calls = []
    monkeypatch.setattr(channels, "send_telegram",
                        lambda text: calls.append(text) or channels.Delivery("telegram", ok=True))

    r = routines.Routine(name="briefing", output="telegram")
    routines._deliver(r, "the morning briefing")

    assert calls == ["the morning briefing"], "routine did not reach the telegram channel"


def test_doctor_names_routines_that_cannot_reach_their_channel(milo_home, monkeypatch):
    """The check that would have surfaced the original bug in under a second."""
    from miloctl.cli import _stranded_routines
    from miloctl.routines import store

    monkeypatch.setattr(channels.env, "get", lambda k, d="": "")
    store().add("morning-briefing", prompt="brief me", output="vault,telegram")

    assert ("morning-briefing", "telegram") in _stranded_routines()


def test_doctor_is_quiet_when_the_channel_is_configured(milo_home, monkeypatch):
    from miloctl.cli import _stranded_routines
    from miloctl.routines import store

    monkeypatch.setattr(channels.env, "get",
                        lambda k, d="": "milo-topic" if k == "NTFY_TOPIC" else "")
    store().add("backup-done", command="echo ok", output="ntfy")

    assert _stranded_routines() == []


def test_doctor_ignores_local_only_outputs(milo_home, monkeypatch):
    """log/vault/memory are not channels and must not be reported as stranded."""
    from miloctl.cli import _stranded_routines
    from miloctl.routines import store

    monkeypatch.setattr(channels.env, "get", lambda k, d="": "")
    store().add("tidy", command="echo ok", output="log,vault,memory")

    assert _stranded_routines() == []


def test_routine_delivery_survives_a_broken_channel(monkeypatch, milo_home):
    """A routine must never fail because the network did."""
    from miloctl import routines

    def boom(text):
        raise RuntimeError("network down")

    monkeypatch.setattr(channels, "send_telegram", boom)
    routines._deliver(routines.Routine(name="briefing", output="telegram"), "text")


# ── authorization (ported from Hermes) ────────────────────────────────────────


def test_empty_allowlist_denies_everyone():
    """Hermes #24457. 'No allowlist' must never mean 'allow all'."""
    allowed, reason = bot.Authorizer(allowed=set()).check({"from": {"id": 12345}})
    assert allowed is False
    assert "allowlist" in reason


def test_allowlisted_user_is_allowed():
    allowed, _ = bot.Authorizer(allowed={"42"}).check({"from": {"id": 42}})
    assert allowed is True


def test_stranger_is_denied():
    allowed, reason = bot.Authorizer(allowed={"42"}).check({"from": {"id": 99}})
    assert allowed is False
    assert "99" in reason


@pytest.mark.parametrize("allow_bots, expected", [(False, False), (True, True)])
def test_bot_accounts_are_denied_unless_explicitly_allowed(allow_bots, expected):
    """Hermes #32188 — an allowlisted *id* that is a bot is still a bot."""
    auth = bot.Authorizer(allowed={"42"}, allow_bots=allow_bots)
    allowed, _ = auth.check({"from": {"id": 42, "is_bot": True}})
    assert allowed is expected


def test_channel_post_is_authorized_by_its_sender_chat():
    """Channel posts carry no ``from``; authorizing only ``from`` lets them in free."""
    auth = bot.Authorizer(allowed={"42"})
    assert auth.check({"sender_chat": {"id": 777}})[0] is False
    assert auth.check({"sender_chat": {"id": 42}})[0] is True


def test_message_with_no_identifiable_sender_is_denied():
    allowed, reason = bot.Authorizer(allowed={"42"}).check({})
    assert allowed is False
    assert "sender" in reason


def test_wildcard_allows_everyone():
    assert bot.Authorizer(allowed={"*"}).check({"from": {"id": 1}})[0] is True


def test_allowlist_from_env_reads_user_ids(monkeypatch):
    monkeypatch.setenv("ALLOWED_USER_IDS", "111, 222 333")
    assert bot.Authorizer.from_env().allowed == {"111", "222", "333"}


def test_positive_chat_id_is_an_implicit_allowlist(monkeypatch):
    monkeypatch.delenv("ALLOWED_USER_IDS", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "5550001")
    assert bot.Authorizer.from_env().allowed == {"5550001"}


def test_group_chat_id_does_not_become_an_allowlist(monkeypatch):
    """A negative id is a group, not a person — it must not widen the allowlist."""
    monkeypatch.delenv("ALLOWED_USER_IDS", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001234567")
    assert bot.Authorizer.from_env().allowed == set()


# ── bot plumbing ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", ["message", "edited_message", "channel_post"])
def test_extract_finds_the_message_whatever_the_update_shape(key):
    assert bot.TelegramBot.extract({key: {"text": "hi"}}) == {"text": "hi"}


def test_extract_ignores_updates_with_nothing_to_read():
    assert bot.TelegramBot.extract({"poll_answer": {}}) is None


def test_unauthorized_message_never_reaches_the_agent(monkeypatch):
    """The whole point of the allowlist."""
    b = bot.TelegramBot(token="123:abc", authorizer=bot.Authorizer(allowed={"42"}))
    monkeypatch.setattr(b, "ask_agent", lambda text: pytest.fail("agent was invoked"))
    monkeypatch.setattr(b, "send", lambda *a, **k: None)

    b.handle({"message": {"from": {"id": 99}, "chat": {"id": 99}, "text": "rm -rf /"}})


def test_http_409_becomes_a_conflict_error(monkeypatch):
    """The 409 must be classified at the transport layer, not just handled above it.

    Without this, ``call()`` could swallow the 409 into a generic error dict and
    the loop would treat a permanent conflict as a retryable blip — the exact
    flapping this is meant to prevent.
    """
    import urllib.error

    def raise_409(*a, **k):
        raise urllib.error.HTTPError("url", 409, "Conflict", {}, None)

    monkeypatch.setattr(bot.urllib.request, "urlopen", raise_409)
    b = bot.TelegramBot(token="123:abc")

    with pytest.raises(bot.ConflictError):
        b.call("getUpdates")


def test_http_401_is_reported_as_a_bad_token(monkeypatch):
    import urllib.error

    def raise_401(*a, **k):
        raise urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(bot.urllib.request, "urlopen", raise_401)
    with pytest.raises(RuntimeError, match="401"):
        bot.TelegramBot(token="123:abc").call("getMe")


def test_conflict_stops_the_loop_instead_of_flapping(monkeypatch):
    """Two pollers on one token steal updates from each other forever."""
    b = bot.TelegramBot(token="123:abc", authorizer=bot.Authorizer(allowed={"42"}))
    monkeypatch.setattr(b, "me", lambda: {"username": "milobot"})

    def conflict():
        raise bot.ConflictError("terminated by other getUpdates request")

    monkeypatch.setattr(b, "poll_once", conflict)
    monkeypatch.setattr(bot.time, "sleep", lambda s: pytest.fail("backed off instead of stopping"))

    assert b.run_forever(max_iterations=5) == 1


def test_bot_refuses_to_start_without_an_allowlist(monkeypatch):
    """Better a clear refusal than a bot that silently denies every message."""
    b = bot.TelegramBot(token="123:abc", authorizer=bot.Authorizer(allowed=set()))
    monkeypatch.setattr(b, "poll_once", lambda: pytest.fail("polled without an allowlist"))
    assert b.run_forever(max_iterations=1) == 1


def test_offset_advances_so_updates_are_not_reprocessed(monkeypatch):
    b = bot.TelegramBot(token="123:abc")
    monkeypatch.setattr(b, "call", lambda *a, **k: {
        "ok": True, "result": [{"update_id": 7}, {"update_id": 9}]})

    b.poll_once()
    assert b.offset == 10


def test_help_and_status_need_no_network(monkeypatch):
    b = bot.TelegramBot(token="123:abc", authorizer=bot.Authorizer(allowed={"1"}))
    monkeypatch.setattr(b, "call", lambda *a, **k: pytest.fail("hit the network"))

    assert "/recall" in b.respond("/help", {})
    assert "channels" in b.respond("/status", {})


def test_group_style_command_mention_is_stripped():
    """In a group Telegram sends '/status@MiloBot', not '/status'."""
    b = bot.TelegramBot(token="123:abc", authorizer=bot.Authorizer(allowed={"1"}))
    assert "channels" in b.respond("/status@MiloBot", {})


def test_unknown_command_is_reported_not_sent_to_the_agent(monkeypatch):
    b = bot.TelegramBot(token="123:abc", authorizer=bot.Authorizer(allowed={"1"}))
    monkeypatch.setattr(b, "ask_agent", lambda text: pytest.fail("agent was invoked"))
    assert "unknown command" in b.respond("/frobnicate", {})
