# ai_grid/qa/tests/test_combat_rules.py
# Comprehensive Async Unit Tests for Forced Combat Rules, Optional PvP Flow,
# Session Authentication (login <name> <key>), Arena Queue/Ready Gating,
# and Claimed Node Stability Decay Exemption.

import asyncio
import datetime
import os
import time
import unittest
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from ai_grid.models import Base, Player, NetworkAlias, Character, GridNode
from ai_grid.grid_db import ArenaDB
from ai_grid.core.command_router import CommandRouter
from ai_grid.core.handlers.combat import (
    handle_pvp_command, handle_ready, resolve_pvp_challenge, handle_mob_encounter, resolve_mob
)
from ai_grid.core.handlers.personal import handle_login
from ai_grid.database.repositories.maintenance_repo import MaintenanceRepository


class TestCombatRules(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db_path = f"test_combat_rules_{int(datetime.datetime.now().timestamp() * 1000)}.db"
        self.db = ArenaDB(self.db_path)
        self.async_session = self.db.async_session

        async with self.db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Mock Network Node
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
        self.node.match_queue = []
        self.node.ready_players = []
        self.node.pending_fighters = []
        self.node.pending_encounters = {}
        self.node.pending_challenges = {}
        self.node.authenticated_users = set()
        self.node.authenticated_sessions = {}
        self.node.nickserv_verified = set()
        self.node.last_player_locations = {}
        self.node.admins = ["admin_user"]
        self.node.check_match_start = AsyncMock()
        self.node.action_timestamps = {}
        self.node.flood_config = {
            'max_tokens': 1000.0,
            'refill_rate': 100.0,
            'violation_threshold': 500,
            'lockout_duration': 30,
            'messages': {}
        }

        self.node.is_user_verified = lambda nick: nick.lower() in self.node.nickserv_verified
        self.node.is_authenticated = lambda nick: (
            self.node.is_user_verified(nick)
            or nick.lower() in getattr(self.node, 'authenticated_users', set())
        )
        self.node.check_admin_privilege = lambda nick: (True, "AUTHORIZED") if nick.lower() in self.node.admins else (False, "NOT_ADMIN")

        self.router = CommandRouter(self.node)
        self.node.router = self.router
        orig_dispatch = self.router.dispatch
        async def dispatch_and_wait(*args, **kwargs):
            res = await orig_dispatch(*args, **kwargs)
            await asyncio.sleep(0.05)
            return res
        self.router.dispatch = dispatch_and_wait

    async def _wait_until(self, pred, timeout=2.0, interval=0.02):
        start = time.time()
        while time.time() - start < timeout:
            if pred():
                return True
            await asyncio.sleep(interval)
        return pred()

    async def asyncTearDown(self):
        # Cancel any pending challenge or encounter timers
        for chal in list(getattr(self.node, 'pending_challenges', {}).values()):
            timer = chal.get('timer')
            if timer and not timer.done():
                timer.cancel()
        for enc in list(getattr(self.node, 'pending_encounters', {}).values()):
            timer = enc.get('timer')
            if timer and not timer.done():
                timer.cancel()

        await self.db.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    async def _create_test_character(self, name: str, network: str = "testnet", auth_token: str = "token_abc",
                                     node_id: int = None, is_nickserv_verified: bool = False) -> Character:
        async with self.async_session() as session:
            player = Player(global_name=f"{name}_{network}", is_autonomous=False)
            session.add(player)
            await session.flush()

            alias = NetworkAlias(player_id=player.id, network_name=network, nickname=name)
            session.add(alias)

            char = Character(
                player_id=player.id,
                name=name,
                race="Synth",
                char_class="Infiltrator",
                node_id=node_id,
                cpu=10,
                ram=10,
                bnd=10,
                sec=10,
                alg=10,
                current_hp=100,
                power=200.0,
                stability=100.0,
                credits=500.0,
                auth_token=auth_token
            )
            session.add(char)
            await session.commit()
            await session.refresh(char)

        if is_nickserv_verified:
            self.node.nickserv_verified.add(name.lower())

        return char

    async def _create_test_node(self, name: str, node_type: str = "void", region_type: str = "VOD",
                                owner_char_id: int = None) -> GridNode:
        async with self.async_session() as session:
            node = GridNode(
                name=name,
                node_type=node_type,
                region_type=region_type,
                owner_character_id=owner_char_id,
                power_stored=500.0,
                durability=100.0
            )
            session.add(node)
            await session.commit()
            await session.refresh(node)
            return node

    # =========================================================================
    # R3 Tests: login <name> <key> & Arena Queue / Ready Gating
    # =========================================================================

    async def test_login_authentication_success_and_binding(self):
        """R3: login <name> <key> authenticates unregistered nicks and binds session."""
        char = await self._create_test_character("AlphaBot", auth_token="secret_key_999")

        # Caller nick is "GuestPlayer" (not NickServ verified)
        self.assertFalse(self.node.is_authenticated("GuestPlayer"))

        # Test prefixed command: !a login AlphaBot secret_key_999
        await self.router.dispatch("GuestPlayer", "PRIVMSG", "#arena", "!a login AlphaBot secret_key_999", is_admin=False)
        await self._wait_until(lambda: "guestplayer" in self.node.authenticated_users)
        self.assertTrue(self.node.is_authenticated("GuestPlayer"))
        self.assertIn("guestplayer", self.node.authenticated_users)
        self.assertEqual(self.node.authenticated_sessions.get("guestplayer"), "AlphaBot")

        # Verify DB binding: NetworkAlias for GuestPlayer on testnet now points to AlphaBot's player_id
        async with self.async_session() as session:
            stmt = select(NetworkAlias).where(NetworkAlias.nickname == "GuestPlayer", NetworkAlias.network_name == "testnet")
            alias = (await session.execute(stmt)).scalars().first()
            self.assertIsNotNone(alias)
            self.assertEqual(alias.player_id, char.player_id)

        # Place AlphaBot at Arena node and verify GuestPlayer can queue without rejection
        arn_node = await self._create_test_node("TheArena", node_type="arena", region_type="ARN")
        await self.db.move_player_to_node("GuestPlayer", "testnet", "TheArena")
        await self.router.dispatch("GuestPlayer", "PRIVMSG", "#arena", "!a queue", is_admin=False)
        self.assertIn("GuestPlayer", self.node.match_queue)

    async def test_login_authentication_non_prefixed(self):
        """R3: login <name> <key> works without command prefix."""
        await self._create_test_character("NonPrefixedUser", auth_token="key_pass_123")
        self.assertFalse(self.node.is_authenticated("GuestAnon"))

        await self.router.dispatch("GuestAnon", "PRIVMSG", "#arena", "login NonPrefixedUser key_pass_123", is_admin=False)
        await self._wait_until(lambda: self.node.is_authenticated("GuestAnon"))
        self.assertTrue(self.node.is_authenticated("GuestAnon"))

    async def test_login_authentication_failures(self):
        """R3: login rejects invalid keys or nonexistent characters."""
        await self._create_test_character("SecureChar", auth_token="correct_key")

        # 1. Invalid key
        await self.router.dispatch("BadUser", "PRIVMSG", "#arena", "!a login SecureChar wrong_key", is_admin=False)
        await self._wait_until(lambda: any("Invalid authentication key" in m for m in [call[0][0] for call in self.node.send.call_args_list]))
        self.assertFalse(self.node.is_authenticated("BadUser"))
        sent_msgs = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("Invalid authentication key" in m for m in sent_msgs))

        # 2. Nonexistent character
        self.node.send.reset_mock()
        await self.router.dispatch("BadUser2", "PRIVMSG", "#arena", "!a login NonExistentChar some_key", is_admin=False)
        await self._wait_until(lambda: any("not found" in m for m in [call[0][0] for call in self.node.send.call_args_list]))
        self.assertFalse(self.node.is_authenticated("BadUser2"))
        sent_msgs2 = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("not found" in m for m in sent_msgs2))

    async def test_queue_gating_unregistered_user(self):
        """R3: queue command rejects users without a character account."""
        await self.router.dispatch("Stranger", "PRIVMSG", "#arena", "!a queue", is_admin=False)
        self.assertNotIn("Stranger", self.node.match_queue)

        sent_msgs = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("[ERR] Unregistered user. Please create an account first: !a register" in m for m in sent_msgs))

    async def test_queue_gating_unauthenticated_user(self):
        """R3: queue command rejects registered users who are not logged in/authenticated."""
        node = await self._create_test_node("TestArena", node_type="arena", region_type="ARN")
        await self._create_test_character("UnauthUser", node_id=node.id, is_nickserv_verified=False)

        # UnauthUser has an account, but has neither NickServ nor active login session
        self.assertFalse(self.node.is_authenticated("UnauthUser"))

        await self.router.dispatch("UnauthUser", "PRIVMSG", "#arena", "!a queue", is_admin=False)
        self.assertNotIn("UnauthUser", self.node.match_queue)

        sent_msgs = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("[ERR] Authentication required. Please log in first: !a login <name> <key>" in m for m in sent_msgs))

    async def test_queue_gating_ineligible_location(self):
        """R3: queue command rejects authenticated users at non-eligible grid nodes."""
        void_node = await self._create_test_node("VoidNode", node_type="void", region_type="VOD")
        await self._create_test_character("VoidWalker", node_id=void_node.id, is_nickserv_verified=True)

        self.assertTrue(self.node.is_authenticated("VoidWalker"))

        await self.router.dispatch("VoidWalker", "PRIVMSG", "#arena", "!a queue", is_admin=False)
        self.assertNotIn("VoidWalker", self.node.match_queue)

        sent_msgs = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("[ERR] Queue only available at Arena [ARN], claimed nodes, or friendly nodes." in m for m in sent_msgs))

    async def test_queue_gating_eligible_locations_and_ready_without_token(self):
        """R3: queue succeeds at Arena [ARN], claimed, and friendly nodes; ready succeeds without token."""
        # 1. Arena node [ARN]
        arn_node = await self._create_test_node("ArenaNode", node_type="arena", region_type="ARN")
        await self._create_test_character("Gladiator", node_id=arn_node.id, is_nickserv_verified=True)

        await self.router.dispatch("Gladiator", "PRIVMSG", "#arena", "!a queue", is_admin=False)
        self.assertIn("Gladiator", self.node.match_queue)

        # 2. Player-claimed node
        claim_node = await self._create_test_node("ClaimedBase", node_type="void", region_type="VOD")
        owner = await self._create_test_character("BaseOwner", node_id=claim_node.id, is_nickserv_verified=True)
        async with self.async_session() as session:
            stmt = select(GridNode).where(GridNode.id == claim_node.id)
            db_node = (await session.execute(stmt)).scalars().first()
            db_node.owner_character_id = owner.id
            await session.commit()

        await self.router.dispatch("BaseOwner", "PRIVMSG", "#arena", "!a queue", is_admin=False)
        self.assertIn("BaseOwner", self.node.match_queue)

        # 3. Friendly node (CIV / safezone)
        civ_node = await self._create_test_node("CivicCenter", node_type="safezone", region_type="CIV")
        await self._create_test_character("Citizen", node_id=civ_node.id, is_nickserv_verified=True)

        await self.router.dispatch("Citizen", "PRIVMSG", "#arena", "!a queue", is_admin=False)
        self.assertIn("Citizen", self.node.match_queue)

        # 4. Ready Preconditions:
        # 4a. Unqueued player fails !a ready
        await self._create_test_character("UnqueuedFighter", node_id=arn_node.id, is_nickserv_verified=True)
        await self.router.dispatch("UnqueuedFighter", "PRIVMSG", "#arena", "!a ready", is_admin=False)
        self.assertNotIn("UnqueuedFighter", self.node.ready_players)
        sent_msgs = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("[ERR] You must be in the queue to ready up. Use !a queue first." in m for m in sent_msgs))

        # 4b. Queued and authenticated player sends !a ready without demanding token
        await self.router.dispatch("Gladiator", "PRIVMSG", "#arena", "!a ready", is_admin=False)
        await self._wait_until(lambda: self.node.check_match_start.called)
        self.assertIn("Gladiator", self.node.ready_players)
        self.node.check_match_start.assert_called()

    # =========================================================================
    # R1 & R2 Tests: Forced Combat Rules & Optional Open-World PvP
    # =========================================================================

    async def test_forced_combat_claimed_territory_defense_owner_present(self):
        """R1 Condition 1: Attacking claimed territory with owner present forces combat immediately."""
        base_node = await self._create_test_node("Fortress_01", node_type="void", region_type="VOD")
        defender = await self._create_test_character("DefenderBob", node_id=base_node.id, is_nickserv_verified=True)
        attacker = await self._create_test_character("AttackerAlice", node_id=base_node.id, is_nickserv_verified=True)

        # Set DefenderBob as owner
        async with self.async_session() as session:
            n = (await session.execute(select(GridNode).where(GridNode.id == base_node.id))).scalars().first()
            n.owner_character_id = defender.id
            await session.commit()

        # Attacker attacks DefenderBob directly on Defender's claimed node
        await self.router.dispatch("AttackerAlice", "PRIVMSG", "#arena", "!a attack DefenderBob", is_admin=False)

        # Must force combat immediately: active_engine initialized, no engagement challenge prompt
        await self._wait_until(lambda: self.node.active_engine is not None)
        self.assertIsNotNone(self.node.active_engine)
        self.assertTrue(self.node.active_engine.active)
        self.assertIn("AttackerAlice", self.node.active_engine.entities)
        self.assertIn("DefenderBob", self.node.active_engine.entities)
        self.assertNotIn("defenderbob", self.node.pending_challenges)

        sent_msgs = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("FORCED_COMBAT" in m and "TERRITORY_DEFENSE" in m for m in sent_msgs))

    async def test_forced_combat_claimed_territory_node_hack_owner_present(self):
        """R1 Condition 1: Hacking another player's claimed node with owner present forces combat."""
        base_node = await self._create_test_node("Fortress_02", node_type="void", region_type="VOD")
        defender = await self._create_test_character("DefenderGuy", node_id=base_node.id, is_nickserv_verified=True)
        attacker = await self._create_test_character("HackerGirl", node_id=base_node.id, is_nickserv_verified=True)

        async with self.async_session() as session:
            n = (await session.execute(select(GridNode).where(GridNode.id == base_node.id))).scalars().first()
            n.owner_character_id = defender.id
            await session.commit()

        # HackerGirl hacks the claimed node where DefenderGuy is present
        await self.router.dispatch("HackerGirl", "PRIVMSG", "#arena", "!a grid hack", is_admin=False)

        # Forced combat with DefenderGuy
        await self._wait_until(lambda: self.node.active_engine is not None)
        self.assertIsNotNone(self.node.active_engine)
        self.assertTrue(self.node.active_engine.active)
        self.assertIn("HackerGirl", self.node.active_engine.entities)
        self.assertIn("DefenderGuy", self.node.active_engine.entities)

        sent_msgs = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("FORCED_COMBAT" in m and "TERRITORY_DEFENSE" in m for m in sent_msgs))

    async def test_claimed_territory_defense_owner_absent_does_not_force_combat(self):
        """R1 Condition 1: Attacking/hacking claimed node when owner is absent does NOT force combat."""
        base_node = await self._create_test_node("Fortress_03", node_type="void", region_type="VOD")
        spawn_node = await self._create_test_node("SpawnNexus", node_type="safezone", region_type="CIV")

        # Defender is away at spawn_node
        defender = await self._create_test_character("AbsentOwner", node_id=spawn_node.id, is_nickserv_verified=True)
        attacker = await self._create_test_character("LoneRaider", node_id=base_node.id, is_nickserv_verified=True)

        async with self.async_session() as session:
            n = (await session.execute(select(GridNode).where(GridNode.id == base_node.id))).scalars().first()
            n.owner_character_id = defender.id
            await session.commit()

        # LoneRaider hacks Fortress_03 while owner is absent
        await self.router.dispatch("LoneRaider", "PRIVMSG", "#arena", "!a grid hack", is_admin=False)

        # Combat should NOT be forced against AbsentOwner
        self.assertIsNone(self.node.active_engine)
        sent_msgs = [call[0][0] for call in self.node.send.call_args_list]
        self.assertFalse(any("FORCED_COMBAT" in m for m in sent_msgs))

    async def test_forced_combat_warzone_nodes(self):
        """R1 Condition 2: Two players present in a Warzone [WAR] are forced into combat."""
        war_node = await self._create_test_node("Warzone_Alpha", node_type="void", region_type="WAR")
        await self._create_test_character("SoldierA", node_id=war_node.id, is_nickserv_verified=True)
        await self._create_test_character("SoldierB", node_id=war_node.id, is_nickserv_verified=True)

        # SoldierA attacks SoldierB in Warzone
        await self.router.dispatch("SoldierA", "PRIVMSG", "#arena", "!a attack SoldierB", is_admin=False)

        # Forced combat immediately
        await self._wait_until(lambda: self.node.active_engine is not None)
        self.assertIsNotNone(self.node.active_engine)
        self.assertTrue(self.node.active_engine.active)
        self.assertIn("SoldierA", self.node.active_engine.entities)
        self.assertIn("SoldierB", self.node.active_engine.entities)
        self.assertNotIn("soldierb", self.node.pending_challenges)

        sent_msgs = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("FORCED_COMBAT" in m and "WARZONE" in m for m in sent_msgs))

    async def test_safezone_strictly_prohibits_combat(self):
        """R1 Invariant: Safezones strictly prohibit combat."""
        safe_node = await self._create_test_node("NexusSafe", node_type="safezone", region_type="CIV")
        await self._create_test_character("PeacefulA", node_id=safe_node.id, is_nickserv_verified=True)
        await self._create_test_character("PeacefulB", node_id=safe_node.id, is_nickserv_verified=True)

        await self.router.dispatch("PeacefulA", "PRIVMSG", "#arena", "!a attack PeacefulB", is_admin=False)
        await self._wait_until(lambda: any("Combat is strictly prohibited in this zone" in m for m in [call[0][0] for call in self.node.send.call_args_list]))

        self.assertIsNone(self.node.active_engine)
        self.assertNotIn("peacefulb", self.node.pending_challenges)

        sent_msgs = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("Combat is strictly prohibited in this zone" in m for m in sent_msgs))

    async def test_optional_open_world_pvp_challenge_and_engage(self):
        """R2: Non-Warzone attack initiates 30s challenge; 'engage' starts combat."""
        neutral_node = await self._create_test_node("OpenSector", node_type="void", region_type="VOD")
        await self._create_test_character("ChallengerX", node_id=neutral_node.id, is_nickserv_verified=True)
        await self._create_test_character("DefenderY", node_id=neutral_node.id, is_nickserv_verified=True)

        # ChallengerX attacks DefenderY in neutral sector
        await self.router.dispatch("ChallengerX", "PRIVMSG", "#arena", "!a attack DefenderY", is_admin=False)

        # Combat has NOT started yet
        await self._wait_until(lambda: "defendery" in self.node.pending_challenges)
        self.assertIsNone(self.node.active_engine)
        self.assertIn("defendery", self.node.pending_challenges)

        sent_msgs = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("ENGAGEMENT_CHALLENGE" in m and "ChallengerX initiated combat against DefenderY" in m for m in sent_msgs))

        # DefenderY accepts via 'engage' (without prefix or with prefix)
        await self.router.dispatch("DefenderY", "PRIVMSG", "#arena", "engage", is_admin=False)

        # Combat is now formally started
        await self._wait_until(lambda: self.node.active_engine is not None)
        self.assertIsNotNone(self.node.active_engine)
        self.assertTrue(self.node.active_engine.active)
        self.assertNotIn("defendery", self.node.pending_challenges)

    async def test_optional_open_world_pvp_challenge_and_flee(self):
        """R2: Challenged player issues 'flee' to decline and retreat without loss."""
        start_node = await self._create_test_node("PreviousSector", node_type="void", region_type="VOD")
        neutral_node = await self._create_test_node("AmbushSector", node_type="void", region_type="VOD")
        await self._create_test_character("AttackerZ", node_id=neutral_node.id, is_nickserv_verified=True)
        def_char = await self._create_test_character("RunnerW", node_id=neutral_node.id, is_nickserv_verified=True)

        # Record runner's previous location
        self.node.last_player_locations["runnerw"] = "PreviousSector"

        await self.router.dispatch("AttackerZ", "PRIVMSG", "#arena", "!a attack RunnerW", is_admin=False)
        await self._wait_until(lambda: "runnerw" in self.node.pending_challenges)
        self.assertIn("runnerw", self.node.pending_challenges)

        # RunnerW flees: !a flee
        await self.router.dispatch("RunnerW", "PRIVMSG", "#arena", "!a flee", is_admin=False)
        await self._wait_until(lambda: any("fled back to PreviousSector" in m for m in [call[0][0] for call in self.node.send.call_args_list]))

        self.assertNotIn("runnerw", self.node.pending_challenges)
        self.assertIsNone(self.node.active_engine)

        sent_msgs = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("fled back to PreviousSector" in m for m in sent_msgs))

        # Verify RunnerW's node was updated back to PreviousSector
        async with self.async_session() as session:
            stmt = select(Character).options(selectinload(Character.current_node)).where(Character.id == def_char.id)
            c = (await session.execute(stmt)).scalars().first()
            self.assertEqual(c.current_node.name, "PreviousSector")

    async def test_optional_open_world_pvp_timeout_safe(self):
        """R2: Challenge times out safely without forcing combat or penalizing defender."""
        neutral_node = await self._create_test_node("TimeoutSector", node_type="void", region_type="VOD")
        await self._create_test_character("PatientAttacker", node_id=neutral_node.id, is_nickserv_verified=True)
        await self._create_test_character("IdleDefender", node_id=neutral_node.id, is_nickserv_verified=True)

        # Trigger attack with fast challenge_timeout=0.08 for deterministic async test
        await handle_pvp_command(self.node, "PatientAttacker", "#arena", "attack", "IdleDefender", challenge_timeout=0.08)
        self.assertIn("idledefender", self.node.pending_challenges)

        # Wait for the async timer callback to actually execute and expire the challenge
        await self._wait_until(lambda: "idledefender" not in self.node.pending_challenges, timeout=1.0)
        self.assertNotIn("idledefender", self.node.pending_challenges)
        self.assertIsNone(self.node.active_engine)

        sent_msgs = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("ENGAGEMENT_TIMEOUT" in m and "expired safely" in m for m in sent_msgs))

    async def test_pve_forced_mob_encounters(self):
        """R1 Condition 3: Mob encounters force combat with countdown or engage."""
        node = await self._create_test_node("MobNode", node_type="void", region_type="VOD")
        await self._create_test_character("ExplorerBob", node_id=node.id, is_nickserv_verified=True)

        await handle_mob_encounter(self.node, "ExplorerBob", "MobNode", threat=1, prev_node="Spawn", reply_target="#arena")
        self.assertIn("ExplorerBob", self.node.pending_encounters)

        # Engage mob
        await resolve_mob(self.node, "ExplorerBob", "#arena")
        self.assertNotIn("ExplorerBob", self.node.pending_encounters)

    # =========================================================================
    # R4 Tests: Claimed Node Stability Decay Exemption
    # =========================================================================

    async def test_stability_decay_exemption_claimed_node_idling(self):
        """R4: Idling on an owned/claimed node explicitly exempts character from stability decay."""
        owned_node = await self._create_test_node("MyClaimedNode", node_type="void", region_type="VOD")
        unowned_node = await self._create_test_node("PublicVoidNode", node_type="void", region_type="VOD")

        char_owner = await self._create_test_character("TerritoryOwner", node_id=owned_node.id)
        char_guest = await self._create_test_character("VoidIdler", node_id=unowned_node.id)

        # Set ownership of owned_node to char_owner
        async with self.async_session() as session:
            n = (await session.execute(select(GridNode).where(GridNode.id == owned_node.id))).scalars().first()
            n.owner_character_id = char_owner.id
            await session.commit()

        # Set initial stability
        async with self.async_session() as session:
            c1 = (await session.execute(select(Character).where(Character.id == char_owner.id))).scalars().first()
            c2 = (await session.execute(select(Character).where(Character.id == char_guest.id))).scalars().first()
            c1.stability = 80.0
            c2.stability = 80.0
            await session.commit()

        maintenance_repo = MaintenanceRepository(self.async_session)

        # Run tick_player_maintenance with both players idling
        await maintenance_repo.tick_player_maintenance(network="testnet", idlers=["TerritoryOwner", "VoidIdler"])

        async with self.async_session() as session:
            c1_after = (await session.execute(select(Character).where(Character.id == char_owner.id))).scalars().first()
            c2_after = (await session.execute(select(Character).where(Character.id == char_guest.id))).scalars().first()

            # TerritoryOwner was idling at their owned node:
            # Base decay (80.0 * 0.01 / 24) was NOT deducted!
            # They recovered +2.0 stability as safe idler -> 82.0
            self.assertEqual(c1_after.stability, 82.0)

            # VoidIdler was idling at an unowned void node:
            # Base decay WAS deducted: 80.0 - (80.0 * 0.01 / 24) = 79.96667
            # Then void recovery +0.5 was added -> 80.46667
            decay_rate = 0.01 / 24.0
            expected_guest_stability = (80.0 - (80.0 * decay_rate)) + 0.5
            self.assertAlmostEqual(c2_after.stability, expected_guest_stability, places=4)

    async def test_optional_open_world_pvp_challenge_when_defender_has_alias(self):
        """R2: Challenge resolution works when challenged defender is authenticated via alias."""
        neutral_node = await self._create_test_node("AliasSector", node_type="void", region_type="VOD")
        await self._create_test_character("AlphaAttacker", node_id=neutral_node.id, is_nickserv_verified=True)
        def_char = await self._create_test_character("DefenderRealChar", node_id=neutral_node.id, is_nickserv_verified=True)

        # DefenderRealChar is logged in under active IRC nick "DefenderNick"
        await self.router.dispatch("DefenderNick", "PRIVMSG", "#arena", f"!a login DefenderRealChar {def_char.auth_token}", is_admin=False)
        await self._wait_until(lambda: "defendernick" in getattr(self.node, 'authenticated_users', set()))
        self.assertTrue(self.node.is_authenticated("DefenderNick"))

        # AlphaAttacker attacks DefenderRealChar
        await self.router.dispatch("AlphaAttacker", "PRIVMSG", "#arena", "!a attack DefenderRealChar", is_admin=False)
        await self._wait_until(lambda: len(self.node.pending_challenges) > 0)
        self.assertEqual(len(self.node.pending_challenges), 1)

        # DefenderNick accepts with 'engage'
        await self.router.dispatch("DefenderNick", "PRIVMSG", "#arena", "engage", is_admin=False)
        await self._wait_until(lambda: self.node.active_engine is not None)
        self.assertIsNotNone(self.node.active_engine)
        self.assertEqual(len(self.node.pending_challenges), 0)

    async def test_flee_fallback_to_spawn_node_when_no_prior_movement(self):
        """R2: Fleeing without prior recorded movement retreats to spawn node rather than remaining in place."""
        spawn_node = await self._create_test_node("NexusSafeSpawn", node_type="safezone", region_type="CIV")
        async with self.async_session() as session:
            sn = (await session.execute(select(GridNode).where(GridNode.id == spawn_node.id))).scalars().first()
            sn.is_spawn_node = True
            await session.commit()

        ambush_node = await self._create_test_node("AmbushZone", node_type="void", region_type="VOD")
        await self._create_test_character("AmbushAttacker", node_id=ambush_node.id, is_nickserv_verified=True)
        victim = await self._create_test_character("FreshSpawnVictim", node_id=ambush_node.id, is_nickserv_verified=True)

        # No prior location recorded in self.node.last_player_locations
        self.assertNotIn("freshspawnvictim", self.node.last_player_locations)

        await self.router.dispatch("AmbushAttacker", "PRIVMSG", "#arena", "!a attack FreshSpawnVictim", is_admin=False)
        await self._wait_until(lambda: "freshspawnvictim" in self.node.pending_challenges)

        # Victim flees
        await self.router.dispatch("FreshSpawnVictim", "PRIVMSG", "#arena", "!a flee", is_admin=False)
        await self._wait_until(lambda: any("NexusSafeSpawn" in m for m in [call[0][0] for call in self.node.send.call_args_list]))
        self.assertNotIn("freshspawnvictim", self.node.pending_challenges)

        # Victim should have been moved to spawn node
        async with self.async_session() as session:
            c = (await session.execute(select(Character).options(selectinload(Character.current_node)).where(Character.id == victim.id))).scalars().first()
            self.assertEqual(c.current_node.name, "NexusSafeSpawn")

    async def test_pvp_anti_interference_and_combat_lock(self):
        """R1 & R2: Engaged players are locked from third-party attacks and cannot spam challenges."""
        zone = await self._create_test_node("DuellingGrounds", node_type="void", region_type="VOD")
        await self._create_test_character("DuelistA", node_id=zone.id, is_nickserv_verified=True)
        await self._create_test_character("DuelistB", node_id=zone.id, is_nickserv_verified=True)
        await self._create_test_character("IntruderC", node_id=zone.id, is_nickserv_verified=True)

        # DuelistA attacks DuelistB -> pending challenge
        await self.router.dispatch("DuelistA", "PRIVMSG", "#arena", "!a attack DuelistB", is_admin=False)
        await self._wait_until(lambda: "duelistb" in self.node.pending_challenges)

        # IntruderC tries to attack DuelistB while challenge pending -> rejected
        self.node.send.reset_mock()
        await self.router.dispatch("IntruderC", "PRIVMSG", "#arena", "!a attack DuelistB", is_admin=False)
        await self._wait_until(lambda: any("already responding to an engagement challenge" in call[0][0] for call in self.node.send.call_args_list))
        sent_msgs = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("already responding to an engagement challenge" in m for m in sent_msgs))

        # DuelistB engages -> combat started
        await self.router.dispatch("DuelistB", "PRIVMSG", "#arena", "engage", is_admin=False)
        await self._wait_until(lambda: self.node.active_engine is not None)

        # IntruderC tries to attack DuelistA while in active combat -> locked out
        self.node.send.reset_mock()
        await self.router.dispatch("IntruderC", "PRIVMSG", "#arena", "!a attack DuelistA", is_admin=False)
        await self._wait_until(lambda: any("locked in combat" in call[0][0] for call in self.node.send.call_args_list))
        sent_msgs2 = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("locked in combat" in m for m in sent_msgs2))

    async def test_warzone_movement_does_not_force_combat_with_self_when_using_alias(self):
        """R1 Condition 2: Moving into a Warzone under an alias nickname does NOT force combat with oneself."""
        from ai_grid.core.handlers.grid import handle_grid_movement
        war_node = await self._create_test_node("LonelyWarzone", node_type="void", region_type="WAR")
        char = await self._create_test_character("WarriorBot", node_id=war_node.id, is_nickserv_verified=True)

        # Login under active IRC nick "WarriorNick"
        await self.router.dispatch("WarriorNick", "PRIVMSG", "#arena", f"!a login WarriorBot {char.auth_token}", is_admin=False)

        # Move around inside or into the warzone
        self.node.send.reset_mock()
        self.db.move_player = AsyncMock(return_value=({'name': 'LonelyWarzone', 'region_type': 'WAR', 'type': 'void'}, "Moved"))
        self.db.get_location = AsyncMock(return_value={'region_type': 'WAR', 'type': 'void'})

        await handle_grid_movement(self.node, "WarriorNick", "north", "#arena")
        # Ensure no active combat engine was started against oneself
        self.assertIsNone(self.node.active_engine)
        sent_msgs = [call[0][0] for call in self.node.send.call_args_list]
        self.assertFalse(any("WARZONE" in m and "FORCED_COMBAT" in m for m in sent_msgs))

    async def test_ready_case_insensitivity(self):
        """R3: !a ready confirms ready regardless of nickname capitalization differences."""
        arn = await self._create_test_node("CaseArena", node_type="arena", region_type="ARN")
        await self._create_test_character("CaseGladiator", node_id=arn.id, is_nickserv_verified=True)

        await self.router.dispatch("CaseGladiator", "PRIVMSG", "#arena", "!a queue", is_admin=False)
        self.assertIn("CaseGladiator", self.node.match_queue)

        # User client sends ready in lowercase
        await self.router.dispatch("casegladiator", "PRIVMSG", "#arena", "!a ready", is_admin=False)
        self.assertTrue(any(p.lower() == "casegladiator" for p in self.node.ready_players))

    async def test_stability_decay_exemption_with_alias_nickname(self):
        """R4: Idling on an owned node under an alias nickname exempts character from stability decay."""
        owned_node = await self._create_test_node("AliasOwnedNode", node_type="void", region_type="VOD")
        char_owner = await self._create_test_character("TrueCharName", node_id=owned_node.id)

        # Bind alias "ActiveNick" to TrueCharName
        async with self.async_session() as session:
            session.add(NetworkAlias(player_id=char_owner.player_id, network_name="testnet", nickname="ActiveNick"))
            n = (await session.execute(select(GridNode).where(GridNode.id == owned_node.id))).scalars().first()
            n.owner_character_id = char_owner.id
            c = (await session.execute(select(Character).where(Character.id == char_owner.id))).scalars().first()
            c.stability = 80.0
            await session.commit()

        maintenance_repo = MaintenanceRepository(self.async_session)

        # Payouts/idlers list only contains IRC nick "ActiveNick"
        await maintenance_repo.tick_player_maintenance(network="testnet", idlers=["ActiveNick"])

        async with self.async_session() as session:
            c_after = (await session.execute(select(Character).where(Character.id == char_owner.id))).scalars().first()
            self.assertEqual(c_after.stability, 82.0)

    async def test_combat_action_routing_with_authenticated_session_alias(self):
        """R1 & R2: In-combat commands sent by an authenticated session nickname route to combat engine."""
        war_node = await self._create_test_node("WarzoneSector", node_type="void", region_type="WAR")
        attacker = await self._create_test_character("CombatAttacker", node_id=war_node.id, is_nickserv_verified=True)
        defender = await self._create_test_character("CombatDefender", node_id=war_node.id, is_nickserv_verified=True)

        # Defender is logged in under IRC nick "DefenderNick"
        await self.router.dispatch("DefenderNick", "PRIVMSG", "#arena", f"!a login CombatDefender {defender.auth_token}", is_admin=False)
        await self._wait_until(lambda: "defendernick" in self.node.authenticated_users)
        self.assertTrue(self.node.is_authenticated("DefenderNick"))

        # Combat is forced in warzone
        await self.router.dispatch("CombatAttacker", "PRIVMSG", "#arena", "!a attack CombatDefender", is_admin=False)
        await self._wait_until(lambda: self.node.active_engine is not None)
        self.assertIsNotNone(self.node.active_engine)

        # DefenderNick issues non-prefixed combat verb "hack"
        await self.router.dispatch("DefenderNick", "PRIVMSG", "#arena", "hack", is_admin=False)
        
        # Check command queued for defender in combat engine
        defender_ent = self.node.active_engine.get_entity("DefenderNick")
        self.assertIsNotNone(defender_ent)
        self.assertEqual(defender_ent.command_queued.get('raw_verb'), "hack")
        self.assertEqual(defender_ent.command_queued.get('intent'), "cyber")

    async def test_ready_non_prefixed_command(self):
        """R3: Non-prefixed 'ready' command confirms drop-pod lock without requiring '!a' prefix."""
        arn = await self._create_test_node("NonPrefixedArena", node_type="arena", region_type="ARN")
        await self._create_test_character("DropPodPilot", node_id=arn.id, is_nickserv_verified=True)

        await self.router.dispatch("DropPodPilot", "PRIVMSG", "#arena", "!a queue", is_admin=False)
        self.assertIn("DropPodPilot", self.node.match_queue)

        # Sends 'ready' without '!a'
        await self.router.dispatch("DropPodPilot", "PRIVMSG", "#arena", "ready", is_admin=False)
        await self._wait_until(lambda: self.node.check_match_start.called)
        self.assertIn("DropPodPilot", self.node.ready_players)
        self.node.check_match_start.assert_called()

    async def test_mob_flee_fallback_to_spawn_node_when_no_prior_movement(self):
        """R1 & R2: Fleeing from a mob encounter without prior movement retreats to spawn node."""
        spawn_node = await self._create_test_node("MobSafeSpawn", node_type="safezone", region_type="CIV")
        async with self.async_session() as session:
            sn = (await session.execute(select(GridNode).where(GridNode.id == spawn_node.id))).scalars().first()
            sn.is_spawn_node = True
            await session.commit()

        mob_zone = await self._create_test_node("DeepDarkVoid", node_type="void", region_type="VOD")
        char = await self._create_test_character("LostExplorer", node_id=mob_zone.id, is_nickserv_verified=True)

        # Mob encounter with prev_node=None
        await handle_mob_encounter(self.node, "LostExplorer", "DeepDarkVoid", threat=1, prev_node=None, reply_target="#arena")
        self.assertIn("LostExplorer", self.node.pending_encounters)

        # LostExplorer flees
        await self.router.dispatch("LostExplorer", "PRIVMSG", "#arena", "!a flee", is_admin=False)
        await self._wait_until(lambda: "LostExplorer" not in self.node.pending_encounters)

        # LostExplorer should have retreated to spawn node
        async with self.async_session() as session:
            c = (await session.execute(select(Character).options(selectinload(Character.current_node)).where(Character.id == char.id))).scalars().first()
            self.assertEqual(c.current_node.name, "MobSafeSpawn")

    async def test_mob_encounter_resolution_with_authenticated_session_alias(self):
        """R1 Condition 3: Mob encounter resolves successfully when player is logged in with an alias nickname."""
        spawn_node = await self._create_test_node("NexusSpawnMob", node_type="safezone", region_type="CIV")
        mob_node = await self._create_test_node("DeepRuins", node_type="void", region_type="VOD")
        char = await self._create_test_character("MobHunterReal", node_id=mob_node.id, is_nickserv_verified=True)

        # Login under active nickname "HunterNick"
        await self.router.dispatch("HunterNick", "PRIVMSG", "#arena", f"!a login MobHunterReal {char.auth_token}", is_admin=False)
        await self._wait_until(lambda: "hunternick" in self.node.authenticated_users)
        self.assertTrue(self.node.is_authenticated("HunterNick"))

        # Trigger mob encounter under active IRC nick "HunterNick"
        await handle_mob_encounter(self.node, "HunterNick", "DeepRuins", threat=1, prev_node="NexusSpawnMob", reply_target="#arena")
        self.assertIn("HunterNick", self.node.pending_encounters)

        # HunterNick engages mob via 'engage'
        await self.router.dispatch("HunterNick", "PRIVMSG", "#arena", "engage", is_admin=False)
        await self._wait_until(lambda: any("neutralized" in call[0][0] or "overwhelmed" in call[0][0] for call in self.node.send.call_args_list))
        self.assertNotIn("HunterNick", self.node.pending_encounters)

        # Verify encounter resolved cleanly without error message
        sent_msgs = [call[0][0] for call in self.node.send.call_args_list]
        self.assertFalse(any("Character not found" in m for m in sent_msgs))
        self.assertTrue(any("neutralized" in m or "overwhelmed" in m for m in sent_msgs))

    async def test_raid_hack_triggers_territory_defense_forced_combat(self):
        """R1 Condition 1: Issuing !a raid hack on an owned node with owner present forces combat."""
        base_node = await self._create_test_node("Fortress_Raid", node_type="void", region_type="VOD")
        defender = await self._create_test_character("RaidDefender", node_id=base_node.id, is_nickserv_verified=True)
        attacker = await self._create_test_character("RaidAttacker", node_id=base_node.id, is_nickserv_verified=True)

        async with self.async_session() as session:
            n = (await session.execute(select(GridNode).where(GridNode.id == base_node.id))).scalars().first()
            n.owner_character_id = defender.id
            await session.commit()

        # Attacker executes !a raid hack while owner is present
        await self.router.dispatch("RaidAttacker", "PRIVMSG", "#arena", "!a raid hack", is_admin=False)

        await self._wait_until(lambda: self.node.active_engine is not None)
        self.assertIsNotNone(self.node.active_engine)
        self.assertTrue(self.node.active_engine.active)
        self.assertIn("RaidAttacker", self.node.active_engine.entities)
        self.assertIn("RaidDefender", self.node.active_engine.entities)

        sent_msgs = [call[0][0] for call in self.node.send.call_args_list]
        self.assertTrue(any("FORCED_COMBAT" in m and "TERRITORY_DEFENSE" in m for m in sent_msgs))

    async def test_warzone_forced_combat_when_other_player_has_no_network_alias(self):
        """R1 Condition 2: Moving into a Warzone forces combat even if other character has no NetworkAlias."""
        from ai_grid.core.handlers.grid import handle_grid_movement
        war_node = await self._create_test_node("WildWarzone", node_type="void", region_type="WAR")

        # Create CharStandalone without NetworkAlias on testnet
        async with self.async_session() as session:
            p = Player(global_name="StandalonePlayer", is_autonomous=False)
            session.add(p)
            await session.flush()
            c = Character(
                player_id=p.id,
                name="StandaloneChar",
                race="Synth",
                char_class="Infiltrator",
                node_id=war_node.id,
                cpu=10, ram=10, bnd=10, sec=10, alg=10,
                current_hp=100, power=200.0, stability=100.0, credits=500.0, auth_token="tok_std"
            )
            session.add(c)
            await session.commit()

        # Mover moves into WildWarzone
        mover = await self._create_test_character("MoverSoldier", is_nickserv_verified=True)
        self.db.move_player = AsyncMock(return_value=({'name': 'WildWarzone', 'region_type': 'WAR', 'type': 'void'}, "Moved"))
        self.db.get_location = AsyncMock(return_value={'region_type': 'WAR', 'type': 'void'})

        await handle_grid_movement(self.node, "MoverSoldier", "north", "#arena")
        await self._wait_until(lambda: self.node.active_engine is not None)

        self.assertIsNotNone(self.node.active_engine)
        self.assertTrue(self.node.active_engine.active)
        self.assertIn("MoverSoldier", self.node.active_engine.entities)
        self.assertIn("StandaloneChar", self.node.active_engine.entities)


if __name__ == "__main__":
    unittest.main()
