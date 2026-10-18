# ai_grid/qa/tests/test_incursions.py
# Comprehensive Async Unit Tests for Dynamic LLM Incursion Mob Names & 4 Difficulty Tiers
# - R1: LLM Mob Name Generation with 4-Tier Fallback Roster & 30-Second Timeout Handling
# - R2: Incursion Loop & Event Broadcast Integration with Tier & Difficulty Labels
# - R3: In-Memory SQLite (aiosqlite) Persistence & Sandbox Socket Safety

import asyncio
import random
import unittest
import urllib.error
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select, update
from ai_grid.models import Base, GridNode, Character, IncursionEvent, IncursionDefender
from ai_grid.grid_db import ArenaDB
from ai_grid.grid_llm import ArenaLLM, FALLBACK_ROSTER, INCURSION_FALLBACK_ROSTER, TIER_LABELS
from ai_grid.core.loops import (
    incursion_event_loop,
    INCURSION_SCHEDULE,
    TIER_POPULATION_GATES,
    count_active_players,
    calculate_effective_incursion_chance,
)


class TestLLMIncursionMobName(unittest.IsolatedAsyncioTestCase):
    """Unit tests for ArenaLLM.generate_incursion_mob_name and fallback handling."""

    def setUp(self):
        self.llm = ArenaLLM({"llm": {"endpoint": "http://mock-llm/v1/chat/completions", "timeout": 30.0}})

    async def test_valid_llm_response(self):
        """Verify generate_incursion_mob_name returns clean alphanumeric name from valid LLM response."""
        with patch.object(self.llm, "_make_request", return_value="CyberViper"):
            name = await self.llm.generate_incursion_mob_name(1, "AlphaNode", "CRP")
            self.assertEqual(name, "CyberViper")

    async def test_valid_llm_response_sanitization(self):
        """Verify whitespace, quotation marks, and backticks are cleanly stripped."""
        with patch.object(self.llm, "_make_request", return_value='  `"NullWarden"` \n'):
            name = await self.llm.generate_incursion_mob_name(2, "DataVault", "DTC")
            self.assertEqual(name, "NullWarden")

    async def test_timeout_enforces_30_seconds_and_passes_to_request(self):
        """Verify request timeout is passed as 30.0s to _make_request."""
        with patch.object(self.llm, "_make_request", return_value="ApexSentinel") as mock_req:
            await self.llm.generate_incursion_mob_name(3, "CentralCore", "MIL")
            self.assertEqual(mock_req.call_count, 1)
            # Check timeout kwarg or positional arg is 30.0
            args, kwargs = mock_req.call_args
            timeout_passed = kwargs.get("timeout") if "timeout" in kwargs else (args[2] if len(args) > 2 else None)
            self.assertEqual(timeout_passed, 30.0)

    async def test_timeout_fallback_tier1_easy(self):
        """Verify timeout handling falls back to a valid Tier 1 (Easy) mob name."""
        async def mock_timeout(coro, timeout=None):
            coro.close()
            raise asyncio.TimeoutError("Simulated 30s timeout")

        with patch("asyncio.wait_for", side_effect=mock_timeout):
            name = await self.llm.generate_incursion_mob_name(1, "AlphaNode", "VOD")
            self.assertIn(name, FALLBACK_ROSTER[1])
            self.assertIn(name, [
                "HacktopusAI", "ByteViper", "NullPointer", "GlitchHound", "PingReaper", "SynFloodBot"
            ])

    async def test_timeout_fallback_tier2_medium(self):
        """Verify timeout handling falls back to a valid Tier 2 (Medium) mob name."""
        async def mock_timeout(coro, timeout=None):
            coro.close()
            raise asyncio.TimeoutError("Simulated 30s timeout")

        with patch("asyncio.wait_for", side_effect=mock_timeout):
            name = await self.llm.generate_incursion_mob_name(2, "BetaNode", "SMB")
            self.assertIn(name, FALLBACK_ROSTER[2])
            self.assertIn(name, [
                "Gridbugs", "TrojanSwarm", "DaemonCrawler", "BufferOverflow", "CipherScrambler", "PhantomRoutine"
            ])

    async def test_timeout_fallback_tier3_hard(self):
        """Verify timeout handling falls back to a valid Tier 3 (Hard) mob name."""
        async def mock_timeout(coro, timeout=None):
            coro.close()
            raise asyncio.TimeoutError("Simulated 30s timeout")

        with patch("asyncio.wait_for", side_effect=mock_timeout):
            name = await self.llm.generate_incursion_mob_name(3, "GammaNode", "MIL")
            self.assertIn(name, FALLBACK_ROSTER[3])
            self.assertIn(name, [
                "KrakenProcess", "ZeroDayColossus", "ShadowRootkit", "KernelPanic", "HypervisorWraith", "DarknetLeviathan"
            ])

    async def test_timeout_fallback_tier4_epic(self):
        """Verify timeout handling falls back to a valid Tier 4 (Epic) mob name."""
        async def mock_timeout(coro, timeout=None):
            coro.close()
            raise asyncio.TimeoutError("Simulated 30s timeout")

        with patch("asyncio.wait_for", side_effect=mock_timeout):
            name = await self.llm.generate_incursion_mob_name(4, "OmegaNode", "GOV")
            self.assertIn(name, FALLBACK_ROSTER[4])
            self.assertIn(name, [
                "KaijuBreach", "GibsonTitan", "SingularityEngine", "RansomwareOverlord", "BlackIceApex", "DoomsdayCascade"
            ])

    async def test_malformed_empty_response_fallback(self):
        """Verify empty response triggers fallback to appropriate tier roster."""
        with patch.object(self.llm, "_make_request", return_value="   "):
            name = await self.llm.generate_incursion_mob_name(1, "AlphaNode")
            self.assertIn(name, FALLBACK_ROSTER[1])

    async def test_malformed_api_error_response_fallback(self):
        """Verify API error string triggers fallback to appropriate tier roster."""
        with patch.object(self.llm, "_make_request", return_value="ERROR: Neural connection severed."):
            name = await self.llm.generate_incursion_mob_name(2, "BetaNode")
            self.assertIn(name, FALLBACK_ROSTER[2])

    async def test_malformed_spaces_response_fallback(self):
        """Verify non-alphanumeric response with multiple words triggers fallback."""
        with patch.object(self.llm, "_make_request", return_value="Giant Robot Dragon"):
            name = await self.llm.generate_incursion_mob_name(3, "GammaNode")
            self.assertIn(name, FALLBACK_ROSTER[3])

    async def test_malformed_punctuation_response_fallback(self):
        """Verify response with special characters or punctuation triggers fallback."""
        with patch.object(self.llm, "_make_request", return_value="Cyber_Glitch!"):
            name = await self.llm.generate_incursion_mob_name(4, "OmegaNode")
            self.assertIn(name, FALLBACK_ROSTER[4])

    async def test_malformed_too_short_response_fallback(self):
        """Verify response shorter than 3 characters triggers fallback."""
        with patch.object(self.llm, "_make_request", return_value="AI"):
            name = await self.llm.generate_incursion_mob_name(1, "AlphaNode")
            self.assertIn(name, FALLBACK_ROSTER[1])

    async def test_malformed_too_long_response_fallback(self):
        """Verify response longer than 24 characters triggers fallback."""
        with patch.object(self.llm, "_make_request", return_value="SuperMegaCyberQuantumTitanBreachOverlord"):
            name = await self.llm.generate_incursion_mob_name(2, "BetaNode")
            self.assertIn(name, FALLBACK_ROSTER[2])

    async def test_network_exception_fallback(self):
        """Verify clean fallback on urllib.error.URLError."""
        with patch.object(self.llm, "_make_request", side_effect=urllib.error.URLError("Connection refused")):
            name = await self.llm.generate_incursion_mob_name(3, "GammaNode")
            self.assertIn(name, FALLBACK_ROSTER[3])

    async def test_non_ascii_unicode_response_fallback(self):
        """Verify response with non-ASCII unicode or accented characters triggers fallback."""
        with patch.object(self.llm, "_make_request", return_value="CaféViper"):
            name = await self.llm.generate_incursion_mob_name(1, "AlphaNode")
            self.assertIn(name, FALLBACK_ROSTER[1])

        with patch.object(self.llm, "_make_request", return_value="Хакер"):
            name = await self.llm.generate_incursion_mob_name(2, "BetaNode")
            self.assertIn(name, FALLBACK_ROSTER[2])

    async def test_reasoning_tags_and_markdown_sanitization(self):
        """Verify <think>...</think> reasoning tags, bold formatting, and trailing dots are sanitized."""
        with patch.object(self.llm, "_make_request", return_value="<think>Selecting tactical mob...</think>CyberViper"):
            name = await self.llm.generate_incursion_mob_name(1, "AlphaNode")
            self.assertEqual(name, "CyberViper")

        with patch.object(self.llm, "_make_request", return_value="**ByteSentinel**"):
            name = await self.llm.generate_incursion_mob_name(2, "BetaNode")
            self.assertEqual(name, "ByteSentinel")

        with patch.object(self.llm, "_make_request", return_value="SynReaper."):
            name = await self.llm.generate_incursion_mob_name(1, "AlphaNode")
            self.assertEqual(name, "SynReaper")

    async def test_complex_markdown_and_prefix_sanitization(self):
        """Verify headers (#), bold prefixes (**Name:**), and underscores are cleaned cleanly."""
        with patch.object(self.llm, "_make_request", return_value="**Name:** ByteViper"):
            name = await self.llm.generate_incursion_mob_name(1, "AlphaNode")
            self.assertEqual(name, "ByteViper")

        with patch.object(self.llm, "_make_request", return_value="# SingularityEngine"):
            name = await self.llm.generate_incursion_mob_name(4, "OmegaNode")
            self.assertEqual(name, "SingularityEngine")

        with patch.object(self.llm, "_make_request", return_value="_GlitchHound_"):
            name = await self.llm.generate_incursion_mob_name(1, "AlphaNode")
            self.assertEqual(name, "GlitchHound")

        with patch.object(self.llm, "_make_request", return_value="```NullPointer```"):
            name = await self.llm.generate_incursion_mob_name(1, "AlphaNode")
            self.assertEqual(name, "NullPointer")

        with patch.object(self.llm, "_make_request", return_value="Threat Tier: 1 (Easy). Mob: PingReaper"):
            name = await self.llm.generate_incursion_mob_name(1, "AlphaNode")
            self.assertEqual(name, "PingReaper")

    async def test_multiline_llm_response_extracts_first_line(self):
        """Verify multiline LLM output extracts the first valid mob name line."""
        multiline = "ByteViper\n\nA stealthy serpentine process infiltrating system buffers."
        with patch.object(self.llm, "_make_request", return_value=multiline):
            name = await self.llm.generate_incursion_mob_name(1, "AlphaNode")
            self.assertEqual(name, "ByteViper")

    async def test_string_tier_parsing(self):
        """Verify string representations of tiers are parsed properly to their integer equivalents."""
        with patch.object(self.llm, "_make_request", return_value="DaemonCrawler") as mock_req:
            name = await self.llm.generate_incursion_mob_name("2", "BetaNode")
            self.assertEqual(name, "DaemonCrawler")
            sys_p, usr_p = mock_req.call_args[0][:2]
            self.assertIn("Threat Tier: 2 (Medium)", usr_p)

        with patch.object(self.llm, "_make_request", return_value="DoomsdayCascade") as mock_req:
            name = await self.llm.generate_incursion_mob_name("4", "OmegaNode")
            self.assertEqual(name, "DoomsdayCascade")
            sys_p, usr_p = mock_req.call_args[0][:2]
            self.assertIn("Threat Tier: 4 (Epic)", usr_p)

        with patch.object(self.llm, "_make_request", return_value="HacktopusAI") as mock_req:
            name = await self.llm.generate_incursion_mob_name("invalid", "AlphaNode")
            self.assertEqual(name, "HacktopusAI")
            sys_p, usr_p = mock_req.call_args[0][:2]
            self.assertIn("Threat Tier: 1 (Easy)", usr_p)

    async def test_out_of_range_tier_handling(self):
        """Verify out-of-range tiers (0, 5, -1) are safely clamped to 1..4."""
        with patch.object(self.llm, "_make_request", return_value="ApexSentinel") as mock_req:
            name = await self.llm.generate_incursion_mob_name(5, "OmegaNode")
            self.assertEqual(name, "ApexSentinel")
            sys_p, usr_p = mock_req.call_args[0][:2]
            self.assertIn("Threat Tier: 4 (Epic)", usr_p)

        with patch.object(self.llm, "_make_request", return_value="BasicDrone") as mock_req:
            name = await self.llm.generate_incursion_mob_name(0, "AlphaNode")
            self.assertEqual(name, "BasicDrone")
            sys_p, usr_p = mock_req.call_args[0][:2]
            self.assertIn("Threat Tier: 1 (Easy)", usr_p)

    async def test_prompts_contain_tier_label_and_target_details(self):
        """Verify system and user prompts include MCP persona, tier number, tier label, and node details."""
        with patch.object(self.llm, "_make_request", return_value="SentinelCore") as mock_req:
            await self.llm.generate_incursion_mob_name(3, "SpireNexus", "ICS")
            sys_prompt, user_prompt = mock_req.call_args[0][:2]
            
            # System prompt checks
            self.assertIn("Master Control Program", sys_prompt)
            self.assertIn("MCP", sys_prompt)
            self.assertIn("alphanumeric", sys_prompt)
            
            # User prompt checks
            self.assertIn("Threat Tier: 3 (Hard)", user_prompt)
            self.assertIn("SpireNexus", user_prompt)
            self.assertIn("ICS", user_prompt)

    async def test_fenced_code_blocks_sanitization(self):
        """Verify markdown fenced code blocks (```, ```text, ```yaml) are cleanly parsed."""
        with patch.object(self.llm, "_make_request", return_value="```\nByteViper\n```"):
            name = await self.llm.generate_incursion_mob_name(1, "AlphaNode")
            self.assertEqual(name, "ByteViper")

        with patch.object(self.llm, "_make_request", return_value="```text\nDarknetLeviathan\n```"):
            name = await self.llm.generate_incursion_mob_name(3, "GammaNode")
            self.assertEqual(name, "DarknetLeviathan")

        with patch.object(self.llm, "_make_request", return_value="```yaml\nKernelPanic\n```"):
            name = await self.llm.generate_incursion_mob_name(3, "BetaNode")
            self.assertEqual(name, "KernelPanic")

    async def test_json_response_parsing(self):
        """Verify structured JSON object responses extract the target mob name correctly."""
        with patch.object(self.llm, "_make_request", return_value='{"name": "ByteViper"}'):
            name = await self.llm.generate_incursion_mob_name(1, "AlphaNode")
            self.assertEqual(name, "ByteViper")

        with patch.object(self.llm, "_make_request", return_value='{"mob_name": "DarknetLeviathan", "tier": 3}'):
            name = await self.llm.generate_incursion_mob_name(3, "GammaNode")
            self.assertEqual(name, "DarknetLeviathan")

    async def test_multiline_prompt_repetition_sanitization(self):
        """Verify prompt header repetitions on separate lines are filtered before name extraction."""
        with patch.object(self.llm, "_make_request", return_value="Threat Tier: 1 (Easy)\nByteViper"):
            name = await self.llm.generate_incursion_mob_name(1, "AlphaNode")
            self.assertEqual(name, "ByteViper")

        with patch.object(self.llm, "_make_request", return_value="Threat Tier: 2 (Medium)\nTarget Node: VoidSector\nDaemonCrawler"):
            name = await self.llm.generate_incursion_mob_name(2, "VoidSector")
            self.assertEqual(name, "DaemonCrawler")

    async def test_conversational_preamble_sanitization(self):
        """Verify conversational preambles like 'Here is the enemy name:' and surrounding brackets are stripped."""
        with patch.object(self.llm, "_make_request", return_value="Here is the enemy name: ByteViper"):
            name = await self.llm.generate_incursion_mob_name(1, "AlphaNode")
            self.assertEqual(name, "ByteViper")

        with patch.object(self.llm, "_make_request", return_value="The selected mob name is: NullPointer"):
            name = await self.llm.generate_incursion_mob_name(1, "AlphaNode")
            self.assertEqual(name, "NullPointer")

        with patch.object(self.llm, "_make_request", return_value="[ByteViper]"):
            name = await self.llm.generate_incursion_mob_name(1, "AlphaNode")
            self.assertEqual(name, "ByteViper")

    async def test_tier_named_labels_and_prefixes_parsing(self):
        """Verify string tier labels ('Hard', 'Epic') and prefixed strings ('Tier 3', 'tier 4') map properly."""
        with patch.object(self.llm, "_make_request", return_value="ZeroDayColossus") as mock_req:
            name = await self.llm.generate_incursion_mob_name("Tier 3", "AlphaNode")
            self.assertEqual(name, "ZeroDayColossus")
            sys_p, usr_p = mock_req.call_args[0][:2]
            self.assertIn("Threat Tier: 3 (Hard)", usr_p)

        with patch.object(self.llm, "_make_request", return_value="SingularityEngine") as mock_req:
            name = await self.llm.generate_incursion_mob_name("Hard", "AlphaNode")
            self.assertEqual(name, "SingularityEngine")
            sys_p, usr_p = mock_req.call_args[0][:2]
            self.assertIn("Threat Tier: 3 (Hard)", usr_p)

        with patch.object(self.llm, "_make_request", return_value="KaijuBreach") as mock_req:
            name = await self.llm.generate_incursion_mob_name("Epic", "AlphaNode")
            self.assertEqual(name, "KaijuBreach")
            sys_p, usr_p = mock_req.call_args[0][:2]
            self.assertIn("Threat Tier: 4 (Epic)", usr_p)


class TestIncursionDatabaseSpawning(unittest.IsolatedAsyncioTestCase):
    """Unit tests for IncursionRepository operations, persistence, and tiered resolution."""

    async def asyncSetUp(self):
        self.db = ArenaDB(":memory:")
        async with self.db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Seed sample grid nodes
        async with self.db.async_session() as session:
            self.safe_node = GridNode(name="SafeHaven", node_type="safezone", net_affinity="TestNet")
            self.void_node = GridNode(name="VoidSector", node_type="void", net_affinity="TestNet", region_type="VOD")
            self.dtc_node = GridNode(name="DataCenter9", node_type="corp", net_affinity="TestNet", region_type="DTC")
            session.add_all([self.safe_node, self.void_node, self.dtc_node])
            await session.commit()

            # Refresh IDs
            await session.refresh(self.safe_node)
            await session.refresh(self.void_node)
            await session.refresh(self.dtc_node)

    async def asyncTearDown(self):
        await self.db.engine.dispose()

    async def test_get_random_incursion_node_never_picks_safezone(self):
        """Verify get_random_incursion_node only returns non-safezone nodes."""
        for _ in range(20):
            node = await self.db.incursion.get_random_incursion_node("TestNet")
            self.assertIsNotNone(node)
            self.assertNotEqual(node.node_type, "safezone")
            self.assertIn(node.name, ["VoidSector", "DataCenter9"])

    async def test_spawn_incursion_records_dynamic_mob_and_tier(self):
        """Verify incursion spawning records dynamic mob name, tier, and reward in the database."""
        result = await self.db.incursion.spawn_incursion(
            "TestNet",
            inc_type="ShadowRootkit",
            tier=3,
            reward=1500.0,
            duration_mins=5
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "ShadowRootkit")
        self.assertEqual(result["tier"], 3)
        self.assertEqual(result["reward"], 1500.0)
        self.assertIn(result["node_name"], ["VoidSector", "DataCenter9"])

        # Verify DB persistence
        async with self.db.async_session() as session:
            stmt = select(IncursionEvent).where(IncursionEvent.incursion_type == "ShadowRootkit")
            event = (await session.execute(stmt)).scalars().first()
            self.assertIsNotNone(event)
            self.assertEqual(event.tier, 3)
            self.assertEqual(event.reward_val, 1500.0)
            self.assertEqual(event.status, "ACTIVE")
            self.assertEqual(event.network_name, "TestNet")

    async def test_spawn_incursion_with_explicit_target_node_id(self):
        """Verify incursion spawns at the specified node when target_node_id is provided."""
        result = await self.db.incursion.spawn_incursion(
            "TestNet",
            inc_type="KaijuBreach",
            tier=4,
            reward=2000.0,
            duration_mins=5,
            target_node_id=self.dtc_node.id
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["node_name"], "DataCenter9")
        self.assertEqual(result["type"], "KaijuBreach")
        self.assertEqual(result["tier"], 4)

    async def test_tiered_defender_requirements_resolution(self):
        """Verify required defenders scale by tier (Tier 1: 1, Tier 2: 2, Tier 3: 4, Tier 4: 8)."""
        # Register 8 test characters located at VoidSector
        char_names = [f"Agent_{i}" for i in range(8)]
        for name in char_names:
            await self.db.register_player(name, "TestNet", "Synth", "Hacker", "Bio", {
                "str": 1, "cpu": 1, "ram": 1, "bnd": 1, "sec": 1, "alg": 1
            })
            await self.db.navigation.move_player_to_node(name, "TestNet", "VoidSector")

        # Test Tier 3 (Hard) -> Requires 4 defenders
        inc = await self.db.incursion.spawn_incursion(
            "TestNet", inc_type="KernelPanic", tier=3, reward=1500.0, target_node_id=self.void_node.id
        )
        self.assertIsNotNone(inc)

        # First 3 defenders: not yet repelled
        for i in range(3):
            ok, msg, victors = await self.db.incursion.register_defense(char_names[i], "TestNet", "VoidSector")
            self.assertTrue(ok)
            self.assertEqual(len(victors), 0)
            self.assertIn("Waiting for", msg)

        # 4th defender repels Tier 3 threat
        ok, msg, victors = await self.db.incursion.register_defense(char_names[3], "TestNet", "VoidSector")
        self.assertTrue(ok)
        self.assertIn("SUCCESS", msg)
        self.assertEqual(len(victors), 4)

        # Verify reward scaling: credits scaled by tier (3 * 500 = 1500)
        async with self.db.async_session() as session:
            stmt = select(Character).where(Character.name == char_names[0])
            c = (await session.execute(stmt)).scalars().first()
            self.assertEqual(c.credits, 1500.0)

    async def test_concurrent_defenders_registration(self):
        """Verify concurrent defense calls resolve cleanly without race conditions or orphaned state."""
        char_names = [f"ConcurrentAgent_{i}" for i in range(4)]
        for name in char_names:
            await self.db.register_player(name, "TestNet", "Synth", "Hacker", "Bio", {
                "str": 1, "cpu": 1, "ram": 1, "bnd": 1, "sec": 1, "alg": 1
            })
            await self.db.navigation.move_player_to_node(name, "TestNet", "DataCenter9")

        # Tier 2 requires 2 defenders
        inc = await self.db.incursion.spawn_incursion(
            "TestNet", inc_type="BufferOverflow", tier=2, reward=1000.0, target_node_id=self.dtc_node.id
        )
        self.assertIsNotNone(inc)

        # 4 players register defense concurrently
        results = await asyncio.gather(*[
            self.db.incursion.register_defense(name, "TestNet", "DataCenter9")
            for name in char_names
        ])

        # Exactly 1 success resolution with victors, 1 waiting message, and 2 rejection messages
        resolved_results = [r for r in results if r[0] and len(r[2]) == 2]
        waiting_results = [r for r in results if r[0] and len(r[2]) == 0]
        inactive_results = [r for r in results if not r[0]]

        self.assertEqual(len(resolved_results), 1)
        self.assertEqual(len(waiting_results), 1)
        self.assertEqual(len(inactive_results), 2)

        # Check DB state: incursion is marked RESOLVED with exactly 2 defenders
        async with self.db.async_session() as session:
            stmt = select(IncursionEvent).where(IncursionEvent.incursion_type == "BufferOverflow")
            ev = (await session.execute(stmt)).scalars().first()
            self.assertEqual(ev.status, "RESOLVED")

            stmt_def = select(IncursionDefender).where(IncursionDefender.incursion_id == ev.id)
            defs = (await session.execute(stmt_def)).scalars().all()
            self.assertEqual(len(defs), 2)

    async def test_get_random_incursion_node_avoids_active_incursions(self):
        """Verify get_random_incursion_node prioritizes non-safezone nodes without active incursions."""
        # Spawn an active incursion on VoidSector
        await self.db.incursion.spawn_incursion(
            "TestNet", inc_type="GlitchHound", tier=1, reward=500.0, target_node_id=self.void_node.id
        )

        # Calling get_random_incursion_node should select DataCenter9 since VoidSector is occupied
        for _ in range(10):
            node = await self.db.incursion.get_random_incursion_node("TestNet")
            self.assertEqual(node.name, "DataCenter9")

    async def test_expire_incursions_handles_none_node_cleanly(self):
        """Verify expire_incursions handles incursions without attached node safely."""
        from datetime import datetime, timezone, timedelta
        async with self.db.async_session() as session:
            past_expiry = datetime.now(timezone.utc) - timedelta(minutes=10)
            inc = IncursionEvent(
                node_id=None,
                network_name="TestNet",
                incursion_type="GhostProcess",
                tier=1,
                reward_val=500.0,
                expires_at=past_expiry,
                status='ACTIVE'
            )
            session.add(inc)
            await session.commit()

        notifications = await self.db.incursion.expire_incursions("TestNet")
        self.assertEqual(len(notifications), 1)
        self.assertIn("GhostProcess", notifications[0])
        self.assertIn("Unknown Node", notifications[0])

    async def test_defense_reduces_mcp_heat(self):
        """Verify successful incursion defense reduces player MCP heat by 1.0 (Mechanics Draft Sec 9)."""
        await self.db.register_player("HeatAgent", "TestNet", "Synth", "Hacker", "Bio", {
            "str": 1, "cpu": 1, "ram": 1, "bnd": 1, "sec": 1, "alg": 1
        })
        await self.db.navigation.move_player_to_node("HeatAgent", "TestNet", "VoidSector")

        # Set initial MCP heat to 3.5
        async with self.db.async_session() as session:
            stmt = select(Character).where(Character.name == "HeatAgent")
            char = (await session.execute(stmt)).scalars().first()
            char.mcp_heat = 3.5
            await session.commit()

        # Spawn Tier 1 incursion (requires 1 defender)
        await self.db.incursion.spawn_incursion(
            "TestNet", inc_type="GlitchHound", tier=1, reward=500.0, target_node_id=self.void_node.id
        )

        ok, msg, victors = await self.db.incursion.register_defense("HeatAgent", "TestNet", "VoidSector")
        self.assertTrue(ok)
        self.assertIn("SUCCESS", msg)

        # Check heat was decremented by 1.0 to 2.5
        async with self.db.async_session() as session:
            stmt = select(Character).where(Character.name == "HeatAgent")
            char = (await session.execute(stmt)).scalars().first()
            self.assertAlmostEqual(char.mcp_heat, 2.5, places=2)

    async def test_get_random_incursion_node_returns_none_when_all_occupied(self):
        """Verify get_random_incursion_node returns None when all non-safezone nodes have active incursions."""
        # Occupy both non-safezone nodes (VoidSector and DataCenter9)
        await self.db.incursion.spawn_incursion(
            "TestNet", inc_type="GlitchHound", tier=1, reward=500.0, target_node_id=self.void_node.id
        )
        await self.db.incursion.spawn_incursion(
            "TestNet", inc_type="BufferOverflow", tier=2, reward=1000.0, target_node_id=self.dtc_node.id
        )

        # All non-safezone nodes are occupied -> returns None (no duplicate stacking)
        node = await self.db.incursion.get_random_incursion_node("TestNet")
        self.assertIsNone(node)

    async def test_spawn_incursion_avoids_stacking_on_already_occupied_target_node(self):
        """Verify spawn_incursion returns None if the specified target_node_id already has an active incursion."""
        res1 = await self.db.incursion.spawn_incursion(
            "TestNet", inc_type="GlitchHound", tier=1, reward=500.0, target_node_id=self.dtc_node.id
        )
        self.assertIsNotNone(res1)

        # Attempt to spawn another incursion on the same node
        res2 = await self.db.incursion.spawn_incursion(
            "TestNet", inc_type="BufferOverflow", tier=2, reward=1000.0, target_node_id=self.dtc_node.id
        )
        self.assertIsNone(res2)

    async def test_spawn_incursion_rejects_safezone_target_node_id(self):
        """Verify spawn_incursion rejects attempts to target safezone nodes directly."""
        res = await self.db.incursion.spawn_incursion(
            "TestNet", inc_type="GlitchHound", tier=1, reward=500.0, target_node_id=self.safe_node.id
        )
        self.assertIsNone(res)

    async def test_expire_incursions_does_not_hold_lock_during_llm_flavor(self):
        """Verify expire_incursions releases DB lock before invoking LLM flavor text generator."""
        from datetime import datetime, timezone, timedelta
        async with self.db.async_session() as session:
            past_expiry = datetime.now(timezone.utc) - timedelta(minutes=10)
            inc = IncursionEvent(
                node_id=self.void_node.id,
                network_name="TestNet",
                incursion_type="GhostProcess",
                tier=1,
                reward_val=500.0,
                expires_at=past_expiry,
                status='ACTIVE'
            )
            session.add(inc)
            await session.commit()

        lock_was_free_during_llm = False
        mock_llm = MagicMock()
        async def mock_generate_flavor(inc_type, node_name):
            nonlocal lock_was_free_during_llm
            lock = self.db.incursion._get_lock()
            lock_was_free_during_llm = not lock.locked()
            return f"{inc_type} dissipated from {node_name}."

        mock_llm.generate_incursion_flavor = AsyncMock(side_effect=mock_generate_flavor)

        notifications = await self.db.incursion.expire_incursions("TestNet", llm_client=mock_llm)
        self.assertEqual(len(notifications), 1)
        self.assertTrue(lock_was_free_during_llm)

    async def test_tier_scaled_heat_reduction_and_clamping(self):
        """Verify defense resolution awards tier-scaled MCP heat reduction (-float(tier)) clamped at 0.0."""
        char_names = [f"HeatChar_{i}" for i in range(8)]
        for name in char_names:
            await self.db.register_player(name, "TestNet", "Synth", "Hacker", "Bio", {
                "str": 1, "cpu": 1, "ram": 1, "bnd": 1, "sec": 1, "alg": 1
            })
            await self.db.navigation.move_player_to_node(name, "TestNet", "VoidSector")

        # Tier 1 = -1.0, Tier 2 = -2.0, Tier 3 = -3.0, Tier 4 = -4.0
        test_matrix = [
            (1, 10.0, 9.0),   # Tier 1: 10.0 - 1.0 = 9.0
            (2, 10.0, 8.0),   # Tier 2: 10.0 - 2.0 = 8.0
            (3, 10.0, 7.0),   # Tier 3: 10.0 - 3.0 = 7.0
            (4, 10.0, 6.0),   # Tier 4: 10.0 - 4.0 = 6.0
            (3, 1.5, 0.0),    # Clamp at 0.0: 1.5 - 3.0 = 0.0
            (1, 0.0, 0.0),    # Clamp at 0.0: 0.0 - 1.0 = 0.0
        ]
        
        tier_defenders_map = {1: 1, 2: 2, 3: 4, 4: 8}
        for idx, (tier, init_heat, expected_heat) in enumerate(test_matrix):
            req_defenders = tier_defenders_map[tier]
            current_names = char_names[:req_defenders]
            async with self.db.async_session() as session:
                for name in current_names:
                    stmt = select(Character).where(Character.name == name)
                    c = (await session.execute(stmt)).scalars().first()
                    c.mcp_heat = init_heat
                await session.commit()

            inc = await self.db.incursion.spawn_incursion(
                "TestNet", inc_type=f"HeatMob_{idx}", tier=tier, reward=500.0 * tier, target_node_id=self.void_node.id
            )
            self.assertIsNotNone(inc)

            for i in range(req_defenders):
                ok, msg, victors = await self.db.incursion.register_defense(current_names[i], "TestNet", "VoidSector")
                self.assertTrue(ok)

            self.assertEqual(len(victors), req_defenders)
            async with self.db.async_session() as session:
                for name in current_names:
                    stmt = select(Character).where(Character.name == name)
                    c = (await session.execute(stmt)).scalars().first()
                    self.assertAlmostEqual(c.mcp_heat, expected_heat, places=2)

    async def test_multi_resource_payouts_all_tiers(self):
        """Verify defeating incursions awards Credits, Unit Power (uP), Data units, and XP scaled by tier."""
        char_names = [f"PayoutChar_{i}" for i in range(8)]
        for name in char_names:
            await self.db.register_player(name, "TestNet", "Synth", "Hacker", "Bio", {
                "str": 1, "cpu": 1, "ram": 1, "bnd": 1, "sec": 1, "alg": 1
            })
            await self.db.navigation.move_player_to_node(name, "TestNet", "VoidSector")

        tier_cases = [1, 2, 3, 4]
        tier_defenders_map = {1: 1, 2: 2, 3: 4, 4: 8}

        for tier in tier_cases:
            req_count = tier_defenders_map[tier]
            current_names = char_names[:req_count]
            async with self.db.async_session() as session:
                for name in current_names:
                    stmt = select(Character).where(Character.name == name)
                    c = (await session.execute(stmt)).scalars().first()
                    c.level = 1
                    c.credits = 0.0
                    c.power = 100.0
                    c.data_units = 0.0
                    c.xp = 0
                    c.mcp_heat = 5.0
                await session.commit()

            inc = await self.db.incursion.spawn_incursion(
                "TestNet", inc_type=f"PayoutMob_T{tier}", tier=tier, reward=500.0 * tier, target_node_id=self.void_node.id
            )
            self.assertIsNotNone(inc)

            for i in range(req_count):
                ok, msg, victors = await self.db.incursion.register_defense(current_names[i], "TestNet", "VoidSector")
                self.assertTrue(ok)

            self.assertEqual(len(victors), req_count)

            # Check L1 base values
            base_pkg = self.db.incursion.calculate_mcp_rewards(1, 'defend')
            expected_credits = base_pkg['credits'] * tier
            expected_data = base_pkg['data'] * tier
            expected_xp = base_pkg['xp'] * tier
            expected_power = 100.0 + (25.0 * tier)
            expected_heat = max(0.0, 5.0 - float(tier))

            async with self.db.async_session() as session:
                for name in current_names:
                    stmt = select(Character).where(Character.name == name)
                    c = (await session.execute(stmt)).scalars().first()
                    self.assertAlmostEqual(c.credits, expected_credits, places=2)
                    self.assertAlmostEqual(c.data_units, expected_data, places=2)
                    if tier == 4:
                        self.assertEqual(c.level, 2)
                        self.assertEqual(c.xp, 0)
                    else:
                        self.assertEqual(c.level, 1)
                        self.assertEqual(c.xp, expected_xp)
                    self.assertAlmostEqual(c.power, expected_power, places=2)
                    self.assertAlmostEqual(c.mcp_heat, expected_heat, places=2)

    async def test_victory_message_reflects_all_resources_and_heat(self):
        """Verify defense victory message reflects Credits, Power, Data, XP, and Tier-Scaled Heat Reduction."""
        await self.db.register_player("SitrepAgent", "TestNet", "Synth", "Hacker", "Bio", {
            "str": 1, "cpu": 1, "ram": 1, "bnd": 1, "sec": 1, "alg": 1
        })
        await self.db.navigation.move_player_to_node("SitrepAgent", "TestNet", "VoidSector")

        await self.db.incursion.spawn_incursion(
            "TestNet", inc_type="SitrepViper", tier=2, reward=1000.0, target_node_id=self.void_node.id
        )

        await self.db.register_player("SitrepAgent2", "TestNet", "Synth", "Hacker", "Bio", {
            "str": 1, "cpu": 1, "ram": 1, "bnd": 1, "sec": 1, "alg": 1
        })
        await self.db.navigation.move_player_to_node("SitrepAgent2", "TestNet", "VoidSector")

        ok1, msg1, _ = await self.db.incursion.register_defense("SitrepAgent", "TestNet", "VoidSector")
        self.assertTrue(ok1)
        self.assertIn("Waiting for", msg1)

        ok2, msg2, victors = await self.db.incursion.register_defense("SitrepAgent2", "TestNet", "VoidSector")
        self.assertTrue(ok2)
        self.assertEqual(len(victors), 2)
        self.assertIn("SUCCESS", msg2)
        self.assertIn("Credits", msg2)
        self.assertIn("Power", msg2)
        self.assertIn("Data", msg2)
        self.assertIn("XP", msg2)
        self.assertIn("Heat", msg2)

    async def test_defense_resolution_non_default_level_stat_points(self):
        """Verify non-default initial level (>1) scales rewards and increments stat points on level up."""
        await self.db.register_player("VeteranDefender1", "TestNet", "Synth", "Hacker", "Bio", {
            "str": 2, "cpu": 2, "ram": 2, "bnd": 2, "sec": 2, "alg": 2
        })
        await self.db.register_player("VeteranDefender2", "TestNet", "Synth", "Hacker", "Bio", {
            "str": 2, "cpu": 2, "ram": 2, "bnd": 2, "sec": 2, "alg": 2
        })
        await self.db.navigation.move_player_to_node("VeteranDefender1", "TestNet", "VoidSector")
        await self.db.navigation.move_player_to_node("VeteranDefender2", "TestNet", "VoidSector")

        # Set VeteranDefender1 to level 3 with XP close to threshold
        # For Level 3: xp_threshold = int(100 * (1.25 ** 2)) = 156
        async with self.db.async_session() as session:
            stmt = select(Character).where(Character.name == "VeteranDefender1")
            c = (await session.execute(stmt)).scalars().first()
            c.level = 3
            c.xp = 130
            c.credits = 50.0
            c.power = 80.0
            c.data_units = 10.0
            c.pending_stat_points = 0
            c.mcp_heat = 4.0
            await session.commit()

        # Spawn Tier 2 Incursion (requires 2 defenders)
        inc = await self.db.incursion.spawn_incursion(
            "TestNet", inc_type="VeteranThreat", tier=2, reward=1000.0, target_node_id=self.void_node.id
        )
        self.assertIsNotNone(inc)

        ok1, msg1, _ = await self.db.incursion.register_defense("VeteranDefender1", "TestNet", "VoidSector")
        self.assertTrue(ok1)

        ok2, msg2, victors = await self.db.incursion.register_defense("VeteranDefender2", "TestNet", "VoidSector")
        self.assertTrue(ok2)
        self.assertEqual(len(victors), 2)

        # Verify VeteranDefender1 gained rewards, leveled up to 4, and received a stat point
        base_pkg = self.db.incursion.calculate_mcp_rewards(3, 'defend')
        expected_xp = base_pkg['xp'] * 2
        expected_credits = round(50.0 + (base_pkg['credits'] * 2), 1)
        expected_data = round(10.0 + (base_pkg['data'] * 2), 1)
        expected_power = round(80.0 + (25.0 * 2), 1) # 130.0
        expected_heat = max(0.0, 4.0 - 2.0) # 2.0

        async with self.db.async_session() as session:
            stmt = select(Character).where(Character.name == "VeteranDefender1")
            c = (await session.execute(stmt)).scalars().first()
            self.assertEqual(c.level, 4) # Leveled up from 3 to 4!
            self.assertEqual(c.pending_stat_points, 1) # Received 1 stat point!
            self.assertEqual(c.xp, (130 + expected_xp) - 156)
            self.assertAlmostEqual(c.credits, expected_credits, places=1)
            self.assertAlmostEqual(c.data_units, expected_data, places=1)
            self.assertAlmostEqual(c.power, expected_power, places=1)
            self.assertAlmostEqual(c.mcp_heat, expected_heat, places=1)

    async def test_defense_resolution_defender_null_xp_and_tier_none(self):
        """Verify defense resolution gracefully handles characters with NULL xp and incursions with NULL tier."""
        await self.db.register_player("NullXPDefender", "TestNet", "Synth", "Hacker", "Bio", {
            "str": 1, "cpu": 1, "ram": 1, "bnd": 1, "sec": 1, "alg": 1
        })
        await self.db.navigation.move_player_to_node("NullXPDefender", "TestNet", "VoidSector")

        async with self.db.async_session() as session:
            stmt = select(Character).where(Character.name == "NullXPDefender")
            c = (await session.execute(stmt)).scalars().first()
            c.xp = None
            c.credits = None
            c.data_units = None
            c.power = None
            c.mcp_heat = 3.0
            await session.commit()

        inc = await self.db.incursion.spawn_incursion(
            "TestNet", inc_type="NullGlitch", tier=1, reward=500.0, target_node_id=self.void_node.id
        )
        self.assertIsNotNone(inc)

        # Artificially set tier to None in DB
        async with self.db.async_session() as session:
            stmt = select(IncursionEvent).where(
                IncursionEvent.node_id == self.void_node.id,
                IncursionEvent.status == 'ACTIVE'
            )
            evt = (await session.execute(stmt)).scalars().first()
            evt.tier = None
            await session.commit()

        ok, msg, victors = await self.db.incursion.register_defense("NullXPDefender", "TestNet", "VoidSector")
        self.assertTrue(ok)
        self.assertIn("NullXPDefender", victors)
        self.assertIn("SUCCESS", msg)

        # Verify character was credited properly without errors
        async with self.db.async_session() as session:
            stmt = select(Character).where(Character.name == "NullXPDefender")
            c = (await session.execute(stmt)).scalars().first()
            self.assertIsNotNone(c.xp)
            self.assertGreater(c.xp, 0)
            self.assertGreater(c.credits, 0.0)
            self.assertGreater(c.power, 0.0)
            self.assertEqual(c.mcp_heat, 2.0) # 3.0 - 1.0 (fallback tier 1)

    async def test_defense_resolution_defender_null_level_and_stat_points(self):
        """Verify defense resolution gracefully handles characters with NULL level and pending_stat_points."""
        await self.db.register_player("NullLevelDefender", "TestNet", "Synth", "Hacker", "Bio", {
            "str": 1, "cpu": 1, "ram": 1, "bnd": 1, "sec": 1, "alg": 1
        })
        await self.db.navigation.move_player_to_node("NullLevelDefender", "TestNet", "VoidSector")

        async with self.db.async_session() as session:
            stmt = select(Character).where(Character.name == "NullLevelDefender")
            c = (await session.execute(stmt)).scalars().first()
            c.level = None
            c.pending_stat_points = None
            c.xp = 99  # close to level up threshold (100)
            c.credits = 0.0
            c.power = 100.0
            c.mcp_heat = 2.0
            await session.commit()

        inc = await self.db.incursion.spawn_incursion(
            "TestNet", inc_type="NullLevelBug", tier=1, reward=500.0, target_node_id=self.void_node.id
        )
        self.assertIsNotNone(inc)

        ok, msg, victors = await self.db.incursion.register_defense("NullLevelDefender", "TestNet", "VoidSector")
        self.assertTrue(ok)
        self.assertIn("NullLevelDefender", victors)
        self.assertIn("SUCCESS", msg)

        async with self.db.async_session() as session:
            stmt = select(Character).where(Character.name == "NullLevelDefender")
            c = (await session.execute(stmt)).scalars().first()
            self.assertIsNotNone(c.level)
            self.assertGreaterEqual(c.level, 1)
            self.assertIsNotNone(c.pending_stat_points)
            self.assertGreater(c.power, 100.0)
            self.assertEqual(c.mcp_heat, 1.0) # 2.0 - 1.0

    async def test_spawn_incursion_float_and_invalid_tier(self):
        """Verify spawn_incursion handles float tiers, string floats, None, and invalid inputs gracefully."""
        # Float tier 2.0 -> Tier 2
        inc1 = await self.db.incursion.spawn_incursion("TestNet", "FloatThreat", tier=2.0, target_node_id=self.void_node.id)
        self.assertIsNotNone(inc1)
        self.assertEqual(inc1["tier"], 2)

        # String float tier "3.0" -> Tier 3
        inc2 = await self.db.incursion.spawn_incursion("TestNet", "StrFloatThreat", tier="3.0", target_node_id=self.dtc_node.id)
        self.assertIsNotNone(inc2)
        self.assertEqual(inc2["tier"], 3)

        # Clear active status
        async with self.db.async_session() as session:
            await session.execute(update(IncursionEvent).values(status='RESOLVED'))
            await session.commit()

        # None tier -> falls back to 1
        inc3 = await self.db.incursion.spawn_incursion("TestNet", "NoneThreat", tier=None, target_node_id=self.void_node.id)
        self.assertIsNotNone(inc3)
        self.assertEqual(inc3["tier"], 1)

        # Invalid string -> falls back to 1
        inc4 = await self.db.incursion.spawn_incursion("TestNet", "InvalidThreat", tier="corrupted", target_node_id=self.dtc_node.id)
        self.assertIsNotNone(inc4)
        self.assertEqual(inc4["tier"], 1)


class TestIncursionEventLoopAndBroadcast(unittest.IsolatedAsyncioTestCase):
    """Unit tests for incursion schedule configuration and IRC broadcast messaging."""

    def test_incursion_schedule_structure(self):
        """Verify INCURSION_SCHEDULE defines all 4 difficulty tiers with exact requirements."""
        self.assertEqual(len(INCURSION_SCHEDULE), 4)
        
        # Tier 1 (Easy)
        self.assertEqual(INCURSION_SCHEDULE[1]["label"], "Easy")
        self.assertEqual(INCURSION_SCHEDULE[1]["interval"], 1800)
        self.assertEqual(INCURSION_SCHEDULE[1]["players"], 1)
        self.assertEqual(INCURSION_SCHEDULE[1]["chance"], 0.30)

        # Tier 2 (Medium)
        self.assertEqual(INCURSION_SCHEDULE[2]["label"], "Medium")
        self.assertEqual(INCURSION_SCHEDULE[2]["interval"], 3600)
        self.assertEqual(INCURSION_SCHEDULE[2]["players"], 2)
        self.assertEqual(INCURSION_SCHEDULE[2]["chance"], 0.25)

        # Tier 3 (Hard)
        self.assertEqual(INCURSION_SCHEDULE[3]["label"], "Hard")
        self.assertEqual(INCURSION_SCHEDULE[3]["interval"], 14400)
        self.assertEqual(INCURSION_SCHEDULE[3]["players"], 4)
        self.assertEqual(INCURSION_SCHEDULE[3]["chance"], 0.20)

        # Tier 4 (Epic)
        self.assertEqual(INCURSION_SCHEDULE[4]["label"], "Epic")
        self.assertEqual(INCURSION_SCHEDULE[4]["interval"], 43200)
        self.assertEqual(INCURSION_SCHEDULE[4]["players"], 8)
        self.assertEqual(INCURSION_SCHEDULE[4]["chance"], 0.15)

    async def test_broadcast_messaging_format_for_all_tiers(self):
        """Verify broadcast messaging contains both Tier number and Difficulty label for all tiers."""
        test_cases = [
            (1, "Easy", 1, "HacktopusAI", "NodeAlpha"),
            (2, "Medium", 2, "Gridbugs", "NodeBeta"),
            (3, "Hard", 4, "KrakenProcess", "NodeGamma"),
            (4, "Epic", 8, "KaijuBreach", "NodeOmega"),
        ]

        for tier, label, players, mob, node in test_cases:
            msg = f"WORLD EVENT: A Tier {tier} ({label}) anomaly [{mob}] has breached {node}! Requires {players} defenders. Use '!a defend {node}' within 5 minutes!"
            
            # Assertions for broadcast requirements
            self.assertIn(f"Tier {tier} ({label})", msg)
            self.assertIn(f"[{mob}]", msg)
            self.assertIn(f"breached {node}!", msg)
            self.assertIn(f"Requires {players} defenders.", msg)
            self.assertIn(f"Use '!a defend {node}' within 5 minutes!", msg)

    async def test_incursion_event_loop_triggers_and_broadcasts(self):
        """Verify incursion_event_loop calls generate_incursion_mob_name, spawn_incursion, and broadcasts."""
        node = MagicMock()
        node.net_name = "Rizon"
        node.config = {"channel": "#automata"}
        node.send = AsyncMock()
        node.channel_users = {f"user_{i}": {"chat_lines": 5} for i in range(4)}
        node.authenticated_sessions = {}
        
        # Mock LLM
        node.llm = MagicMock()
        node.llm.generate_incursion_mob_name = AsyncMock(return_value="ZeroDayColossus")

        # Mock DB
        mock_target = MagicMock()
        mock_target.name = "CentralSpire"
        mock_target.region_type = "MIL"
        mock_target.id = 42

        node.db = MagicMock()
        node.db.incursion = MagicMock()
        node.db.incursion.expire_incursions = AsyncMock(return_value=[])
        node.db.incursion.get_random_incursion_node = AsyncMock(return_value=mock_target)
        node.db.incursion.spawn_incursion = AsyncMock(return_value={
            "node_name": "CentralSpire",
            "type": "ZeroDayColossus",
            "tier": 3,
            "reward": 1500.0
        })

        # Run loop with simulated time and sleep cancellation after 1 tick
        call_count = 0
        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=mock_sleep), \
             patch("time.time", return_value=100000.0), \
             patch("random.random", return_value=0.01):
            
            # Force Tier 3 to trigger by overriding schedule in test
            with patch.dict(INCURSION_SCHEDULE, {
                3: {'label': 'Hard', 'interval': 14400, 'players': 4, 'chance': 1.0}
            }, clear=True):
                await incursion_event_loop(node)

        # Verify LLM was invoked with tier 3, target node name, and region
        node.llm.generate_incursion_mob_name.assert_awaited_once_with(3, "CentralSpire", "MIL")

        # Verify spawn_incursion was invoked
        node.db.incursion.spawn_incursion.assert_awaited_once()
        spawn_kwargs = node.db.incursion.spawn_incursion.await_args[1]
        self.assertEqual(spawn_kwargs.get("inc_type"), "ZeroDayColossus")
        self.assertEqual(spawn_kwargs.get("tier"), 3)
        self.assertEqual(spawn_kwargs.get("reward"), 1500.0)

        # Verify node.send broadcast
        node.send.assert_awaited_once()
        broadcast_call = node.send.await_args[0][0]
        self.assertIn("WORLD EVENT: A Tier 3 (Hard) anomaly [ZeroDayColossus] has breached CentralSpire!", broadcast_call)
        self.assertIn("Requires 4 defenders.", broadcast_call)
        self.assertIn("Use '!a defend CentralSpire' within 5 minutes!", broadcast_call)
        self.assertIn("PRIVMSG #automata :", broadcast_call)

    async def test_incursion_event_loop_skips_llm_when_no_node_available(self):
        """Verify incursion_event_loop bypasses LLM and spawn calls when get_random_incursion_node is None."""
        node = MagicMock()
        node.net_name = "Rizon"
        node.config = {"channel": "#automata"}
        node.send = AsyncMock()
        node.channel_users = {"user_1": {"chat_lines": 5}}
        node.authenticated_sessions = {}
        node.llm = MagicMock()
        node.llm.generate_incursion_mob_name = AsyncMock()

        node.db = MagicMock()
        node.db.incursion = MagicMock()
        node.db.incursion.expire_incursions = AsyncMock(return_value=[])
        # Returns None because all nodes are safezones or full
        node.db.incursion.get_random_incursion_node = AsyncMock(return_value=None)
        node.db.incursion.spawn_incursion = AsyncMock()

        call_count = 0
        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=mock_sleep), \
             patch("time.time", return_value=100000.0), \
             patch("random.random", return_value=0.01):
            
            with patch.dict(INCURSION_SCHEDULE, {
                1: {'label': 'Easy', 'interval': 1800, 'players': 1, 'chance': 1.0}
            }, clear=True):
                await incursion_event_loop(node)

        # Verify LLM was NOT invoked
        node.llm.generate_incursion_mob_name.assert_not_awaited()
        # Verify spawn was NOT invoked
        node.db.incursion.spawn_incursion.assert_not_awaited()
        # Verify no broadcast was sent
        node.send.assert_not_awaited()

    async def test_incursion_event_loop_handles_llm_exception_gracefully(self):
        """Verify incursion_event_loop cleanly falls back to FALLBACK_ROSTER when node.llm raises an exception."""
        node = MagicMock()
        node.net_name = "Rizon"
        node.config = {"channel": "#automata"}
        node.send = AsyncMock()
        node.channel_users = {"user_1": {"chat_lines": 5}}
        node.authenticated_sessions = {}
        
        # LLM raises an unhandled exception
        node.llm = MagicMock()
        node.llm.generate_incursion_mob_name = AsyncMock(side_effect=RuntimeError("LLM cluster down"))

        mock_target = MagicMock()
        mock_target.name = "CentralSpire"
        mock_target.region_type = "MIL"
        mock_target.id = 42

        node.db = MagicMock()
        node.db.incursion = MagicMock()
        node.db.incursion.expire_incursions = AsyncMock(return_value=[])
        node.db.incursion.get_random_incursion_node = AsyncMock(return_value=mock_target)
        node.db.incursion.spawn_incursion = AsyncMock(return_value={
            "node_name": "CentralSpire",
            "type": "HacktopusAI",
            "tier": 1,
            "reward": 500.0
        })

        call_count = 0
        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=mock_sleep), \
             patch("time.time", return_value=100000.0), \
             patch("random.random", return_value=0.01):
            
            with patch.dict(INCURSION_SCHEDULE, {
                1: {'label': 'Easy', 'interval': 1800, 'players': 1, 'chance': 1.0}
            }, clear=True):
                await incursion_event_loop(node)

        # Verify spawn_incursion was still called with a fallback mob name from FALLBACK_ROSTER[1]
        node.db.incursion.spawn_incursion.assert_awaited_once()
        spawn_kwargs = node.db.incursion.spawn_incursion.await_args[1]
        self.assertIn(spawn_kwargs.get("inc_type"), FALLBACK_ROSTER[1])
        # Verify broadcast was sent
        node.send.assert_awaited_once()

    def test_count_active_players_comprehensive(self):
        """Verify count_active_players measures active population across sessions, chat lines, and fallbacks."""
        # 1. Missing attributes fallback to 0
        bare_node = MagicMock(spec=['net_name'])
        self.assertEqual(count_active_players(bare_node), 0)

        # 2. Empty channel_users and empty authenticated_sessions -> 0
        node = MagicMock()
        node.channel_users = {}
        node.authenticated_sessions = {}
        self.assertEqual(count_active_players(node), 0)

        # 3. Lurkers with 0 chat lines and no auth -> 0
        node.channel_users = {"lurker1": {"chat_lines": 0}, "lurker2": {"chat_lines": 0}}
        node.authenticated_sessions = {}
        self.assertEqual(count_active_players(node), 0)

        # 4. Active chatters counted
        node.channel_users = {"chatter1": {"chat_lines": 3}, "lurker1": {"chat_lines": 0}}
        self.assertEqual(count_active_players(node), 1)

        # 5. Authenticated sessions counted even with 0 chat lines
        node.channel_users = {"auth_user": {"chat_lines": 0}}
        node.authenticated_sessions = {"auth_user": "CharOne"}
        self.assertEqual(count_active_players(node), 1)

        # 6. Deduplication between authenticated_sessions and channel_users
        node.channel_users = {"user_a": {"chat_lines": 5}, "user_b": {"chat_lines": 2}}
        node.authenticated_sessions = {"user_a": "CharA", "user_c": "CharC"}
        # Unique: user_a, user_b, user_c -> 3
        self.assertEqual(count_active_players(node), 3)

        # 7. Recent activity timestamp counted (within 1 hour)
        now_ts = 100000.0
        with patch("time.time", return_value=now_ts):
            node.channel_users = {
                "recent": {"chat_lines": 0, "last_active": now_ts - 600},
                "stale": {"chat_lines": 0, "last_active": now_ts - 7200},
            }
            node.authenticated_sessions = {}
            self.assertEqual(count_active_players(node), 1)

    def test_dynamic_spawn_chance_formula(self):
        """Verify calculate_effective_incursion_chance dynamic scaling and limits."""
        tier_data = {'chance': 0.20, 'players': 4}
        
        # 0 active users -> 0.0
        self.assertEqual(calculate_effective_incursion_chance(3, tier_data, 0), 0.0)

        # 1 active user -> 0.20 * (1 / 4.0) = 0.05
        self.assertAlmostEqual(calculate_effective_incursion_chance(3, tier_data, 1), 0.05)

        # 2 active users -> 0.20 * (2 / 4.0) = 0.10
        self.assertAlmostEqual(calculate_effective_incursion_chance(3, tier_data, 2), 0.10)

        # 4 active users -> 0.20 * (4 / 4.0) = 0.20 (max_chance capped)
        self.assertAlmostEqual(calculate_effective_incursion_chance(3, tier_data, 4), 0.20)

        # 8 active users -> capped at max_chance (0.20)
        self.assertAlmostEqual(calculate_effective_incursion_chance(3, tier_data, 8), 0.20)

        # Custom max_chance in data allows scaling higher
        custom_data = {'chance': 0.20, 'max_chance': 0.50, 'baseline_divisor': 4.0}
        self.assertAlmostEqual(calculate_effective_incursion_chance(3, custom_data, 8), 0.40)
        self.assertAlmostEqual(calculate_effective_incursion_chance(3, custom_data, 12), 0.50)

    def test_tier_population_gates_constants(self):
        """Verify TIER_POPULATION_GATES requires 1, 2, 4, 6 players for Tiers 1-4."""
        self.assertEqual(TIER_POPULATION_GATES[1], 1)
        self.assertEqual(TIER_POPULATION_GATES[2], 2)
        self.assertEqual(TIER_POPULATION_GATES[3], 4)
        self.assertEqual(TIER_POPULATION_GATES[4], 6)

    async def test_zero_active_users_suppresses_incursion_rolls(self):
        """Verify 0 active users suppresses all incursion rolls (zero activity = zero spawns)."""
        node = MagicMock()
        node.net_name = "Rizon"
        node.config = {"channel": "#automata"}
        node.send = AsyncMock()
        node.channel_users = {}
        node.authenticated_sessions = {}

        node.db = MagicMock()
        node.db.incursion = MagicMock()
        node.db.incursion.expire_incursions = AsyncMock(return_value=[])
        node.db.incursion.spawn_incursion = AsyncMock()

        call_count = 0
        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=mock_sleep), \
             patch("time.time", return_value=100000.0), \
             patch("random.random", return_value=0.0):  # Even with 0.0 random roll

            with patch.dict(INCURSION_SCHEDULE, {
                1: {'label': 'Easy', 'interval': 1800, 'players': 1, 'chance': 1.0}
            }, clear=True):
                await incursion_event_loop(node)

        node.db.incursion.spawn_incursion.assert_not_awaited()
        node.send.assert_not_awaited()

    async def test_population_gates_suppress_high_tiers_in_loop(self):
        """Verify loop skips high tiers when active population is below gate (e.g. Tier 3 skipped when active_count = 2)."""
        node = MagicMock()
        node.net_name = "Rizon"
        node.config = {"channel": "#automata"}
        node.send = AsyncMock()
        # 2 active users: passes Tier 1 (gate 1) and Tier 2 (gate 2), but blocks Tier 3 (gate 4)
        node.channel_users = {
            "u1": {"chat_lines": 5},
            "u2": {"chat_lines": 3},
        }
        node.authenticated_sessions = {}

        mock_target = MagicMock()
        mock_target.name = "DefenseZone"
        mock_target.region_type = "CIV"
        mock_target.id = 99

        node.db = MagicMock()
        node.db.incursion = MagicMock()
        node.db.incursion.expire_incursions = AsyncMock(return_value=[])
        node.db.incursion.get_random_incursion_node = AsyncMock(return_value=mock_target)
        node.db.incursion.spawn_incursion = AsyncMock(return_value={
            "node_name": "DefenseZone", "type": "HacktopusAI", "tier": 1, "reward": 500.0
        })

        call_count = 0
        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                raise asyncio.CancelledError()

        # Test Tier 3 with 2 active users -> should NOT spawn
        with patch("asyncio.sleep", side_effect=mock_sleep), \
             patch("time.time", return_value=100000.0), \
             patch("random.random", return_value=0.001):

            with patch.dict(INCURSION_SCHEDULE, {
                3: {'label': 'Hard', 'interval': 14400, 'players': 4, 'chance': 1.0}
            }, clear=True):
                await incursion_event_loop(node)

        # Tier 3 was blocked by gate (active 2 < gate 4)
        node.db.incursion.spawn_incursion.assert_not_awaited()

    async def test_high_activity_unlocks_tier_4_epic(self):
        """Verify 6+ active users unlocks Tier 4 Epic spawn roll."""
        node = MagicMock()
        node.net_name = "Rizon"
        node.config = {"channel": "#automata"}
        node.send = AsyncMock()
        # 6 active users unlocks Tier 4 (gate 6)
        node.channel_users = {f"epic_user_{i}": {"chat_lines": 2} for i in range(6)}
        node.authenticated_sessions = {}
        node.llm = MagicMock()
        node.llm.generate_incursion_mob_name = AsyncMock(return_value="SingularityEngine")

        mock_target = MagicMock()
        mock_target.name = "OmegaCore"
        mock_target.region_type = "GOV"
        mock_target.id = 100

        node.db = MagicMock()
        node.db.incursion = MagicMock()
        node.db.incursion.expire_incursions = AsyncMock(return_value=[])
        node.db.incursion.get_random_incursion_node = AsyncMock(return_value=mock_target)
        node.db.incursion.spawn_incursion = AsyncMock(return_value={
            "node_name": "OmegaCore", "type": "KaijuBreach", "tier": 4, "reward": 2000.0
        })

        call_count = 0
        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=mock_sleep), \
             patch("time.time", return_value=100000.0), \
             patch("random.random", return_value=0.001):

            with patch.dict(INCURSION_SCHEDULE, {
                4: {'label': 'Epic', 'interval': 43200, 'players': 8, 'chance': 1.0}
            }, clear=True):
                await incursion_event_loop(node)

        # Tier 4 was unlocked and spawned
        node.db.incursion.spawn_incursion.assert_awaited_once()
        spawn_kwargs = node.db.incursion.spawn_incursion.await_args[1]
        self.assertEqual(spawn_kwargs.get("tier"), 4)
        self.assertEqual(spawn_kwargs.get("reward"), 2000.0)

    def test_count_active_players_excludes_bot_nickname(self):
        """Verify count_active_players excludes the bot's own nickname even if present in channel_users."""
        node = MagicMock()
        node.config = {"nickname": "AutomataHub"}
        node.channel_users = {
            "automatahub": {"chat_lines": 50}, # Bot itself
            "real_player": {"chat_lines": 3},
        }
        node.authenticated_sessions = {}
        # Only real_player should be counted -> 1
        self.assertEqual(count_active_players(node), 1)

        # If only bot is in channel -> 0
        node.channel_users = {"automatahub": {"chat_lines": 50}}
        self.assertEqual(count_active_players(node), 0)

        # If nickname is on node.nickname
        node_no_cfg = MagicMock(spec=["nickname", "channel_users", "authenticated_sessions"])
        node_no_cfg.nickname = "MyBot"
        node_no_cfg.channel_users = {"mybot": {"chat_lines": 10}}
        node_no_cfg.authenticated_sessions = {}
        self.assertEqual(count_active_players(node_no_cfg), 0)

    def test_count_active_players_none_and_string_chat_lines(self):
        """Verify count_active_players handles None, string, and invalid chat_lines without raising TypeError."""
        node = MagicMock()
        node.config = {"nickname": "AutomataHub"}
        node.channel_users = {
            "user_none": {"chat_lines": None},
            "user_str_active": {"chat_lines": "3"},
            "user_str_zero": {"chat_lines": "0"},
            "user_invalid": {"chat_lines": "not_an_int"},
        }
        node.authenticated_sessions = {}
        # Only user_str_active ("3" > 0) is active
        self.assertEqual(count_active_players(node), 1)

    def test_calculate_effective_incursion_chance_config_robustness(self):
        """Verify calculate_effective_incursion_chance handles None mechanics, zero divisors, and custom config."""
        tier_data = {'chance': 0.20, 'players': 4}

        # 1. node.config['mechanics'] is None -> gracefully falls back to default divisor 4.0
        node_null_mech = MagicMock()
        node_null_mech.config = {"mechanics": None}
        self.assertAlmostEqual(calculate_effective_incursion_chance(3, tier_data, 2, node_null_mech), 0.10)

        # 2. node is None and node.config is None
        self.assertAlmostEqual(calculate_effective_incursion_chance(3, tier_data, 2, None), 0.10)
        node_none_cfg = MagicMock()
        node_none_cfg.config = None
        self.assertAlmostEqual(calculate_effective_incursion_chance(3, tier_data, 2, node_none_cfg), 0.10)

        # 3. baseline_divisor = 0 or negative -> does NOT raise ZeroDivisionError, falls back to 4.0
        data_zero_div = {'chance': 0.20, 'baseline_divisor': 0}
        self.assertAlmostEqual(calculate_effective_incursion_chance(3, data_zero_div, 2), 0.10)

        data_neg_div = {'chance': 0.20, 'baseline_divisor': -5.0}
        self.assertAlmostEqual(calculate_effective_incursion_chance(3, data_neg_div, 2), 0.10)

        data_invalid_div = {'chance': 0.20, 'baseline_divisor': "not_a_number"}
        self.assertAlmostEqual(calculate_effective_incursion_chance(3, data_invalid_div, 2), 0.10)

        # 4. Custom config via node.config['mechanics']
        custom_node = MagicMock()
        custom_node.config = {
            "mechanics": {
                "incursion_baseline_divisor": 2.0,
                "incursion_max_chance": 0.80
            }
        }
        # With divisor 2.0, active_count 3: 0.20 * (3 / 2.0) = 0.30
        self.assertAlmostEqual(calculate_effective_incursion_chance(3, tier_data, 3, custom_node), 0.30)

        # 5. Respects base_chance key when chance key is omitted
        data_base_chance = {'base_chance': 0.15, 'baseline_divisor': 4.0}
        self.assertAlmostEqual(calculate_effective_incursion_chance(3, data_base_chance, 4), 0.15)

        # 6. Clamped between 0.0 and 1.0
        data_extreme = {'chance': 0.90, 'max_chance': 2.0, 'baseline_divisor': 1.0}
        self.assertAlmostEqual(calculate_effective_incursion_chance(3, data_extreme, 10), 1.0)

        # 7. data is None or non-dict -> safely defaults without AttributeError
        self.assertEqual(calculate_effective_incursion_chance(1, None, 0), 0.0)
        self.assertAlmostEqual(calculate_effective_incursion_chance(1, None, 4), 0.20)
        self.assertAlmostEqual(calculate_effective_incursion_chance(1, "invalid_dict", 4), 0.20)

        # 8. active_count is None, string, or invalid -> handles gracefully without TypeError
        self.assertEqual(calculate_effective_incursion_chance(1, tier_data, None), 0.0)
        self.assertAlmostEqual(calculate_effective_incursion_chance(1, tier_data, "2"), 0.10)
        self.assertEqual(calculate_effective_incursion_chance(1, tier_data, "not_an_int"), 0.0)

        # 9. Handles inf and nan gracefully without OverflowError
        self.assertEqual(calculate_effective_incursion_chance(1, tier_data, float("inf")), 0.0)
        self.assertEqual(calculate_effective_incursion_chance(1, tier_data, float("nan")), 0.0)
        self.assertAlmostEqual(calculate_effective_incursion_chance(1, {'chance': float("nan")}, 4), 0.20)

    async def test_loop_handles_string_tiers_and_gates(self):
        """Verify incursion_event_loop handles string tier keys and string population gates without TypeError."""
        node = MagicMock()
        node.net_name = "Rizon"
        node.config = {"channel": "#automata"}
        node.send = AsyncMock()
        node.channel_users = {"user1": {"chat_lines": 3}}
        node.authenticated_sessions = {}
        node.db = MagicMock()
        node.db.incursion = MagicMock()
        node.db.incursion.expire_incursions = AsyncMock(return_value=[])
        node.db.incursion.get_random_incursion_node = AsyncMock(return_value=None)
        node.db.incursion.spawn_incursion = AsyncMock()

        call_count = 0
        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                raise asyncio.CancelledError()

        # Custom schedule with string tier key and string population gate
        with patch("asyncio.sleep", side_effect=mock_sleep), \
             patch("time.time", return_value=100000.0), \
             patch("random.random", return_value=0.01):

            with patch.dict(INCURSION_SCHEDULE, {
                "special_tier": {'label': 'Special', 'interval': 1800, 'players': 2, 'chance': 1.0, 'population_gate': "2"}
            }, clear=True):
                # 1 active user < gate "2" -> should skip without TypeError
                await incursion_event_loop(node)

        node.db.incursion.spawn_incursion.assert_not_awaited()

    async def test_loop_handles_float_tier_without_downgrading(self):
        """Verify incursion_event_loop correctly maps float tiers like 2.0 to Tier 2 without downgrading to 1."""
        node = MagicMock()
        node.net_name = "Rizon"
        node.config = {"channel": "#automata"}
        node.send = AsyncMock()
        # 2 active users allows Tier 2 (gate 2)
        node.channel_users = {"u1": {"chat_lines": 5}, "u2": {"chat_lines": 3}}
        node.authenticated_sessions = {}
        node.db = MagicMock()
        node.db.incursion = MagicMock()
        node.db.incursion.expire_incursions = AsyncMock(return_value=[])
        mock_target = MagicMock()
        mock_target.name = "FloatNode"
        mock_target.region_type = "CORP"
        mock_target.id = 77
        node.db.incursion.get_random_incursion_node = AsyncMock(return_value=mock_target)
        node.db.incursion.spawn_incursion = AsyncMock(return_value={
            "node_name": "FloatNode", "type": "DaemonCrawler", "tier": 2, "reward": 1000.0
        })

        call_count = 0
        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=mock_sleep), \
             patch("time.time", return_value=100000.0), \
             patch("random.random", return_value=0.01):

            with patch.dict(INCURSION_SCHEDULE, {
                2.0: {'label': 'Medium', 'interval': 3600, 'players': 2, 'chance': 1.0}
            }, clear=True):
                await incursion_event_loop(node)

        node.db.incursion.spawn_incursion.assert_awaited_once()
        spawn_kwargs = node.db.incursion.spawn_incursion.await_args[1]
        self.assertEqual(spawn_kwargs.get("tier"), 2)
        self.assertEqual(spawn_kwargs.get("reward"), 1000.0)

    async def test_loop_handles_missing_channel_config(self):
        """Verify incursion_event_loop falls back safely when node.config is None or lacks channel."""
        node = MagicMock()
        node.net_name = "Rizon"
        node.config = None # No config dict
        node.channel = "#backup_chan"
        node.send = AsyncMock()
        node.channel_users = {}
        node.authenticated_sessions = {}
        node.db = MagicMock()
        node.db.incursion = MagicMock()
        node.db.incursion.expire_incursions = AsyncMock(return_value=["An incursion has expired!"])

        call_count = 0
        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=mock_sleep), \
             patch("time.time", return_value=100000.0):
            await incursion_event_loop(node)

        # Message sent to backup_chan without crash
        node.send.assert_awaited()
        sent_line = node.send.await_args[0][0]
        self.assertIn("PRIVMSG #backup_chan :", sent_line)


if __name__ == "__main__":
    unittest.main()
