# ai_grid/qa/tests/test_messenger.py
"""
Targeted Async Unit Test Suite for MCP Grid Messenger Bot (Priority 11).
Tests ephemeral IRC client lifecycle, unique nick/realname branding, CTCP queries,
polite pacing delays (3.0-5.0s), network resolution, escalating fees, reply windows,
command routing, and help parity.
Uses in-memory SQLite and mock stream harnesses without raw TCP socket binds.
"""

import asyncio
import datetime
import os
import re
import time
import unittest
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_grid.models import (
    Base,
    Character,
    Player,
    NetworkAlias,
    GridNode,
    Memo,
    MessengerDispatch,
)
from ai_grid.core.messenger import (
    MCPMessengerClient,
    resolve_target_network,
    CTCP_VERSION_REPLY,
)
from ai_grid.database.repositories.messenger_repo import (
    MessengerRepository,
    BASE_COURIER_CREDITS,
    BASE_COURIER_POWER,
)
from ai_grid.core.handlers.messenger import handle_messenger
import ai_grid.core.handlers as handlers
from ai_grid.core.handlers.base import handle_help


class MockStreamHarness:
    """
    Offline in-memory harness simulating an IRC network connection.
    Connects StreamReader and StreamWriter without opening raw TCP sockets.
    """

    def __init__(self):
        self.client_reader = asyncio.StreamReader()
        self.client_written: List[str] = []
        self._write_event = asyncio.Event()

        self.client_writer = MagicMock()
        self.client_writer.is_closing = MagicMock(return_value=False)
        self.client_writer.close = MagicMock()
        self.client_writer.wait_closed = AsyncMock()

        def fake_write(data: bytes):
            decoded = data.decode("utf-8", errors="ignore")
            for line in decoded.split("\r\n"):
                if line:
                    self.client_written.append(line)
                    self._write_event.set()

        self.client_writer.write = fake_write
        self.client_writer.drain = AsyncMock()

    def feed_server_line(self, line: str):
        self.client_reader.feed_data(f"{line}\r\n".encode("utf-8"))

    def feed_welcome(self, nick: str):
        self.feed_server_line(f":mock.server 001 {nick} :Welcome to the Mock IRC Network {nick}")

    async def wait_for_client_line(self, contains: str, timeout: float = 2.0) -> Optional[str]:
        start = time.time()
        while time.time() - start < timeout:
            for line in self.client_written:
                if contains in line:
                    return line
            self._write_event.clear()
            try:
                rem = timeout - (time.time() - start)
                if rem > 0:
                    await asyncio.wait_for(self._write_event.wait(), timeout=min(0.05, rem))
            except asyncio.TimeoutError:
                pass
        return None


class MockGridNode:
    """Mock Grid Node simulating the GridManager orchestrator."""

    def __init__(self, async_session_factory, net_name="2600net"):
        self.net_name = net_name
        self.prefix = "!a"
        self.admins = ["admin_alice", "r0d3nt"]
        self.nickserv_verified = {"admin_alice", "r0d3nt"}
        self.sent_messages: List[str] = []
        self.messenger_cooldowns = {}
        self.config = {
            "channel": "#AutomataGrid",
            "nickname": "GridManager",
            "networks": {
                "2600net": {
                    "enabled": True,
                    "server": "irc.scuttled.net",
                    "port": 6697,
                    "ssl": True,
                    "channel": "#AutomataGrid",
                },
                "rizon": {
                    "enabled": True,
                    "server": "irc.rizon.net",
                    "port": 6697,
                    "ssl": True,
                    "channel": "#AutomataGrid",
                },
            },
        }

        # Wire database
        from ai_grid.grid_db import ArenaDB
        self.db = ArenaDB(":memory:")
        self.db.async_session = async_session_factory
        self.db.messenger = MessengerRepository(async_session_factory)

    def check_admin_privilege(self, nick: str):
        if nick.lower() in [a.lower() for a in self.admins]:
            return True, "AUTHORIZED"
        return False, "NOT_ADMIN"

    def is_admin(self, nick: str) -> bool:
        auth, _ = self.check_admin_privilege(nick)
        return auth

    async def send(self, message: str):
        self.sent_messages.append(message)


class TestMCPMessengerClientIdentityAndCTCP(unittest.IsolatedAsyncioTestCase):
    """Verifies R1.1 Identity branding and strict CTCP compliance."""

    async def test_unique_id_and_nick_branding(self):
        client1 = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#arena",
            message="Test message",
            sender_nick="Alice",
            source_network="2600net",
        )
        client2 = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#arena",
            message="Test message",
            sender_nick="Bob",
            source_network="2600net",
        )

        # Unique 4-character ID
        self.assertEqual(len(client1.unique_id), 4)
        self.assertEqual(len(client2.unique_id), 4)
        self.assertNotEqual(client1.unique_id, client2.unique_id)

        # Nickname format GridMessenger<uniqueID>
        self.assertTrue(client1.nickname.startswith("GridMessenger"))
        self.assertEqual(len(client1.nickname), len("GridMessenger") + 4)
        self.assertEqual(client1.nickname, f"GridMessenger{client1.unique_id}")

        # Realname format MCP Courier <uniqueID>
        self.assertEqual(client1.realname, f"MCP Courier {client1.unique_id}")

        # Test registration commands sent on connect
        harness = MockStreamHarness()
        client1.reader = harness.client_reader
        client1.writer = harness.client_writer

        await client1.connect()
        self.assertIn(f"NICK {client1.nickname}", harness.client_written)
        self.assertIn(f"USER GridMessenger 0 * :MCP Courier {client1.unique_id}", harness.client_written)
        await client1.close()

    async def test_ctcp_version_reply(self):
        harness = MockStreamHarness()
        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#arena",
            message="Hello",
            sender_nick="Alice",
            source_network="2600net",
            reader=harness.client_reader,
            writer=harness.client_writer,
        )
        await client.connect()

        # Inbound CTCP VERSION query directed at courier
        query_line = f":CuriousUser!u@host PRIVMSG {client.nickname} :\x01VERSION\x01"
        await client._process_line(query_line)

        # Verify NOTICE reply
        expected = f"NOTICE CuriousUser :{CTCP_VERSION_REPLY}"
        self.assertIn(expected, harness.client_written)

        # Verify strict branding: advertises 2600net and https://scuttled.net
        self.assertIn("2600net", CTCP_VERSION_REPLY)
        self.assertIn("https://scuttled.net", CTCP_VERSION_REPLY)
        # Verify zero leaks of Python version or operating environment
        self.assertNotIn("Python", CTCP_VERSION_REPLY)
        self.assertNotIn("CPython", CTCP_VERSION_REPLY)
        self.assertNotIn("darwin", CTCP_VERSION_REPLY.lower())
        self.assertNotIn("linux", CTCP_VERSION_REPLY.lower())
        await client.close()

    async def test_ctcp_time_reply(self):
        harness = MockStreamHarness()
        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#arena",
            message="Hello",
            sender_nick="Alice",
            source_network="2600net",
            reader=harness.client_reader,
            writer=harness.client_writer,
        )
        await client.connect()

        query_line = f":TimeChecker!u@host PRIVMSG {client.nickname} :\x01TIME\x01"
        await client._process_line(query_line)

        # Verify NOTICE reply contains TIME and UTC
        time_notices = [l for l in harness.client_written if l.startswith("NOTICE TimeChecker :\x01TIME")]
        self.assertEqual(len(time_notices), 1)
        self.assertIn("UTC\x01", time_notices[0])
        await client.close()

    async def test_ctcp_ping_reply(self):
        harness = MockStreamHarness()
        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#arena",
            message="Hello",
            sender_nick="Alice",
            source_network="2600net",
            reader=harness.client_reader,
            writer=harness.client_writer,
        )
        await client.connect()

        # CTCP PING with token
        query_line = f":PingTester!u@host PRIVMSG {client.nickname} :\x01PING 1234567890\x01"
        await client._process_line(query_line)
        self.assertIn("NOTICE PingTester :\x01PING 1234567890\x01", harness.client_written)

        # CTCP PING without token
        query_line2 = f":PingTester!u@host PRIVMSG {client.nickname} :\x01PING\x01"
        await client._process_line(query_line2)
        self.assertIn("NOTICE PingTester :\x01PING\x01", harness.client_written)
        await client.close()


class TestMCPMessengerNetworkResolution(unittest.TestCase):
    """Verifies R1.2 Scoping & Network Resolution."""

    def setUp(self):
        self.mock_node = MagicMock()
        self.mock_node.config = {
            "networks": {
                "2600net": {"server": "irc.scuttled.net", "port": 6697, "ssl": True},
                "rizon": {"server": "irc.rizon.net", "port": 6697, "ssl": True},
            }
        }

    def test_standard_player_configured_network_allowed(self):
        ok, err, net = resolve_target_network(self.mock_node, "2600net", is_admin=False)
        self.assertTrue(ok)
        self.assertEqual(net["server"], "irc.scuttled.net")
        self.assertEqual(net["port"], 6697)
        self.assertTrue(net["ssl"])
        self.assertFalse(net["is_fqdn"])

        # Case-insensitive
        ok, err, net = resolve_target_network(self.mock_node, "RIZON", is_admin=False)
        self.assertTrue(ok)
        self.assertEqual(net["server"], "irc.rizon.net")

    def test_standard_player_unconfigured_network_rejected(self):
        ok, err, net = resolve_target_network(self.mock_node, "irc.libera.chat", is_admin=False)
        self.assertFalse(ok)
        self.assertIn("unconfigured", err.lower())
        self.assertIn("2600net", err)

    def test_admin_fqdn_override_default_port(self):
        ok, err, net = resolve_target_network(self.mock_node, "irc.libera.chat", is_admin=True)
        self.assertTrue(ok)
        self.assertEqual(net["server"], "irc.libera.chat")
        self.assertEqual(net["port"], 6697)
        self.assertTrue(net["ssl"])
        self.assertTrue(net["is_fqdn"])

    def test_admin_fqdn_override_custom_port(self):
        ok, err, net = resolve_target_network(self.mock_node, "irc.oftc.net:6667", is_admin=True)
        self.assertTrue(ok)
        self.assertEqual(net["server"], "irc.oftc.net")
        self.assertEqual(net["port"], 6667)
        self.assertFalse(net["ssl"])  # Plaintext 6667
        self.assertTrue(net["is_fqdn"])


class TestMCPMessengerPoliteLifecycle(unittest.IsolatedAsyncioTestCase):
    """Verifies R1.3 Polite lifecycle, delays (3-5s), payload delivery, part, and quit."""

    async def test_channel_polite_lifecycle_order_and_pacing(self):
        harness = MockStreamHarness()
        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#AutomataGrid",
            message="Strategic alert from 2600net",
            sender_nick="Alice",
            source_network="2600net",
            wait_seconds=0,
            pacing_delay_range=(3.0, 5.0),
            reader=harness.client_reader,
            writer=harness.client_writer,
        )

        sleep_calls: List[float] = []
        orig_sleep = asyncio.sleep

        async def fake_sleep(dur):
            sleep_calls.append(dur)
            await orig_sleep(0)

        # Feed 001 welcome immediately when registered
        async def background_driver():
            await harness.wait_for_client_line("NICK ")
            harness.feed_welcome(client.nickname)

        driver_task = asyncio.create_task(background_driver())

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await client.run()

        await driver_task

        # Assert sequence of sent lines
        written = harness.client_written

        # 1. Join channel
        self.assertIn("JOIN #AutomataGrid", written)

        # 2. Arrival announcement
        arrival_idx = next(i for i, l in enumerate(written) if "Greetings, #AutomataGrid" in l)
        self.assertIn("Incoming transmission from Alice on 2600net.", written[arrival_idx])

        # 3. Message payload delivery
        payload_idx = next(i for i, l in enumerate(written) if 'Alice says: "Strategic alert from 2600net"' in l)
        self.assertGreater(payload_idx, arrival_idx)

        # 4. Departure announcement
        depart_idx = next(i for i, l in enumerate(written) if "Courier mission complete" in l)
        self.assertGreater(depart_idx, payload_idx)
        self.assertIn("Terminating carrier link to 2600net. Farewell.", written[depart_idx])

        # 5. Clean PART and QUIT
        part_idx = next(i for i, l in enumerate(written) if "PART #AutomataGrid :Transmission completed" in l)
        quit_idx = next(i for i, l in enumerate(written) if "QUIT :MCP Courier dispatch sequence concluded" in l)
        self.assertGreater(part_idx, depart_idx)
        self.assertGreater(quit_idx, part_idx)

        # 6. Verify polite pacing delays: arrival sleep and departure sleep are both in [3.0, 5.0]
        # client._get_pacing_delay records in client.sleep_durations
        self.assertEqual(len(client.sleep_durations), 2)
        arrival_sleep = client.sleep_durations[0]
        departure_sleep = client.sleep_durations[1]
        self.assertTrue(3.0 <= arrival_sleep <= 5.0, f"Arrival delay {arrival_sleep} not in [3.0, 5.0]")
        self.assertTrue(3.0 <= departure_sleep <= 5.0, f"Departure delay {departure_sleep} not in [3.0, 5.0]")

    async def test_user_direct_polite_lifecycle(self):
        harness = MockStreamHarness()
        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="Bob",
            message="Private whisper",
            sender_nick="Alice",
            source_network="2600net",
            wait_seconds=0,
            pacing_delay_range=(3.0, 5.0),
            reader=harness.client_reader,
            writer=harness.client_writer,
        )

        async def background_driver():
            await harness.wait_for_client_line("NICK ")
            harness.feed_welcome(client.nickname)

        driver_task = asyncio.create_task(background_driver())

        with patch("asyncio.sleep", new=AsyncMock()):
            await client.run()

        await driver_task
        written = harness.client_written

        # Should not JOIN a channel when target is a user
        self.assertFalse(any(l.startswith("JOIN ") for l in written))
        self.assertFalse(any(l.startswith("PART ") for l in written))

        # Direct PRIVMSG to user
        self.assertTrue(any("PRIVMSG Bob :[MCP COURIER DISPATCH] Greetings, Bob." in l for l in written))
        self.assertTrue(any('PRIVMSG Bob :Alice says: "Private whisper"' in l for l in written))
        self.assertTrue(any("QUIT :MCP Courier dispatch sequence concluded" in l for l in written))


class TestMCPMessengerReplyWindow(unittest.IsolatedAsyncioTestCase):
    """Verifies R1.3 Reply window (--wait <seconds>) handling and timeout."""

    async def test_reply_window_collects_channel_reply(self):
        harness = MockStreamHarness()
        received_reply: List[tuple] = []

        async def on_reply_cb(sender, text):
            received_reply.append((sender, text))

        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#arena",
            message="Ping across grid",
            sender_nick="Alice",
            source_network="2600net",
            wait_seconds=10,
            pacing_delay_range=(0.01, 0.02),
            reader=harness.client_reader,
            writer=harness.client_writer,
            on_reply=on_reply_cb,
        )

        async def background_responder():
            await harness.wait_for_client_line("NICK ")
            harness.feed_welcome(client.nickname)

            # Wait for standby announcement
            await harness.wait_for_client_line("standing by for replies")
            # Inject valid reply prefixed with bot_nick:
            harness.feed_server_line(f":Bob!u@h PRIVMSG #arena :{client.nickname}: Roger from Rizon!")

        responder_task = asyncio.create_task(background_responder())
        await client.run()
        await responder_task

        # Verify standby announcement
        written = harness.client_written
        self.assertTrue(any("Courier standing by for replies (window: 10s)." in l for l in written))

        # Verify logged reply announcement on target channel
        self.assertTrue(any("Transmission logged. Delivering reply back to 2600net." in l for l in written))

        # Verify callback invoked with reply
        self.assertEqual(len(received_reply), 1)
        self.assertEqual(received_reply[0], ("Bob", "Roger from Rizon!"))
        self.assertEqual(client.status, "COMPLETED")

    async def test_reply_window_collects_privmsg_reply(self):
        harness = MockStreamHarness()
        received_reply: List[tuple] = []

        async def on_reply_cb(sender, text):
            received_reply.append((sender, text))

        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="Bob",
            message="Check this out",
            sender_nick="Alice",
            source_network="2600net",
            wait_seconds=15,
            pacing_delay_range=(0.01, 0.02),
            reader=harness.client_reader,
            writer=harness.client_writer,
            on_reply=on_reply_cb,
        )

        async def background_responder():
            await harness.wait_for_client_line("NICK ")
            harness.feed_welcome(client.nickname)
            await harness.wait_for_client_line("standing by for replies")
            # Inject PM directly to courier
            harness.feed_server_line(f":Bob!u@h PRIVMSG {client.nickname} :Acknowledged, thank you.")

        responder_task = asyncio.create_task(background_responder())
        await client.run()
        await responder_task

        self.assertEqual(len(received_reply), 1)
        self.assertEqual(received_reply[0], ("Bob", "Acknowledged, thank you."))

    async def test_reply_window_timeout_expiration(self):
        harness = MockStreamHarness()
        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#arena",
            message="Anyone home?",
            sender_nick="Alice",
            source_network="2600net",
            wait_seconds=5,
            pacing_delay_range=(0.01, 0.02),
            reader=harness.client_reader,
            writer=harness.client_writer,
        )
        # Directly set wait_seconds to 0.05 for lightning fast natural timeout
        client.wait_seconds = 0.05

        async def background_responder():
            await harness.wait_for_client_line("NICK ")
            harness.feed_welcome(client.nickname)

        responder_task = asyncio.create_task(background_responder())
        await client.run()
        await responder_task

        # Verify departure proceeded despite timeout
        written = harness.client_written
        self.assertTrue(any("Courier mission complete." in l for l in written))
        self.assertTrue(any("PART #arena" in l for l in written))
        self.assertTrue(any("QUIT " in l for l in written))
        self.assertEqual(client.status, "COMPLETED")


class TestMCPMessengerCostAndEscalation(unittest.IsolatedAsyncioTestCase):
    """Verifies R2.2 Escalating cost structure and balance checks."""

    async def asyncSetUp(self):
        self.db_name = f"test_msg_cost_{int(time.time() * 1000)}.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.db_name}", echo=False)
        self.async_session = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.repo = MessengerRepository(self.async_session)

        # Seed Player & Character
        async with self.async_session() as session:
            player = Player(global_name="TestCourierPlayer")
            session.add(player)
            await session.flush()
            alias = NetworkAlias(player_id=player.id, network_name="2600net", nickname="Alice")
            session.add(alias)
            char = Character(
                player_id=player.id,
                name="Alice",
                race="Cyborg",
                char_class="Netrunner",
                credits=10000.0,
                power=200.0,
            )
            session.add(char)
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()
        if os.path.exists(self.db_name):
            try:
                os.remove(self.db_name)
            except OSError:
                pass

    async def test_escalating_fee_progression(self):
        # 1st dispatch: 0 prior in 24h -> 2,500c, 50uP
        fee_c, fee_p, recent = await self.repo.calculate_fee("Alice", hours=24)
        self.assertEqual(recent, 0)
        self.assertEqual(fee_c, 2500.0)
        self.assertEqual(fee_p, 50.0)

        # Record 1st dispatch
        await self.repo.record_dispatch(
            sender_nick="Alice",
            character_id=1,
            source_network="2600net",
            target_network="rizon",
            target_dest="#arena",
            message="Msg 1",
            fee_credits=fee_c,
            fee_power=fee_p,
        )

        # 2nd dispatch: 1 prior in 24h -> 2,500 * (1 + 0.5) = 3,750c
        fee_c2, fee_p2, recent2 = await self.repo.calculate_fee("Alice", hours=24)
        self.assertEqual(recent2, 1)
        self.assertEqual(fee_c2, 3750.0)
        self.assertEqual(fee_p2, 50.0)

        # Record 2nd dispatch
        await self.repo.record_dispatch(
            sender_nick="Alice",
            character_id=1,
            source_network="2600net",
            target_network="rizon",
            target_dest="#arena",
            message="Msg 2",
            fee_credits=fee_c2,
            fee_power=fee_p2,
        )

        # 3rd dispatch: 2 prior in 24h -> 2,500 * (1 + 1.0) = 5,000c
        fee_c3, fee_p3, recent3 = await self.repo.calculate_fee("Alice", hours=24)
        self.assertEqual(recent3, 2)
        self.assertEqual(fee_c3, 5000.0)
        self.assertEqual(fee_p3, 50.0)

    async def test_deduct_fee_insufficient_credits(self):
        # Lower Alice credits to 1,000c
        async with self.async_session() as session:
            char = await self.repo._get_character_ci("Alice", "2600net", session)
            char.credits = 1000.0
            await session.commit()

        ok, msg, char = await self.repo.deduct_fee("Alice", "2600net", 2500.0, 50.0)
        self.assertFalse(ok)
        self.assertIn("Insufficient credits", msg)

    async def test_deduct_fee_insufficient_power(self):
        # Lower Alice power to 20uP
        async with self.async_session() as session:
            char = await self.repo._get_character_ci("Alice", "2600net", session)
            char.credits = 5000.0
            char.power = 20.0
            await session.commit()

        ok, msg, char = await self.repo.deduct_fee("Alice", "2600net", 2500.0, 50.0)
        self.assertFalse(ok)
        self.assertIn("Insufficient power", msg)

    async def test_successful_deduction(self):
        ok, msg, char = await self.repo.deduct_fee("Alice", "2600net", 2500.0, 50.0)
        self.assertTrue(ok)
        # Verify in DB
        async with self.async_session() as session:
            refreshed = await self.repo._get_character_ci("Alice", "2600net", session)
            self.assertEqual(refreshed.credits, 7500.0)
            self.assertEqual(refreshed.power, 150.0)


class TestMCPMessengerCommandHandler(unittest.IsolatedAsyncioTestCase):
    """Verifies R2 Command routing, rate limiting, FQDN admin bypass, and reply relay."""

    async def asyncSetUp(self):
        self.db_name = f"test_msg_cmd_{int(time.time() * 1000)}.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.db_name}", echo=False)
        self.async_session = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.node = MockGridNode(self.async_session)

        # Seed Alice
        async with self.async_session() as session:
            player = Player(global_name="AlicePlayer")
            session.add(player)
            await session.flush()
            session.add(NetworkAlias(player_id=player.id, network_name="2600net", nickname="Alice"))
            char = Character(
                player_id=player.id,
                name="Alice",
                race="Cyborg",
                char_class="Netrunner",
                credits=10000.0,
                power=200.0,
            )
            session.add(char)
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()
        if os.path.exists(self.db_name):
            try:
                os.remove(self.db_name)
            except OSError:
                pass

    async def test_command_syntax_validation(self):
        # Empty args
        await handle_messenger(self.node, "Alice", [], "#AutomataGrid")
        self.assertTrue(any("Syntax: !a messenger <network|fqdn>" in m for m in self.node.sent_messages))

        # Too few args
        self.node.sent_messages.clear()
        await handle_messenger(self.node, "Alice", ["2600net", "#channel"], "#AutomataGrid")
        self.assertTrue(any("Syntax: !a messenger" in m for m in self.node.sent_messages))

        # Missing --wait value
        self.node.sent_messages.clear()
        await handle_messenger(self.node, "Alice", ["2600net", "#channel", "Hello", "--wait"], "#AutomataGrid")
        self.assertTrue(any("--wait requires a duration" in m for m in self.node.sent_messages))

    async def test_rate_limit_cooldown_60s(self):
        # Mock client factory so background task runs without socket
        mock_client = MagicMock()
        mock_client.run = AsyncMock()
        mock_client.reply_received = None
        mock_client.wait_seconds = 0
        mock_client.status = "COMPLETED"
        self.node.messenger_client_factory = MagicMock(return_value=mock_client)

        # 1st dispatch succeeds
        await handle_messenger(self.node, "Alice", ["2600net", "#channel", "First message"], "#AutomataGrid")
        self.assertTrue(any("Courier initiated" in m for m in self.node.sent_messages))

        # Immediate 2nd dispatch rejected due to 60s cooldown
        self.node.sent_messages.clear()
        await handle_messenger(self.node, "Alice", ["2600net", "#channel", "Second message"], "#AutomataGrid")
        self.assertTrue(any("Messenger cooldown active" in m for m in self.node.sent_messages))

    async def test_standard_player_unconfigured_network_rejected(self):
        await handle_messenger(self.node, "Alice", ["irc.unknown-net.org", "#chan", "Test"], "#AutomataGrid")
        self.assertTrue(any("Target network 'irc.unknown-net.org' is unconfigured" in m for m in self.node.sent_messages))

    async def test_admin_fqdn_override_and_balance_bypass(self):
        # Admin dispatch to arbitrary FQDN with 0 credits
        mock_client = MagicMock()
        mock_client.run = AsyncMock()
        mock_client.reply_received = None
        mock_client.wait_seconds = 0
        mock_client.status = "COMPLETED"
        self.node.messenger_client_factory = MagicMock(return_value=mock_client)

        # admin_alice is in node.admins
        await handle_messenger(
            self.node, "admin_alice", ["irc.libera.chat:6697", "#test", "Admin dispatch test"], "#AutomataGrid"
        )
        self.assertTrue(any("Courier initiated for #test on irc.libera.chat:6697" in m for m in self.node.sent_messages))

    async def test_reply_forwarded_to_origin_channel_and_memo(self):
        mock_client = MagicMock()
        captured_callbacks = {}

        def mock_factory(**kwargs):
            mock_client.on_reply = kwargs.get("on_reply")
            mock_client.wait_seconds = kwargs.get("wait_seconds", 0)
            mock_client.reply_received = None
            mock_client.status = "COMPLETED"

            async def fake_run():
                # Simulate reply arrival
                if mock_client.on_reply:
                    await mock_client.on_reply("Bob", "Cross-grid response received!")

            mock_client.run = fake_run
            return mock_client

        self.node.messenger_client_factory = mock_factory

        await handle_messenger(
            self.node, "Alice", ["2600net", "#channel", "Hello remote", "--wait", "10"], "#AutomataGrid"
        )

        # Give asyncio tasks a chance to execute
        await asyncio.sleep(0.05)

        # Verify reply announced on origin channel
        self.assertTrue(
            any("Reply from Bob on 2600net: \"Cross-grid response received!\"" in m for m in self.node.sent_messages)
        )

        # Verify Memo stored in DB for Alice
        async with self.async_session() as session:
            from sqlalchemy.future import select
            memos = (await session.execute(select(Memo))).scalars().all()
            self.assertEqual(len(memos), 1)
            self.assertIn("Cross-grid response received!", memos[0].message)


class TestMCPMessengerHelpParity(unittest.IsolatedAsyncioTestCase):
    """Verifies R3 Help Registry & Documentation Parity."""

    async def asyncSetUp(self):
        self.node = MagicMock()
        self.node.prefix = "!a"
        self.node.net_name = "2600net"
        self.sent: List[str] = []

        async def fake_send(line):
            self.sent.append(line)

        self.node.send = fake_send
        self.node.db = MagicMock()
        self.node.db.get_prefs = AsyncMock(return_value={"output_mode": "human"})

    async def test_help_detailed_messenger_human(self):
        await handle_help(self.node, "Alice", ["messenger"], "#AutomataGrid")
        output = " ".join(self.sent)
        self.assertIn("MESSENGER", output)
        self.assertIn("Dispatch a polite autonomous MCP courier bot", output)
        self.assertIn("messenger <network|fqdn> <channel|user> <message> [--wait <seconds>]", output)
        self.assertIn("2500c+ & 50u Power", output)

    async def test_help_detailed_messenger_machine(self):
        self.node.db.get_prefs = AsyncMock(return_value={"output_mode": "machine"})
        await handle_help(self.node, "Alice", ["messenger"], "#AutomataGrid")
        output = " ".join(self.sent)
        self.assertIn("CMD:MESSENGER", output)
        self.assertIn("SYNTAX:!a messenger <network|fqdn>", output)

    async def test_help_categories_include_messenger(self):
        await handle_help(self.node, "Alice", [], "#AutomataGrid")
        output = " ".join(self.sent)
        # Check ANALYTICS or TACTICAL contains messenger
        self.assertIn("!a messenger", output)


class TestMCPMessengerCommandRouter(unittest.IsolatedAsyncioTestCase):
    """Verifies command_router routes !a messenger and !a dispatch."""

    async def asyncSetUp(self):
        from ai_grid.core.command_router import CommandRouter
        self.node = MagicMock()
        self.node.prefix = "!a"
        self.node.active_engine = None
        self.node.flood_config = {
            "max_tokens": 10.0,
            "refill_rate": 1.0,
            "violation_threshold": 5,
            "lockout_duration": 30,
            "messages": {}
        }
        self.node.action_timestamps = {}
        self.node.config = {"nickname": "GridManager", "channel": "#arena"}
        self.router = CommandRouter(self.node)

    @patch("ai_grid.core.handlers.handle_messenger")
    async def test_router_dispatches_messenger_and_alias(self, mock_handler):
        mock_handler.return_value = None

        # !a messenger
        await self.router.dispatch("Alice", "messenger", "#arena", "!a messenger 2600net #target Hi", False)
        await asyncio.sleep(0.01)
        self.assertTrue(mock_handler.called)
        call_args = mock_handler.call_args[0]
        self.assertEqual(call_args[1], "Alice")
        self.assertEqual(call_args[2], ["2600net", "#target", "Hi"])

        mock_handler.reset_mock()
        # !a dispatch
        await self.router.dispatch("Alice", "dispatch", "#arena", "!a dispatch 2600net #target Hi2", False)
        await asyncio.sleep(0.01)
        self.assertTrue(mock_handler.called)
        call_args2 = mock_handler.call_args[0]
        self.assertEqual(call_args2[1], "Alice")
        self.assertEqual(call_args2[2], ["2600net", "#target", "Hi2"])


class TestMCPMessengerAdversarialEdgeCases(unittest.IsolatedAsyncioTestCase):
    """Adversarial and deep edge-case validation for MCP Courier."""

    async def asyncSetUp(self):
        self.db_name = f"test_msg_adv_{int(time.time() * 1000)}.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.db_name}", echo=False)
        self.async_session = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.node = MockGridNode(self.async_session)

        async with self.async_session() as session:
            player = Player(global_name="AdvPlayer")
            session.add(player)
            await session.flush()
            session.add(NetworkAlias(player_id=player.id, network_name="2600net", nickname="Alice"))
            char = Character(
                player_id=player.id,
                name="Alice",
                race="Cyborg",
                char_class="Netrunner",
                credits=10000.0,
                power=200.0,
            )
            session.add(char)
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()
        if os.path.exists(self.db_name):
            try:
                os.remove(self.db_name)
            except OSError:
                pass

    async def test_adversarial_wait_bounds_rejection(self):
        # Negative wait
        await handle_messenger(self.node, "Alice", ["2600net", "#chan", "Test", "--wait", "-10"], "#arena")
        self.assertTrue(any("Invalid --wait duration '-10'" in m for m in self.node.sent_messages))

        # Zero wait
        self.node.sent_messages.clear()
        await handle_messenger(self.node, "Alice", ["2600net", "#chan", "Test", "--wait", "0"], "#arena")
        self.assertTrue(any("Invalid --wait duration '0'" in m for m in self.node.sent_messages))

        # Too large wait
        self.node.sent_messages.clear()
        await handle_messenger(self.node, "Alice", ["2600net", "#chan", "Test", "--wait", "500"], "#arena")
        self.assertTrue(any("Invalid --wait duration '500'" in m for m in self.node.sent_messages))

        # Non-integer wait
        self.node.sent_messages.clear()
        await handle_messenger(self.node, "Alice", ["2600net", "#chan", "Test", "--wait", "NaN"], "#arena")
        self.assertTrue(any("Invalid --wait duration 'NaN'" in m for m in self.node.sent_messages))

    def test_admin_fqdn_adversarial_port_and_hostname(self):
        # Port out of bounds
        ok, err, net = resolve_target_network(self.node, "irc.libera.chat:99999", is_admin=True)
        self.assertFalse(ok)
        self.assertIn("Port must be between 1 and 65535", err)

        ok, err, net = resolve_target_network(self.node, "irc.libera.chat:0", is_admin=True)
        self.assertFalse(ok)
        self.assertIn("Port must be between 1 and 65535", err)

        ok, err, net = resolve_target_network(self.node, "irc.libera.chat:-5", is_admin=True)
        self.assertFalse(ok)
        self.assertIn("Port must be between 1 and 65535", err)

        # Malformed hostname
        ok, err, net = resolve_target_network(self.node, "invalid..host", is_admin=True)
        self.assertFalse(ok)
        self.assertIn("Invalid hostname format", err)

        ok, err, net = resolve_target_network(self.node, "-invalid.host", is_admin=True)
        self.assertFalse(ok)
        self.assertIn("Invalid hostname format", err)

    def test_production_gridnode_and_server_hostname_resolution(self):
        # Simulating production GridNode where node.config is net_config without 'networks' key
        prod_node = MagicMock()
        prod_node.config = {"server": "irc.scuttled.net", "port": 6697, "ssl": True}
        prod_node.hub = None

        with patch("ai_grid.database.core.CONFIG", {"networks": {"2600net": {"server": "irc.scuttled.net", "port": 6697, "ssl": True}}}):
            # Resolve by network name
            ok, err, net = resolve_target_network(prod_node, "2600net", is_admin=False)
            self.assertTrue(ok)
            self.assertEqual(net["network_name"], "2600net")

            # Resolve by server hostname
            ok, err, net = resolve_target_network(prod_node, "irc.scuttled.net", is_admin=False)
            self.assertTrue(ok)
            self.assertEqual(net["network_name"], "2600net")

    async def test_nick_collision_433_rotates_nickname(self):
        harness = MockStreamHarness()
        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#arena",
            message="Collision test",
            sender_nick="Alice",
            source_network="2600net",
            pacing_delay_range=(0.01, 0.02),
            reader=harness.client_reader,
            writer=harness.client_writer,
        )

        initial_nick = client.nickname

        async def background_responder():
            await harness.wait_for_client_line(f"NICK {initial_nick}")
            # Feed 433 ERR_NICKNAMEINUSE
            harness.feed_server_line(f":mock.server 433 * {initial_nick} :Nickname is already in use")

            # Wait for client to send a NEW NICK line different from initial_nick
            start = time.time()
            rotated_line = None
            while time.time() - start < 2.0:
                for line in harness.client_written:
                    if line.startswith("NICK GridMessenger") and line != f"NICK {initial_nick}":
                        rotated_line = line
                        break
                if rotated_line:
                    break
                await asyncio.sleep(0.01)

            self.assertIsNotNone(rotated_line, "Client did not send rotated NICK line after 433")
            new_nick = rotated_line.split()[1]
            self.assertNotEqual(new_nick, initial_nick)
            # Welcome the new nick
            harness.feed_welcome(new_nick)

        responder_task = asyncio.create_task(background_responder())
        await client.run()
        await responder_task
        self.assertEqual(client.status, "COMPLETED")

    async def test_remote_channel_mode_rejection_aborts_lifecycle(self):
        harness = MockStreamHarness()
        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#secret_keyed",
            message="Top secret message",
            sender_nick="Alice",
            source_network="2600net",
            pacing_delay_range=(0.01, 0.02),
            reader=harness.client_reader,
            writer=harness.client_writer,
        )

        async def background_responder():
            await harness.wait_for_client_line("NICK ")
            harness.feed_welcome(client.nickname)
            # Wait for JOIN
            await harness.wait_for_client_line("JOIN #secret_keyed")
            # Send 475 ERR_BADCHANNELKEY
            harness.feed_server_line(f":mock.server 475 {client.nickname} #secret_keyed :Cannot join channel (+k)")

        responder_task = asyncio.create_task(background_responder())

        with self.assertRaises(RuntimeError) as cm:
            await client.run()

        await responder_task
        self.assertIn("Remote IRC error 475 on #secret_keyed: Cannot join channel (+k)", str(cm.exception))
        self.assertEqual(client.status, "FAILED")
        # Ensure payload was NOT delivered
        written = harness.client_written
        self.assertFalse(any('Alice says: "Top secret message"' in l for l in written))

    async def test_moderated_channel_404_rejection_aborts_lifecycle(self):
        harness = MockStreamHarness()
        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#moderated",
            message="Unvoiced talk",
            sender_nick="Alice",
            source_network="2600net",
            pacing_delay_range=(0.01, 0.02),
            reader=harness.client_reader,
            writer=harness.client_writer,
        )

        async def background_responder():
            await harness.wait_for_client_line("NICK ")
            harness.feed_welcome(client.nickname)
            await harness.wait_for_client_line("JOIN #moderated")
            # Wait for arrival announcement
            await harness.wait_for_client_line("Greetings, #moderated")
            # Send 404 ERR_CANNOTSENDTOCHAN
            harness.feed_server_line(f":mock.server 404 {client.nickname} #moderated :Cannot send to channel (+m)")

        responder_task = asyncio.create_task(background_responder())

        with self.assertRaises(RuntimeError) as cm:
            await client.run()

        await responder_task
        self.assertIn("Remote IRC error 404", str(cm.exception))
        self.assertEqual(client.status, "FAILED")

    async def test_reply_window_collects_reply_with_space_prefix(self):
        harness = MockStreamHarness()
        received_reply: List[tuple] = []

        async def on_reply_cb(sender, text):
            received_reply.append((sender, text))

        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#arena",
            message="Ping",
            sender_nick="Alice",
            source_network="2600net",
            wait_seconds=10,
            pacing_delay_range=(0.01, 0.02),
            reader=harness.client_reader,
            writer=harness.client_writer,
            on_reply=on_reply_cb,
        )

        async def background_responder():
            await harness.wait_for_client_line("NICK ")
            harness.feed_welcome(client.nickname)
            await harness.wait_for_client_line("standing by for replies")
            # Space prefix without colon or comma
            harness.feed_server_line(f":Bob!u@h PRIVMSG #arena :{client.nickname} Space prefixed reply!")

        responder_task = asyncio.create_task(background_responder())
        await client.run()
        await responder_task

        self.assertEqual(len(received_reply), 1)
        self.assertEqual(received_reply[0], ("Bob", "Space prefixed reply!"))

    async def test_admin_fqdn_initiation_fee_display(self):
        mock_client = MagicMock()
        mock_client.run = AsyncMock()
        mock_client.reply_received = None
        mock_client.wait_seconds = 0
        mock_client.status = "COMPLETED"
        self.node.messenger_client_factory = MagicMock(return_value=mock_client)

        await handle_messenger(
            self.node, "admin_alice", ["irc.libera.chat:6697", "#test", "Test FQDN"], "#arena"
        )
        self.assertTrue(
            any("Admin FQDN Test Bypass | 0.0c, 0.0uP" in m for m in self.node.sent_messages)
        )

    async def test_payload_rejection_during_departure_delay_fails_fast(self):
        harness = MockStreamHarness()
        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#moderated",
            message="Unvoiced talk",
            sender_nick="Alice",
            source_network="2600net",
            wait_seconds=0,
            pacing_delay_range=(0.05, 0.08),
            reader=harness.client_reader,
            writer=harness.client_writer,
        )

        async def background_responder():
            await harness.wait_for_client_line("NICK ")
            harness.feed_welcome(client.nickname)
            await harness.wait_for_client_line("JOIN #moderated")
            await harness.wait_for_client_line("Greetings, #moderated")
            # Wait for the payload message delivery
            await harness.wait_for_client_line('Alice says: "Unvoiced talk"')
            # Server returns 404 in response to payload delivery
            harness.feed_server_line(f":mock.server 404 {client.nickname} #moderated :Cannot send to channel (+m)")

        responder_task = asyncio.create_task(background_responder())

        with self.assertRaises(RuntimeError) as cm:
            await client.run()

        await responder_task
        self.assertIn("Remote IRC error 404 on #moderated: Cannot send to channel (+m)", str(cm.exception))
        self.assertEqual(client.status, "FAILED")

    async def test_unexpected_server_disconnect_eof_aborts_immediately(self):
        harness = MockStreamHarness()
        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#arena",
            message="Test msg",
            sender_nick="Alice",
            source_network="2600net",
            wait_seconds=30,  # Long wait
            pacing_delay_range=(0.01, 0.02),
            reader=harness.client_reader,
            writer=harness.client_writer,
        )

        async def background_responder():
            await harness.wait_for_client_line("NICK ")
            harness.feed_welcome(client.nickname)
            await harness.wait_for_client_line("standing by for replies")
            # Remote server abruptly drops the connection (EOF)
            harness.client_reader.feed_eof()

        responder_task = asyncio.create_task(background_responder())

        t0 = time.time()
        with self.assertRaises(RuntimeError) as cm:
            await client.run()
        elapsed = time.time() - t0

        await responder_task
        self.assertLess(elapsed, 2.0, "Should abort immediately on EOF instead of waiting 30s")
        self.assertIn("disconnected unexpectedly", str(cm.exception))
        self.assertEqual(client.status, "FAILED")

    async def test_server_error_closing_link_aborts_immediately(self):
        harness = MockStreamHarness()
        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#arena",
            message="Test msg",
            sender_nick="Alice",
            source_network="2600net",
            pacing_delay_range=(0.01, 0.02),
            reader=harness.client_reader,
            writer=harness.client_writer,
        )

        async def background_responder():
            await harness.wait_for_client_line("NICK ")
            # Server immediately drops client with ERROR command
            harness.feed_server_line(":mock.server ERROR :Closing Link: 127.0.0.1 (K-lined: Banned from network)")

        responder_task = asyncio.create_task(background_responder())

        with self.assertRaises(RuntimeError) as cm:
            await client.run()

        await responder_task
        self.assertIn("K-lined: Banned from network", str(cm.exception))
        self.assertEqual(client.status, "FAILED")

    async def test_channel_mode_477_need_regged_nick_aborts(self):
        harness = MockStreamHarness()
        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#regged_only",
            message="Hi",
            sender_nick="Alice",
            source_network="2600net",
            pacing_delay_range=(0.01, 0.02),
            reader=harness.client_reader,
            writer=harness.client_writer,
        )

        async def background_responder():
            await harness.wait_for_client_line("NICK ")
            harness.feed_welcome(client.nickname)
            await harness.wait_for_client_line("JOIN #regged_only")
            # Send 477 ERR_NEEDREGGEDNICK
            harness.feed_server_line(f":mock.server 477 {client.nickname} #regged_only :Cannot join channel (+r) - you need to be identified with NickServ")

        responder_task = asyncio.create_task(background_responder())

        with self.assertRaises(RuntimeError) as cm:
            await client.run()

        await responder_task
        self.assertIn("Remote IRC error 477 on #regged_only", str(cm.exception))
        self.assertEqual(client.status, "FAILED")

    def test_target_plaintext_irc_ports_resolve_ssl_false(self):
        # Plaintext IRC ports 6660-6669 should resolve with ssl=False
        for p in [6660, 6665, 6666, 6667, 6668, 6669]:
            ok, err, net = resolve_target_network(self.node, f"irc.libera.chat:{p}", is_admin=True)
            self.assertTrue(ok)
            self.assertFalse(net["ssl"], f"Port {p} should be plaintext ssl=False")

        # Standard SSL ports should resolve with ssl=True
        for p in [6697, 7000]:
            ok, err, net = resolve_target_network(self.node, f"irc.libera.chat:{p}", is_admin=True)
            self.assertTrue(ok)
            self.assertTrue(net["ssl"], f"Port {p} should be TLS ssl=True")

    async def test_reply_window_collects_reply_with_at_mention_prefix(self):
        harness = MockStreamHarness()
        received_reply: List[tuple] = []

        async def on_reply_cb(sender, text):
            received_reply.append((sender, text))

        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#arena",
            message="Ping",
            sender_nick="Alice",
            source_network="2600net",
            wait_seconds=10,
            pacing_delay_range=(0.01, 0.02),
            reader=harness.client_reader,
            writer=harness.client_writer,
            on_reply=on_reply_cb,
        )

        async def background_responder():
            await harness.wait_for_client_line("NICK ")
            harness.feed_welcome(client.nickname)
            await harness.wait_for_client_line("standing by for replies")
            # Matrix/Discord bridge style @mention
            harness.feed_server_line(f":Bob!u@h PRIVMSG #arena :@{client.nickname}: Matrix bridge reply!")

        responder_task = asyncio.create_task(background_responder())
        await client.run()
        await responder_task

        self.assertEqual(len(received_reply), 1)
        self.assertEqual(received_reply[0], ("Bob", "Matrix bridge reply!"))

    async def test_registration_timeout_unwelcomed_server_aborts(self):
        """Verifies that an unresponsive IRC server (no 001 RPL_WELCOME) triggers registration timeout and fails fast."""
        harness = MockStreamHarness()
        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#AutomataGrid",
            message="Ghost transmission",
            sender_nick="Alice",
            source_network="2600net",
            registration_timeout=0.05,
            pacing_delay_range=(0.01, 0.02),
            reader=harness.client_reader,
            writer=harness.client_writer,
        )

        with self.assertRaises(RuntimeError) as cm:
            await client.run()

        self.assertIn("IRC registration timed out (no 001 RPL_WELCOME received)", str(cm.exception))
        self.assertEqual(client.status, "FAILED")
        self.assertFalse(client.is_registered)
        # Ensure no JOIN or arrival messages were dispatched
        written = harness.client_written
        self.assertFalse(any("JOIN " in l for l in written))
        self.assertFalse(any("Greetings" in l for l in written))

    async def test_nick_collision_exceeding_limit_aborts(self):
        """Verifies that repeated 433 ERR_NICKNAMEINUSE exceeding retry limit aborts lifecycle cleanly."""
        harness = MockStreamHarness()
        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#AutomataGrid",
            message="Collision test",
            sender_nick="Alice",
            source_network="2600net",
            registration_timeout=2.0,
            pacing_delay_range=(0.01, 0.02),
            reader=harness.client_reader,
            writer=harness.client_writer,
        )

        async def collision_spammer():
            for _ in range(7):
                await harness.wait_for_client_line("NICK ")
                harness.feed_server_line(f":mock.server 433 * {client.nickname} :Nickname is already in use")

        spammer_task = asyncio.create_task(collision_spammer())

        with self.assertRaises(RuntimeError) as cm:
            await client.run()

        await spammer_task
        self.assertIn("Nickname collision retry limit exceeded", str(cm.exception))
        self.assertEqual(client.status, "FAILED")

    async def test_numeric_464_password_mismatch_aborts(self):
        """Verifies that 464 ERR_PASSWDMISMATCH aborts lifecycle cleanly."""
        harness = MockStreamHarness()
        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="#AutomataGrid",
            message="Auth fail test",
            sender_nick="Alice",
            source_network="2600net",
            pacing_delay_range=(0.01, 0.02),
            reader=harness.client_reader,
            writer=harness.client_writer,
        )

        async def background_responder():
            await harness.wait_for_client_line("NICK ")
            harness.feed_server_line(f":mock.server 464 {client.nickname} :Password incorrect")

        responder_task = asyncio.create_task(background_responder())

        with self.assertRaises(RuntimeError) as cm:
            await client.run()

        await responder_task
        self.assertIn("Remote IRC error 464", str(cm.exception))
        self.assertEqual(client.status, "FAILED")

    async def test_pm_reply_strips_bot_nickname_prefix(self):
        """Verifies that PM replies directed to courier with bot nick prefix have prefix stripped."""
        harness = MockStreamHarness()
        received_reply: List[tuple] = []

        async def on_reply_cb(sender, text):
            received_reply.append((sender, text))

        client = MCPMessengerClient(
            target_server="irc.scuttled.net",
            target_port=6697,
            target_dest="Bob",
            message="Private request",
            sender_nick="Alice",
            source_network="2600net",
            wait_seconds=10,
            pacing_delay_range=(0.01, 0.02),
            reader=harness.client_reader,
            writer=harness.client_writer,
            on_reply=on_reply_cb,
        )

        async def background_responder():
            await harness.wait_for_client_line("NICK ")
            harness.feed_welcome(client.nickname)
            await harness.wait_for_client_line("standing by for replies")
            # Bob replies via PM with bot_nick prefix
            harness.feed_server_line(f":Bob!u@h PRIVMSG {client.nickname} :{client.nickname}: Understood, executing.")

        responder_task = asyncio.create_task(background_responder())
        await client.run()
        await responder_task

        self.assertEqual(len(received_reply), 1)
        self.assertEqual(received_reply[0], ("Bob", "Understood, executing."))


if __name__ == "__main__":
    unittest.main()


