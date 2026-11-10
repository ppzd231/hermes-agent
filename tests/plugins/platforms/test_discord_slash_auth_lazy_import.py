"""Regression: a lazy discord.py install must reach every Discord sibling.

``check_discord_requirements`` installs discord.py on demand
(``pm.ensure_import``) and used to rebind only ``adapter.py``. A sibling that
cached ``None`` from its own import-time ``except ImportError`` kept that
``None`` for the process, so the first slash command died in the gate with
``AttributeError: 'NoneType' object has no attribute 'DMChannel'``.

The fake module below is the discord.py these tests publish. Authorization
rules are unchanged: a missing install still fails closed, and allowlists
still reject.
"""

from __future__ import annotations

import builtins
import sys
from types import ModuleType, SimpleNamespace

import pytest

from plugins.platforms.discord import adapter as discord_adapter
from plugins.platforms.discord import adapter_slash_auth, adapter_thread_titles
from plugins.platforms.discord.adapter_slash_auth import DiscordSlashAuthMixin
from plugins.platforms.discord.adapter_thread_titles import (
    DiscordThreadTitlesMixin,
    SemanticThreadRenames,
)


def _install_fake_discord(monkeypatch) -> ModuleType:
    module = ModuleType("discord")
    module.DMChannel = type("DMChannel", (), {})
    module.Thread = type("Thread", (), {})
    module.Message = type("Message", (), {})
    module.Intents = type("Intents", (), {})
    module.Interaction = object
    ext = ModuleType("discord.ext")
    commands = ModuleType("discord.ext.commands")
    ext.commands = commands
    module.ext = ext
    monkeypatch.setitem(sys.modules, "discord", module)
    monkeypatch.setitem(sys.modules, "discord.ext", ext)
    monkeypatch.setitem(sys.modules, "discord.ext.commands", commands)
    return module


def _stash_sibling(monkeypatch, name: str, module: ModuleType) -> ModuleType:
    module.discord = None
    monkeypatch.setitem(sys.modules, name, module)
    return module


@pytest.fixture
def late_import(monkeypatch):
    """Facade install after siblings have already cached ``discord = None``."""
    fake = _install_fake_discord(monkeypatch)
    monkeypatch.setattr(discord_adapter, "DISCORD_AVAILABLE", False)
    monkeypatch.setattr(discord_adapter, "discord", None)
    monkeypatch.setattr(discord_adapter, "_define_discord_view_classes", lambda: None)
    monkeypatch.setattr(adapter_slash_auth, "discord", None)
    monkeypatch.setattr(adapter_thread_titles, "discord", None)
    monkeypatch.setattr("pm.ensure_import", lambda _extra: None)

    future = _stash_sibling(
        monkeypatch,
        "plugins.platforms.discord.adapter_future_mixin",
        ModuleType("plugins.platforms.discord.adapter_future_mixin"),
    )
    alias = _stash_sibling(
        monkeypatch,
        "hermes_plugins.platforms__discord.adapter_extra",
        ModuleType("hermes_plugins.platforms__discord.adapter_extra"),
    )
    by_file = ModuleType("flat_adapter_slash_auth")
    by_file.__file__ = "/opt/hermes/plugins/platforms/discord/adapter_slash_auth.py"
    _stash_sibling(monkeypatch, by_file.__name__, by_file)
    untouched = ModuleType("plugins.platforms.slack.adapter")
    untouched.__file__ = "/opt/hermes/plugins/platforms/slack/adapter.py"
    _stash_sibling(monkeypatch, untouched.__name__, untouched)

    assert discord_adapter.check_discord_requirements() is True
    return SimpleNamespace(
        fake=fake,
        future=future,
        alias=alias,
        by_file=by_file,
        untouched=untouched,
    )


class _SlashHost(DiscordSlashAuthMixin):
    """Only the collaborators the slash gate calls."""

    def __init__(self, users=(), channels=(), ignored=(), thread_parent=None):
        self._allowed_user_ids = set(users)
        self._allowed_role_ids = set()
        self._allowed_channels = set(channels)
        self._ignored_channels = set(ignored)
        self._thread_parent = thread_parent

    def _get_parent_channel_id(self, channel):
        return self._thread_parent

    def _discord_channel_keys_from_channel(self, channel, parent_id=None):
        keys = {str(getattr(channel, "id", ""))}
        if parent_id is not None:
            keys.add(str(parent_id))
        return keys

    def _get_allowed_channels(self):
        return self._allowed_channels

    def _get_ignored_channels(self):
        return self._ignored_channels

    def _is_allowed_user(self, user_id, **kwargs):
        return user_id in self._allowed_user_ids


class _TitleHost(DiscordThreadTitlesMixin):
    def __init__(self):
        self._semantic_thread_renames = SemanticThreadRenames()

    def _is_forum_parent(self, parent):
        return False

    def _get_effective_topic(self, channel, is_thread=False):
        return "thread-topic" if is_thread else "channel-topic"


def _dm_interaction(fake, user_id, channel_id="1569367686411198609"):
    channel = fake.DMChannel()
    channel.id = int(channel_id)
    return SimpleNamespace(
        user=SimpleNamespace(id=int(user_id)),
        guild=None,
        guild_id=None,
        channel_id=channel.id,
        channel=channel,
    )


def test_dm_slash_authorizes_after_late_discord_import(late_import):
    host = _SlashHost(users={"100200300"})
    verdict = host._evaluate_slash_authorization(
        _dm_interaction(late_import.fake, "100200300")
    )
    assert verdict == (True, None)
    assert adapter_slash_auth.discord is late_import.fake


def test_late_import_keeps_user_allowlist_enforced(late_import):
    host = _SlashHost(users={"100200300"})
    allowed, reason = host._evaluate_slash_authorization(
        _dm_interaction(late_import.fake, "555555555")
    )
    assert allowed is False
    assert reason == "user not in DISCORD_ALLOWED_USERS / DISCORD_ALLOWED_ROLES"


def test_late_import_keeps_channel_allowlist_enforced(late_import):
    interaction = _dm_interaction(late_import.fake, "100200300")
    interaction.channel = SimpleNamespace(id=999, name="general")
    interaction.channel_id = 999
    allowed, reason = _SlashHost(
        users={"100200300"}, channels={"777"}
    )._evaluate_slash_authorization(interaction)
    assert allowed is False
    assert reason == "channel not in DISCORD_ALLOWED_CHANNELS"


def test_late_import_thread_parent_still_satisfies_channel_allowlist(late_import):
    thread = late_import.fake.Thread()
    thread.id = 999
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=100200300),
        guild=SimpleNamespace(id=1),
        guild_id=1,
        channel_id=999,
        channel=thread,
    )
    allowed, reason = _SlashHost(
        users={"100200300"}, channels={"5555"}, thread_parent="5555"
    )._evaluate_slash_authorization(interaction)
    assert (allowed, reason) == (True, None)


def test_late_import_missing_user_still_rejects(late_import):
    interaction = _dm_interaction(late_import.fake, "100200300")
    interaction.user = None
    allowed, reason = _SlashHost(users={"100200300"})._evaluate_slash_authorization(
        interaction
    )
    assert allowed is False
    assert reason == "missing interaction.user with allowlist configured"


def test_guild_channel_labels_recognize_thread_after_late_import(late_import):
    assert adapter_thread_titles.discord is late_import.fake
    thread = late_import.fake.Thread()
    thread.id = 42
    thread.name = "my-thread"
    thread.parent = SimpleNamespace(name="general")
    thread.guild = SimpleNamespace(name="Guild")
    host = _TitleHost()
    assert host._guild_channel_labels(thread) == (
        "Guild / #general / my-thread",
        "thread-topic",
    )

    channel = SimpleNamespace(id=7, name="general", guild=SimpleNamespace(name="Guild"))
    assert host._guild_channel_labels(channel) == ("Guild / #general", "channel-topic")


def test_publish_reaches_discord_siblings_only(late_import):
    assert late_import.future.discord is late_import.fake
    assert late_import.alias.discord is late_import.fake
    assert late_import.by_file.discord is late_import.fake
    assert late_import.untouched.discord is None


def test_unavailable_discord_stays_fail_closed(monkeypatch):
    """No discord.py at all: the installer reports failure and the gate still raises."""
    monkeypatch.setattr(discord_adapter, "DISCORD_AVAILABLE", False)
    monkeypatch.setattr(discord_adapter, "discord", None)
    monkeypatch.setattr(adapter_slash_auth, "discord", None)
    monkeypatch.setattr("pm.ensure_import", lambda _extra: None)
    original_import = builtins.__import__

    def _block_discord(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "discord" or name.startswith("discord."):
            raise ImportError("discord unavailable for test")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _block_discord)

    assert discord_adapter.check_discord_requirements() is False
    assert adapter_slash_auth.discord is None
    with pytest.raises(AttributeError, match="DMChannel"):
        _SlashHost(users={"100200300"})._evaluate_slash_authorization(
            SimpleNamespace(
                user=SimpleNamespace(id=100200300),
                guild=None,
                guild_id=None,
                channel_id=1,
                channel=SimpleNamespace(id=1),
            )
        )
