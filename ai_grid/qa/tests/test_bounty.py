# ai_grid/qa/tests/test_bounty.py
# Comprehensive Async Unit Tests for the Bounty Board System:
# - Data model & repository operations (get_active_bounties, get_bounty_for_target, post_bounty, ensure_mcp_bounty, claim_bounty)
# - MCP automated bounties triggered by is_bounty_eligible (Heat > 6.0 and Hostile regions)
# - Player bounty posting, credit deduction, and reward stacking
# - Insufficient funds, minimum bounty amount, and self-bounty guards
# - IRC command handlers (!a bounty, !a bounty list, !a bounty check, !a bounty post) across human, text, narrative modes
# - Automated bounty claiming and channel announcements upon fatal PvP combat resolution
# - Self-claim (suicide) rejection

import asyncio
import datetime
import json
import os
import time
import unittest
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from ai_grid.models import Base, Player, NetworkAlias, Character, GridNode, Bounty
from ai_grid.grid_db import ArenaDB
from ai_grid.core.command_router import CommandRouter
from ai_grid.core.handlers.bounty import handle_bounty
from ai_grid.core.handlers.combat import handle_combat_bounty_claim, start_pvp_combat
from ai_grid.grid_combat import CombatEngine, Entity


class TestBountySystem(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db_path = f"test_bounty_{int(datetime.datetime.now().timestamp() * 1000)}.db"
        self.db = ArenaDB(self.db_path)
        self.async_session = self.db.async_session

        async with self.db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Mock Network Node for IRC & Combat integration
        self.node = MagicMock()
        self.node.net_name = "testnet"
        self.node.prefix = "!a"
        self.node.config = {
            "channel": "#arena",
            "cmd_prefix": "!a",
            "nickname": "GridBot",
            "admins": ["admin_user"]
        }
        self.node.db = self.db
        self.node.send = AsyncMock()
        self.node.active_engine = None
        self.node.authenticated_users = set()
        self.node.authenticated_sessions = {}
        self.node.action_timestamps = {}
        self.node.flood_config = {
            'max_tokens': 1000.0,
            'refill_rate': 100.0,
            'violation_threshold': 500,
            'lockout_duration': 30,
            'messages': {}
        }
        self.node.is_user_verified = lambda nick: True

        self.router = CommandRouter(self.node)
        self.node.router = self.router

    async def _wait_until(self, pred, timeout=2.0, interval=0.02):
        start = time.time()
        while time.time() - start < timeout:
            if pred():
                return True
            await asyncio.sleep(interval)
        return pred()

    async def asyncTearDown(self):
        if hasattr(self, 'db') and hasattr(self.db, 'engine'):
            await self.db.engine.dispose()
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except OSError:
                pass

    async def _create_character(
        self,
        name: str,
        credits: float = 1000.0,
        heat: float = 0.0,
        rep: dict = None,
        output_mode: str = "human",
        node_id: int = None
    ) -> Character:
        async with self.async_session() as session:
            player = Player(global_name=name)
            session.add(player)
            await session.flush()

            alias = NetworkAlias(player_id=player.id, network_name="testnet", nickname=name)
            session.add(alias)

            prefs = json.dumps({"output_mode": output_mode, "msg_type": "privmsg"})
            char = Character(
                player_id=player.id,
                node_id=node_id,
                name=name,
                race="Cyborg",
                char_class="Hacker",
                credits=credits,
                mcp_heat=heat,
                node_rep=rep or {},
                prefs=prefs,
                cpu=5, ram=5, bnd=5, sec=5, alg=5,
                power=100.0,
                stability=100.0,
                current_hp=100
            )
            session.add(char)
            await session.commit()
            await session.refresh(char)
            return char

    # =========================================================================
    # 1. DATA MODEL & REPOSITORY UNIT TESTS
    # =========================================================================

    async def test_bounty_model_and_empty_list(self):
        """Bounty table persists and returns empty list when no active bounties exist."""
        bounties = await self.db.get_active_bounties("testnet")
        self.assertEqual(bounties, [])

        target_bounty = await self.db.get_bounty_for_target("GhostPlayer", "testnet")
        self.assertIsNone(target_bounty)

    async def test_player_post_bounty_success_and_deduction(self):
        """Posting a player-funded bounty deducts credits and creates ACTIVE bounty."""
        alice = await self._create_character("Alice", credits=1000.0)
        bob = await self._create_character("Bob", credits=500.0)

        success, msg, bounty = await self.db.post_bounty("Alice", "Bob", "testnet", 300.0, "Corporate sabotage")
        self.assertTrue(success)
        self.assertIsNotNone(bounty)
        self.assertEqual(bounty.target_nick, "Bob")
        self.assertEqual(bounty.issuer_nick, "Alice")
        self.assertEqual(bounty.reward_credits, 300.0)
        self.assertEqual(bounty.status, "ACTIVE")
        self.assertEqual(bounty.reason, "Corporate sabotage")

        # Verify Alice's balance deducted
        async with self.async_session() as session:
            stmt = select(Character).where(Character.id == alice.id)
            c = (await session.execute(stmt)).scalars().first()
            self.assertEqual(c.credits, 700.0)

        # Verify retrieval via repository
        active = await self.db.get_active_bounties("testnet")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].target_nick, "Bob")

        b = await self.db.get_bounty_for_target("bob", "testnet")
        self.assertIsNotNone(b)
        self.assertEqual(b.reward_credits, 300.0)

    async def test_bounty_reward_stacking_on_same_target(self):
        """Posting additional bounty onto an existing wanted target stacks the reward credits."""
        await self._create_character("Alice", credits=1000.0)
        await self._create_character("Charlie", credits=1000.0)
        bob = await self._create_character("Bob", credits=500.0)

        # Alice posts 250c
        success1, _, b1 = await self.db.post_bounty("Alice", "Bob", "testnet", 250.0, "Breached firewall")
        self.assertTrue(success1)
        self.assertEqual(b1.reward_credits, 250.0)

        # Charlie posts 350c on same target Bob
        success2, msg2, b2 = await self.db.post_bounty("Charlie", "Bob", "testnet", 350.0, "Stole trade data")
        self.assertTrue(success2)
        self.assertIn("increased by 350.0c", msg2)
        self.assertIn("Total reward: 600.0c", msg2)

        # Verify single active bounty exists with stacked total 600c
        active = await self.db.get_active_bounties("testnet")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].reward_credits, 600.0)
        self.assertIn("Breached firewall", active[0].reason)
        self.assertIn("Stole trade data", active[0].reason)

    async def test_post_bounty_guards(self):
        """Validates guards: insufficient funds, minimum amount (100c), and self-bounty."""
        alice = await self._create_character("Alice", credits=50.0)
        bob = await self._create_character("Bob", credits=500.0)

        # 1. Minimum amount violation (< 100c)
        s1, m1, _ = await self.db.post_bounty("Alice", "Bob", "testnet", 50.0, "Trivial slight")
        self.assertFalse(s1)
        self.assertIn("Minimum bounty reward is 100c", m1)

        # 2. Insufficient credits
        s2, m2, _ = await self.db.post_bounty("Alice", "Bob", "testnet", 200.0, "Greed")
        self.assertFalse(s2)
        self.assertIn("Insufficient credits", m2)

        # 3. Self-bounty prevention
        s3, m3, _ = await self.db.post_bounty("Bob", "Bob", "testnet", 200.0, "Suicidal thought")
        self.assertFalse(s3)
        self.assertIn("Cannot post a bounty on yourself", m3)

        # Verify no bounties created
        active = await self.db.get_active_bounties("testnet")
        self.assertEqual(len(active), 0)

    async def test_ensure_mcp_bounty_triggers_and_scaling(self):
        """MCP automated bounty creates or scales reward based on is_bounty_eligible."""
        # 1. Ineligible character
        await self._create_character("CleanPlayer", credits=500.0, heat=1.0, rep={"CIV": 10.0})
        mcp_b1 = await self.db.ensure_mcp_bounty("CleanPlayer", "testnet")
        self.assertIsNone(mcp_b1)

        # 2. Eligible via MCP Heat > 6.0 (Heat = 8.0)
        # Expected reward: 500c base + (8.0 - 6.0) * 250c = 1000.0c
        await self._create_character("HotHacker", credits=500.0, heat=8.0)
        mcp_b2 = await self.db.ensure_mcp_bounty("HotHacker", "testnet")
        self.assertIsNotNone(mcp_b2)
        self.assertEqual(mcp_b2.issuer_nick, "MCP")
        self.assertEqual(mcp_b2.reward_credits, 1000.0)
        self.assertEqual(mcp_b2.status, "ACTIVE")

        # 3. Eligible via 2+ Hostile regions (MIL=-80, LEA=-90, Heat=2.0)
        # Expected reward: 500c base + 2 * 250c = 1000.0c
        await self._create_character("HostileRebel", credits=500.0, heat=2.0, rep={"MIL": -80.0, "LEA": -90.0})
        mcp_b3 = await self.db.ensure_mcp_bounty("HostileRebel", "testnet")
        self.assertIsNotNone(mcp_b3)
        self.assertEqual(mcp_b3.issuer_nick, "MCP")
        self.assertEqual(mcp_b3.reward_credits, 1000.0)

        # 4. Idempotency & update: When heat increases on HotHacker (e.g. to 9.0), reward updates
        async with self.async_session() as session:
            stmt = select(Character).where(Character.name == "HotHacker")
            c = (await session.execute(stmt)).scalars().first()
            c.mcp_heat = 9.0
            await session.commit()

        # Expected reward: 500c base + (9.0 - 6.0) * 250c = 1250.0c
        mcp_b2_updated = await self.db.ensure_mcp_bounty("HotHacker", "testnet")
        self.assertEqual(mcp_b2_updated.reward_credits, 1250.0)

        # Verify no duplicate active bounties for HotHacker
        active = await self.db.get_active_bounties("testnet")
        hot_bounties = [b for b in active if b.target_nick == "HotHacker"]
        self.assertEqual(len(hot_bounties), 1)

    async def test_claim_bounty_credit_transfer_and_status(self):
        """Claiming a bounty transfers credits to killer and updates status to CLAIMED."""
        hunter = await self._create_character("HunterX", credits=100.0)
        outlaw = await self._create_character("OutlawY", credits=500.0, heat=7.0)

        # Ensure MCP bounty on OutlawY (500 + 1.0 * 250 = 750c)
        bounty = await self.db.ensure_mcp_bounty("OutlawY", "testnet")
        self.assertEqual(bounty.reward_credits, 750.0)

        # HunterX claims bounty on OutlawY
        success, msg, claimed = await self.db.claim_bounty("OutlawY", "HunterX", "testnet")
        self.assertTrue(success)
        self.assertIn("claimed by HunterX", msg)
        self.assertEqual(claimed.status, "CLAIMED")
        self.assertEqual(claimed.claimed_by, "HunterX")
        self.assertIsNotNone(claimed.claimed_at)

        # Verify hunter's credits increased by exactly 750c (100 -> 850)
        async with self.async_session() as session:
            stmt = select(Character).where(Character.id == hunter.id)
            c = (await session.execute(stmt)).scalars().first()
            self.assertEqual(c.credits, 850.0)

        # Active bounties list is now empty
        active = await self.db.get_active_bounties("testnet")
        self.assertEqual(len(active), 0)

        # Second claim attempt fails
        s2, m2, _ = await self.db.claim_bounty("OutlawY", "HunterX", "testnet")
        self.assertFalse(s2)
        self.assertIn("No active bounty", m2)

    async def test_self_claim_rejected(self):
        """Self-claims (suicide) are strictly rejected without credit payout or status modification."""
        outlaw = await self._create_character("OutlawZ", credits=500.0, heat=7.0)
        bounty = await self.db.ensure_mcp_bounty("OutlawZ", "testnet")
        self.assertIsNotNone(bounty)

        success, msg, _ = await self.db.claim_bounty("OutlawZ", "OutlawZ", "testnet")
        self.assertFalse(success)
        self.assertIn("Self-claim rejected", msg)

        # Bounty remains ACTIVE
        b = await self.db.get_bounty_for_target("OutlawZ", "testnet")
        self.assertEqual(b.status, "ACTIVE")

    # =========================================================================
    # 2. IRC COMMAND HANDLERS & MULTI-MODE FORMATTING
    # =========================================================================

    async def test_bounty_list_command_human_text_narrative(self):
        """!a bounty / !a bounty list formats across human, text, and narrative modes."""
        await self._create_character("IssuerAlice", credits=2000.0)
        await self._create_character("WantedBob", credits=500.0)
        await self.db.post_bounty("IssuerAlice", "WantedBob", "testnet", 500.0, "Zero-day theft")

        # 1. Text / Machine Mode
        await self._create_character("BotViewer", output_mode="text")
        await handle_bounty(self.node, "BotViewer", ["list"], "#arena")
        sent = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("[GRID][ACTION:BOUNTY][RESULT:LIST]" in m and "WantedBob:500.0c:IssuerAlice" in m for m in sent))

        # 2. Narrative Mode
        self.node.send.reset_mock()
        await self._create_character("StoryViewer", output_mode="narrative")
        await handle_bounty(self.node, "StoryViewer", ["list"], "#arena")
        sent = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("[GRID][NARRATIVE]" in m and "WantedBob" in m and "500.0c" in m for m in sent))

        # 3. Human Mode
        self.node.send.reset_mock()
        await self._create_character("HumanViewer", output_mode="human")
        await handle_bounty(self.node, "HumanViewer", ["list"], "#arena")
        sent = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("BOUNTY BOARD" in m for m in sent))
        self.assertTrue(any("TARGET: WantedBob" in m and "500.0c" in m for m in sent))

    async def test_bounty_check_command_states(self):
        """!a bounty check <nick> handles active, eligible, and clean target statuses."""
        # 1. Target with active bounty
        await self._create_character("P1", credits=1000.0)
        await self._create_character("P2", credits=500.0)
        await self.db.post_bounty("P1", "P2", "testnet", 300.0, "Espionage")

        await handle_bounty(self.node, "P1", ["check", "P2"], "#arena")
        sent = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("BOUNTY: P2" in m for m in sent))
        self.assertTrue(any("300.0c" in m and "Espionage" in m for m in sent))

        # 2. Target without active bounty, but eligible for MCP bounty
        self.node.send.reset_mock()
        await self._create_character("P3", credits=500.0, heat=7.5)
        await handle_bounty(self.node, "P1", ["check", "P3"], "#arena")
        sent = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("ELIGIBLE for MCP bounty" in m and "Heat: 7.5" in m for m in sent))

        # 3. Target clean (no bounty, not eligible)
        self.node.send.reset_mock()
        await self._create_character("P4", credits=500.0, heat=0.0)
        await handle_bounty(self.node, "P1", ["check", "P4"], "#arena")
        sent = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("not currently bounty eligible" in m or "CLEAN" in m for m in sent))

    async def test_bounty_post_command_and_channel_broadcast(self):
        """!a bounty post <nick> <credits> [reason] validates input and broadcasts to channel."""
        await self._create_character("DonVito", credits=1000.0)
        await self._create_character("RivalMob", credits=500.0)

        await handle_bounty(self.node, "DonVito", ["post", "RivalMob", "400", "Territory breach"], "#arena")

        sent = [call[0][0] for call in self.node.send.call_args_list]
        # Confirmation to caller
        self.assertTrue(any("Bounty of 400.0c posted on RivalMob" in m for m in sent))
        # Broadcast announcement to #arena
        self.assertTrue(any("[BOUNTY POSTED] DonVito placed a 400.0c bounty on RivalMob!" in m for m in sent))

    async def test_command_router_dispatch_bounty(self):
        """CommandRouter correctly routes '!a bounty', '!a bounty list', and '!a bounty check'."""
        await self._create_character("TesterA", credits=1000.0)
        await self._create_character("WantedB", credits=500.0)
        await self.db.post_bounty("TesterA", "WantedB", "testnet", 200.0, "Testing routing")

        # Route !a bounty
        await self.router.dispatch("TesterA", "PRIVMSG", "#arena", "!a bounty", is_admin=False)
        await self._wait_until(lambda: any("BOUNTY BOARD" in m or "WantedB" in m for m in [c[0][0] for c in self.node.send.call_args_list]))
        sent = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("BOUNTY BOARD" in m or "WantedB" in m for m in sent))

        # Route !a bounty check WantedB
        self.node.send.reset_mock()
        await self.router.dispatch("TesterA", "PRIVMSG", "#arena", "!a bounty check WantedB", is_admin=False)
        await self._wait_until(lambda: any("BOUNTY: WantedB" in m for m in [c[0][0] for c in self.node.send.call_args_list]))
        sent = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("BOUNTY: WantedB" in m for m in sent))

    # =========================================================================
    # 3. COMBAT RESOLUTION & AUTOMATED BOUNTY CLAIM
    # =========================================================================

    async def test_fatal_pvp_resolution_claims_bounty_and_broadcasts(self):
        """Defeating a bountied player in PvP combat automatically claims the bounty and broadcasts."""
        killer_char = await self._create_character("HeroHunter", credits=200.0)
        fugitive_char = await self._create_character("FugitiveZero", credits=500.0)

        # Place 650c bounty on FugitiveZero
        await self._create_character("Contractor", credits=1000.0)
        await self.db.post_bounty("Contractor", "FugitiveZero", "testnet", 650.0, "Critical infiltration")

        # Execute combat bounty claim handler directly
        claimed = await handle_combat_bounty_claim(self.node, "FugitiveZero", "HeroHunter")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.status, "CLAIMED")
        self.assertEqual(claimed.claimed_by, "HeroHunter")
        self.assertEqual(claimed.reward_credits, 650.0)

        # Verify credits transferred: 200 -> 850
        async with self.async_session() as session:
            stmt = select(Character).where(Character.id == killer_char.id)
            c = (await session.execute(stmt)).scalars().first()
            self.assertEqual(c.credits, 850.0)

        # Verify broadcast announcement format
        sent = [call[0][0] for call in self.node.send.call_args_list]
        expected_announcement = "[BOUNTY CLAIMED] HeroHunter neutralized wanted fugitive FugitiveZero and collected 650.0c bounty! Issued by: Contractor."
        self.assertTrue(any(expected_announcement in m for m in sent))

    async def test_combat_engine_fatal_takedown_bounty_integration(self):
        """CombatEngine resolve_turn automatically triggers bounty claim when target.hp <= 0."""
        await self._create_character("TyrellCorp", credits=1000.0)
        killer_char = await self._create_character("BladeRunner", credits=50.0)
        fugitive_char = await self._create_character("ReplicantX", credits=300.0)

        # 450c bounty issued by TyrellCorp
        await self.db.post_bounty("TyrellCorp", "ReplicantX", "testnet", 450.0, "Retirement order")

        # Set up CombatEngine with self.node wired
        send_mock = AsyncMock()
        engine = CombatEngine("match_bounty_pvp", "!a", send_mock, node=self.node)
        engine.sleep_interval = 0

        # Killer has massive CPU to deliver fatal blow
        killer_ent = Entity("BladeRunner", cpu=50, ram=10, bnd=10, sec=5, alg=10, hp=200, max_hp=200, up=100)
        fugitive_ent = Entity("ReplicantX", cpu=2, ram=2, bnd=2, sec=1, alg=1, hp=15, max_hp=15, up=10)

        engine.add_entity(killer_ent)
        engine.add_entity(fugitive_ent)
        engine.active = True

        engine.queue_command("BladeRunner", "attack ReplicantX")
        engine.queue_command("ReplicantX", "defend")

        is_active = await engine.resolve_turn()

        # Target was defeated
        self.assertFalse(fugitive_ent.is_alive)
        self.assertFalse(is_active)

        # Verify bounty was claimed
        b = await self.db.get_bounty_for_target("ReplicantX", "testnet")
        self.assertIsNone(b)  # No active bounty anymore

        async with self.async_session() as session:
            stmt = select(Bounty).where(Bounty.target_nick == "ReplicantX")
            persisted_bounty = (await session.execute(stmt)).scalars().first()
            self.assertEqual(persisted_bounty.status, "CLAIMED")
            self.assertEqual(persisted_bounty.claimed_by, "BladeRunner")

            # Killer received 450c (BladeRunner balance: 50 -> 500)
            # Note: BladeRunner posted 450c originally (500 - 450 = 50), then won back 450c -> 500
            stmt_char = select(Character).where(Character.id == killer_char.id)
            c = (await session.execute(stmt_char)).scalars().first()
            self.assertEqual(c.credits, 500.0)

        # Verify broadcast was made via node.send
        sent = [call[0][0] for call in self.node.send.call_args_list]
        expected_announcement = "[BOUNTY CLAIMED] BladeRunner neutralized wanted fugitive ReplicantX and collected 450.0c bounty!"
        self.assertTrue(any(expected_announcement in m for m in sent))

    # =========================================================================
    # 4. ADVERSARIAL EDGE CASES & ROBUSTNESS TESTS
    # =========================================================================

    async def test_post_bounty_nan_and_inf_validation(self):
        """NaN and Inf rewards are rejected and do not deduct credits, corrupt balance, or crash SQL."""
        alice = await self._create_character("Alice", credits=500.0)
        bob = await self._create_character("Bob", credits=500.0)

        # 1. NaN in repository method
        s_nan, m_nan, _ = await self.db.post_bounty("Alice", "Bob", "testnet", float("nan"), "Exploit")
        self.assertFalse(s_nan)
        self.assertIn("valid finite number", m_nan)

        # 2. Inf in repository method
        s_inf, m_inf, _ = await self.db.post_bounty("Alice", "Bob", "testnet", float("inf"), "Exploit")
        self.assertFalse(s_inf)
        self.assertIn("valid finite number", m_inf)

        # Verify Alice's balance remained exactly 500.0 (not NaN)
        async with self.async_session() as session:
            stmt = select(Character).where(Character.id == alice.id)
            c = (await session.execute(stmt)).scalars().first()
            self.assertEqual(c.credits, 500.0)

        # 3. NaN in IRC command handler
        await handle_bounty(self.node, "Alice", ["post", "Bob", "nan", "NaN check"], "#arena")
        sent = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("must be a valid number" in m for m in sent))

        # 4. Inf in IRC command handler
        self.node.send.reset_mock()
        await handle_bounty(self.node, "Alice", ["post", "Bob", "inf", "Inf check"], "#arena")
        sent = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("must be a valid number" in m for m in sent))

    async def test_concurrent_bounty_posting_and_unique_constraint(self):
        """Concurrent post_bounty calls on the same target stack cleanly with zero duplicate active rows."""
        await self._create_character("Poster1", credits=1000.0)
        await self._create_character("Poster2", credits=1000.0)
        await self._create_character("ConcurrentTarget", credits=500.0)

        # Run concurrent postings
        r1, r2 = await asyncio.gather(
            self.db.post_bounty("Poster1", "ConcurrentTarget", "testnet", 300.0, "Contract A"),
            self.db.post_bounty("Poster2", "ConcurrentTarget", "testnet", 400.0, "Contract B"),
        )
        self.assertTrue(r1[0])
        self.assertTrue(r2[0])

        active = await self.db.get_active_bounties("testnet")
        # Exactly one active bounty exists with stacked reward 700.0c
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].reward_credits, 700.0)
        self.assertIn("Contract A", active[0].reason)
        self.assertIn("Contract B", active[0].reason)

    async def test_mcp_bounty_scaling_preserves_player_contributions(self):
        """MCP bounty updates properly scale MCP heat increases while preserving stacked player rewards."""
        # Scenario 1: MCP first -> Player Alice stacks -> Bob heat increases
        await self._create_character("Alice", credits=2000.0)
        bob = await self._create_character("Bob", credits=500.0, heat=6.5)

        mcp_b = await self.db.ensure_mcp_bounty("Bob", "testnet")
        self.assertEqual(mcp_b.reward_credits, 625.0)  # 500 + 0.5 * 250

        ok, _, stacked_b = await self.db.post_bounty("Alice", "Bob", "testnet", 300.0, "Corporate sabotage")
        self.assertTrue(ok)
        self.assertEqual(stacked_b.reward_credits, 925.0)

        # Bob heat increases to 9.0 (MCP reward = 1250c)
        async with self.async_session() as session:
            stmt = select(Character).where(Character.id == bob.id)
            c = (await session.execute(stmt)).scalars().first()
            c.mcp_heat = 9.0
            await session.commit()

        b_updated = await self.db.ensure_mcp_bounty("Bob", "testnet")
        # Reward must be 1250 (MCP) + 300 (Alice) = 1550c
        self.assertEqual(b_updated.reward_credits, 1550.0)
        self.assertIn("Corporate sabotage", b_updated.reason)

        # Scenario 2: Player Charlie posts first -> MCP ensures -> Heat increases
        await self._create_character("Charlie", credits=2000.0)
        dave = await self._create_character("Dave", credits=500.0, heat=7.0)

        ok2, _, b_dave = await self.db.post_bounty("Charlie", "Dave", "testnet", 300.0, "Trade theft")
        self.assertTrue(ok2)
        self.assertEqual(b_dave.reward_credits, 300.0)

        mcp_d1 = await self.db.ensure_mcp_bounty("Dave", "testnet")
        self.assertEqual(mcp_d1.reward_credits, 1050.0)  # 300 + 750

        # Dave heat increases to 9.0 (MCP 1250c)
        async with self.async_session() as session:
            stmt = select(Character).where(Character.id == dave.id)
            c = (await session.execute(stmt)).scalars().first()
            c.mcp_heat = 9.0
            await session.commit()

        mcp_d2 = await self.db.ensure_mcp_bounty("Dave", "testnet")
        self.assertEqual(mcp_d2.reward_credits, 1550.0)  # 300 + 1250
        self.assertIn("Trade theft", mcp_d2.reason)

    async def test_multi_alias_alt_self_bounty_and_self_claim_prevention(self):
        """Self-bounty and self-claim preventions extend to alt aliases on the same player account."""
        # Create Player with two aliases
        async with self.async_session() as session:
            p = Player(global_name="MultiAliasPlayer")
            session.add(p)
            await session.flush()
            session.add(NetworkAlias(player_id=p.id, network_name="testnet", nickname="MainNick"))
            session.add(NetworkAlias(player_id=p.id, network_name="testnet", nickname="AltNick"))
            c = Character(player_id=p.id, name="MainNick", race="Cyborg", char_class="Hacker", credits=1000.0, current_hp=100)
            session.add(c)
            await session.commit()

        # 1. Post bounty from MainNick onto AltNick (rejected)
        ok_post, msg_post, _ = await self.db.post_bounty("MainNick", "AltNick", "testnet", 200.0, "Self exploit")
        self.assertFalse(ok_post)
        self.assertIn("Cannot post a bounty on yourself", msg_post)

        # 2. Post bounty on RivalTarget
        await self._create_character("RivalTarget", credits=500.0)
        await self.db.post_bounty("MainNick", "RivalTarget", "testnet", 300.0, "Rivalry")

        # 3. Claim bounty on MainNick using AltNick (rejected)
        # Create an MCP bounty on MainNick
        async with self.async_session() as session:
            stmt = select(Character).where(Character.name == "MainNick")
            char_main = (await session.execute(stmt)).scalars().first()
            char_main.mcp_heat = 7.0
            await session.commit()
        await self.db.ensure_mcp_bounty("MainNick", "testnet")

        ok_claim, msg_claim, _ = await self.db.claim_bounty("MainNick", "AltNick", "testnet")
        self.assertFalse(ok_claim)
        self.assertIn("Self-claim rejected", msg_claim)

    async def test_case_insensitive_network_and_multi_alias_resolution(self):
        """Bounties are queryable and claimable across case variations and player alias nicknames."""
        async with self.async_session() as session:
            p = Player(global_name="BobAliasPlayer")
            session.add(p)
            await session.flush()
            session.add(NetworkAlias(player_id=p.id, network_name="testnet", nickname="BobNick"))
            c = Character(player_id=p.id, name="BobChar", race="Cyborg", char_class="Hacker", credits=500.0, current_hp=100)
            session.add(c)
            await session.commit()

        await self._create_character("AliceHunter", credits=2000.0)

        # Alice posts bounty on Bob using alias BobNick and different network casing TESTNET
        ok, msg, b = await self.db.post_bounty("AliceHunter", "BobNick", "TESTNET", 350.0, "Alias test contract")
        self.assertTrue(ok)

        # Query using canonical character name BobChar on testnet
        b_query1 = await self.db.get_bounty_for_target("BobChar", "testnet")
        self.assertIsNotNone(b_query1)
        self.assertEqual(b_query1.reward_credits, 350.0)

        # Query using alias bobnick on TESTNET
        b_query2 = await self.db.get_bounty_for_target("bobnick", "TESTNET")
        self.assertIsNotNone(b_query2)
        self.assertEqual(b_query2.id, b_query1.id)

        # Claim using alias and different casing
        ok_claim, msg_claim, claimed = await self.db.claim_bounty("bobnick", "alicehunter", "testnet")
        self.assertTrue(ok_claim)
        self.assertEqual(claimed.status, "CLAIMED")

    async def test_concurrent_claim_bounty_race_prevention(self):
        """Concurrent claim_bounty calls on the same target allow strictly one winner with zero double-payout."""
        await self._create_character("FugitiveRacer", credits=200.0, heat=8.0)
        killer1 = await self._create_character("HunterAlpha", credits=100.0)
        killer2 = await self._create_character("HunterBeta", credits=100.0)

        # MCP creates 1000c bounty on FugitiveRacer
        b = await self.db.ensure_mcp_bounty("FugitiveRacer", "testnet")
        self.assertEqual(b.reward_credits, 1000.0)

        # Both hunters claim simultaneously
        res1, res2 = await asyncio.gather(
            self.db.claim_bounty("FugitiveRacer", "HunterAlpha", "testnet"),
            self.db.claim_bounty("FugitiveRacer", "HunterBeta", "testnet")
        )

        # Exactly one claim must succeed
        results = [res1[0], res2[0]]
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 1)

        # Verify only the winning hunter received credits
        async with self.async_session() as session:
            stmt1 = select(Character).where(Character.id == killer1.id)
            c1 = (await session.execute(stmt1)).scalars().first()
            stmt2 = select(Character).where(Character.id == killer2.id)
            c2 = (await session.execute(stmt2)).scalars().first()

            if res1[0]:
                self.assertEqual(c1.credits, 1100.0)
                self.assertEqual(c2.credits, 100.0)
            else:
                self.assertEqual(c1.credits, 100.0)
                self.assertEqual(c2.credits, 1100.0)

        # Bounty is claimed and no active bounty remains
        active = await self.db.get_active_bounties("testnet")
        self.assertEqual(len(active), 0)

    async def test_grid_attack_prevents_alt_account_self_claim(self):
        """grid_attack fatal strike prevents claiming bounty on character belonging to same player account."""
        async with self.async_session() as session:
            node = GridNode(id=999, name="TestCombatZone", node_type="void")
            session.add(node)
            await session.flush()

            p = Player(id=555, global_name="SelfFarmer")
            session.add(p)
            await session.flush()

            session.add(NetworkAlias(player_id=555, network_name="testnet", nickname="MainFighter"))
            session.add(NetworkAlias(player_id=555, network_name="testnet", nickname="AltSacrifice"))

            c_main = Character(
                id=501, player_id=555, node_id=999, name="MainFighter",
                race="Cyborg", char_class="Hacker", credits=100.0,
                cpu=50, ram=10, bnd=5, sec=5, alg=10, power=100.0, current_hp=100
            )
            c_alt = Character(
                id=502, player_id=555, node_id=999, name="AltSacrifice",
                race="Cyborg", char_class="Hacker", credits=100.0,
                cpu=1, ram=1, bnd=1, sec=1, alg=1, power=100.0, current_hp=5
            )
            session.add_all([c_main, c_alt])

            b = Bounty(
                target_nick="AltSacrifice", issuer_nick="MCP", network_name="testnet",
                reward_credits=500.0, reason="Wanted fugitive",
                created_at=datetime.datetime.now(datetime.timezone.utc), status="ACTIVE"
            )
            session.add(b)
            await session.commit()

        # MainFighter strikes AltSacrifice for fatal damage
        ok, msg, _ = await self.db.combat.grid_attack("MainFighter", "AltSacrifice", "testnet")
        self.assertTrue(ok)
        self.assertNotIn("Bounty Claimed", msg)

        # Bounty remains ACTIVE on AltSacrifice
        b_check = await self.db.get_bounty_for_target("AltSacrifice", "testnet")
        self.assertIsNotNone(b_check)
        self.assertEqual(b_check.status, "ACTIVE")

        # MainFighter did not receive the 500c bounty payout
        async with self.async_session() as session:
            stmt = select(Character).where(Character.id == 501)
            c = (await session.execute(stmt)).scalars().first()
            # Stole 10c looted from AltSacrifice, but NO 500c bounty
            self.assertEqual(c.credits, 110.0)

    async def test_repository_none_and_invalid_inputs_handled_safely(self):
        """Repository methods handle None, non-string, and empty inputs gracefully without exceptions."""
        self.assertIsNone(await self.db.get_bounty_for_target(None, "testnet"))
        self.assertIsNone(await self.db.get_bounty_for_target("Bob", None))
        self.assertEqual(await self.db.get_active_bounties(None), [])
        self.assertIsNone(await self.db.ensure_mcp_bounty(None, "testnet"))
        self.assertIsNone(await self.db.ensure_mcp_bounty("Bob", None))

        ok_post, msg_post, _ = await self.db.post_bounty(None, "Bob", "testnet", 100.0)
        self.assertFalse(ok_post)
        self.assertIn("Invalid issuer, target, or network", msg_post)

        ok_claim, msg_claim, _ = await self.db.claim_bounty(None, "Killer", "testnet")
        self.assertFalse(ok_claim)
        self.assertIn("Invalid target, killer, or network", msg_claim)

    async def test_mcp_tag_regex_special_characters_preserved(self):
        """Reason strings containing backslashes and regex patterns scale safely without corruption."""
        bob = await self._create_character("SpecialCharBob", credits=500.0, heat=7.0)
        await self._create_character("PostUser", credits=2000.0)

        # Post bounty with tricky strings containing backslash sequences
        tricky_reason = r"Exploit vector \g<0> and \1 shell"
        ok, _, b = await self.db.post_bounty("PostUser", "SpecialCharBob", "testnet", 300.0, tricky_reason)
        self.assertTrue(ok)

        # MCP ensures bounty and scales
        b_mcp1 = await self.db.ensure_mcp_bounty("SpecialCharBob", "testnet")
        self.assertIsNotNone(b_mcp1)
        self.assertIn(r"\g<0>", b_mcp1.reason)

        # Increase heat further and scale again
        async with self.async_session() as session:
            stmt = select(Character).where(Character.id == bob.id)
            c = (await session.execute(stmt)).scalars().first()
            c.mcp_heat = 9.0
            await session.commit()

        b_mcp2 = await self.db.ensure_mcp_bounty("SpecialCharBob", "testnet")
        self.assertIsNotNone(b_mcp2)
        self.assertEqual(b_mcp2.reward_credits, 1550.0)
        self.assertIn(r"\g<0>", b_mcp2.reason)
        self.assertIn(r"\1 shell", b_mcp2.reason)

    async def test_combat_engine_npc_takedown_does_not_claim_bounty(self):
        """Mob takedowns in CombatEngine do not claim bounties (PvP takedowns only)."""
        await self._create_character("CorpIssuer", credits=1000.0)
        fugitive = await self._create_character("BountiedHacker", credits=200.0)
        await self.db.post_bounty("CorpIssuer", "BountiedHacker", "testnet", 400.0, "Wanted by corp")

        # Mob entity kills BountiedHacker
        mob_ent = Entity("ICE_Drone", cpu=50, ram=20, bnd=10, sec=10, alg=10, hp=200, max_hp=200, up=100)
        mob_ent.is_npc = True
        player_ent = Entity("BountiedHacker", cpu=1, ram=1, bnd=1, sec=1, alg=1, hp=5, max_hp=5, up=10)
        player_ent.is_npc = False

        send_mock = AsyncMock()
        engine = CombatEngine("match_pve_bounty", "!a", send_mock, node=self.node)
        engine.sleep_interval = 0
        engine.add_entity(mob_ent)
        engine.add_entity(player_ent)
        engine.active = True

        engine.queue_command("ICE_Drone", "attack BountiedHacker")
        engine.queue_command("BountiedHacker", "defend")

        is_active = await engine.resolve_turn()
        self.assertFalse(player_ent.is_alive)

        # Bounty remains ACTIVE
        b = await self.db.get_bounty_for_target("BountiedHacker", "testnet")
        self.assertIsNotNone(b)
        self.assertEqual(b.status, "ACTIVE")
        self.assertEqual(b.reward_credits, 400.0)


