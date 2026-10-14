import os
import json
import math
import time
import unittest
from datetime import datetime, timezone
from sqlalchemy import select, func, text
from sqlalchemy.orm import selectinload

from ai_grid.grid_db import ArenaDB
from ai_grid.models import Base, GridNode, Character, Player, NetworkAlias, RaidTarget
from ai_grid.core.handlers.admin import handle_admin_command


class MockIRCNode:
    """Mock node for IRC network message handling and testing admin commands."""
    def __init__(self, db, net_name="testnet", admin_nick="AdminSys"):
        self.db = db
        self.net_name = net_name
        self.admins = [admin_nick.lower()]
        self.nickserv_verified = {admin_nick.lower()}
        self.admin_sessions = {admin_nick.lower(): time.time() + 3600}
        self.prefix = "!a"
        self.config = {
            "channel": "#arena",
            "nickname": "AutomataBot",
            "admin_token": "secret_token"
        }
        self.sent_messages = []

    def is_user_verified(self, nick):
        return nick.lower() in self.nickserv_verified

    def is_admin(self, nick):
        return nick.lower() in self.admins

    async def send(self, msg, immediate=False):
        self.sent_messages.append(msg)


class TestGridGeneration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = ArenaDB(db_path=":memory:")
        async with self.db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.mock_irc = MockIRCNode(self.db)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_50x50_topology_generation_and_counts(self):
        """Verify 2,500 coordinate topology generation with calibrated population counts."""
        await self.db.generate_master_grid(width=50, height=50)

        stats = await self.db.expansion.get_grid_stats()
        self.assertEqual(stats["width"], 50)
        self.assertEqual(stats["height"], 50)
        self.assertEqual(stats["total_nodes"], 2500)
        
        # Population target calibration: ~700 active (28%), ~1800 void (72%)
        self.assertEqual(stats["active_nodes"], 700)
        self.assertEqual(stats["void_nodes"], 1800)

        # Detailed controller breakdown:
        # MCP controlled: 400
        # NPC / merchant: 150
        # Raid targets: 100
        # Player claimable: 50
        controllers = stats["controllers"]
        self.assertEqual(controllers.get("MCP", 0), 400)
        self.assertEqual(controllers.get("NPC", 0), 150)
        self.assertEqual(controllers.get("RAID", 0), 100)
        self.assertEqual(controllers.get("CLAIMABLE", 0), 50)
        
        # Verify 100 actual RaidTarget model instances exist
        self.assertEqual(stats["raid_targets_count"], 100)

    async def test_all_14_diverse_region_types_present(self):
        """Verify all 14+ region types are present and distributed across the grid."""
        await self.db.generate_master_grid(width=50, height=50)

        stats = await self.db.expansion.get_grid_stats()
        regions = stats["regions"]
        
        expected_types = [
            "CIV", "SMB", "CRP", "EDU", "GOV", "MED", "MIL", "ORG",
            "LEA", "DTC", "POS", "ICS", "UTL", "ARN", "WAR", "VOD"
        ]
        for r_code in expected_types:
            self.assertIn(r_code, regions, f"Region type {r_code} missing from stats")
            self.assertGreater(regions[r_code], 0, f"Region type {r_code} has zero nodes")

        # Sum of all regions must equal total node count (2500)
        self.assertEqual(sum(regions.values()), 2500)

    async def test_realistic_topological_clustering(self):
        """
        Verify nodes cluster logically into believable network zones:
        - Critical Infrastructure: ICS paired with UTL
        - Corporate / Financial: CRP, DTC, POS clustered
        - Defense perimeter: GOV, LEA, MIL clustered
        - Public / Civic: CIV, SMB, EDU, ORG, MED clustered
        """
        await self.db.generate_master_grid(width=50, height=50)

        async with self.db.async_session() as session:
            # 1. Critical Infrastructure Zone: ICS + UTL clustering around (12, 26)
            ics_nodes = (await session.execute(
                select(GridNode).where(GridNode.region_type == 'ICS')
            )).scalars().all()
            utl_nodes = (await session.execute(
                select(GridNode).where(GridNode.region_type == 'UTL')
            )).scalars().all()
            
            self.assertGreater(len(ics_nodes), 0)
            self.assertGreater(len(utl_nodes), 0)
            
            # ICS nodes should be clustered on western side of grid (x <= 16)
            for node in ics_nodes:
                self.assertLessEqual(node.x, 16, f"ICS node {node.name} at x={node.x} outside infrastructure sector")
            for node in utl_nodes:
                self.assertLessEqual(node.x, 22, f"UTL node {node.name} at x={node.x} outside infrastructure sector")

            # 2. Corporate / Financial Zone: CRP + DTC + POS clustering in eastern quadrant (x >= 30)
            crp_nodes = (await session.execute(
                select(GridNode).where(GridNode.region_type == 'CRP')
            )).scalars().all()
            dtc_nodes = (await session.execute(
                select(GridNode).where(GridNode.region_type == 'DTC', GridNode.net_affinity == None)
            )).scalars().all()
            pos_nodes = (await session.execute(
                select(GridNode).where(GridNode.region_type == 'POS')
            )).scalars().all()

            self.assertGreater(len(crp_nodes), 0)
            self.assertGreater(len(dtc_nodes), 0)
            self.assertGreater(len(pos_nodes), 0)

            for node in crp_nodes:
                self.assertGreaterEqual(node.x, 30, f"CRP node {node.name} at x={node.x} outside corporate sector")
            for node in dtc_nodes:
                self.assertGreaterEqual(node.x, 30, f"DTC node {node.name} at x={node.x} outside corporate sector")

            # 3. Defense Perimeter: GOV + LEA + MIL clustering in northern quadrant (y <= 20)
            gov_nodes = (await session.execute(
                select(GridNode).where(GridNode.region_type == 'GOV')
            )).scalars().all()
            lea_nodes = (await session.execute(
                select(GridNode).where(GridNode.region_type == 'LEA')
            )).scalars().all()
            mil_nodes = (await session.execute(
                select(GridNode).where(GridNode.region_type == 'MIL')
            )).scalars().all()

            for node in gov_nodes:
                self.assertLessEqual(node.y, 20, f"GOV node {node.name} at y={node.y} outside defense perimeter")
            for node in lea_nodes:
                self.assertLessEqual(node.y, 20, f"LEA node {node.name} at y={node.y} outside defense perimeter")
            for node in mil_nodes:
                self.assertLessEqual(node.y, 20, f"MIL node {node.name} at y={node.y} outside defense perimeter")

            # 4. Operational Type Compatibility:
            # Arena nodes must have node_type == 'arena'
            arn_nodes = (await session.execute(
                select(GridNode).where(GridNode.region_type == 'ARN')
            )).scalars().all()
            for a_node in arn_nodes:
                self.assertEqual(a_node.node_type, "arena")

            # NPC / Merchant nodes must have node_type == 'merchant'
            merchant_nodes = (await session.execute(
                select(GridNode).where(GridNode.controller == 'NPC')
            )).scalars().all()
            for m_node in merchant_nodes:
                self.assertEqual(m_node.node_type, "merchant")

    async def test_core_hubs_preservation(self):
        """Verify core hubs (UpLink, Arena, Vault, Rizon, 2600net, Edges) retain fixed positions."""
        await self.db.generate_master_grid(width=50, height=50)

        async with self.db.async_session() as session:
            # UpLink at (25, 25)
            uplink = (await session.execute(select(GridNode).where(GridNode.name == "UpLink"))).scalars().first()
            self.assertIsNotNone(uplink)
            self.assertEqual((uplink.x, uplink.y), (25, 25))
            self.assertTrue(uplink.is_spawn_node)
            self.assertTrue(uplink.is_unlocked)
            self.assertEqual(uplink.node_type, "safezone")
            self.assertEqual(uplink.region_type, "CIV")

            # Arena at (25, 30)
            arena = (await session.execute(select(GridNode).where(GridNode.name == "Arena"))).scalars().first()
            self.assertIsNotNone(arena)
            self.assertEqual((arena.x, arena.y), (25, 30))
            self.assertEqual(arena.node_type, "arena")
            self.assertEqual(arena.region_type, "ARN")
            self.assertTrue(arena.is_unlocked)

            # Vault at (25, 45)
            vault = (await session.execute(select(GridNode).where(GridNode.name == "Vault"))).scalars().first()
            self.assertIsNotNone(vault)
            self.assertEqual((vault.x, vault.y), (25, 45))
            self.assertEqual(vault.node_type, "safezone")

            # Rizon at (10, 40)
            rizon = (await session.execute(select(GridNode).where(GridNode.name == "Rizon"))).scalars().first()
            self.assertIsNotNone(rizon)
            self.assertEqual((rizon.x, rizon.y), (10, 40))
            self.assertEqual(rizon.net_affinity, "rizon")
            self.assertEqual(rizon.availability_mode, "OPEN")
            rizon_addons = json.loads(rizon.addons_json or "{}")
            self.assertTrue(rizon_addons.get("NET"))

            # 2600net at (40, 10)
            net2600 = (await session.execute(select(GridNode).where(GridNode.name == "2600net"))).scalars().first()
            self.assertIsNotNone(net2600)
            self.assertEqual((net2600.x, net2600.y), (40, 10))
            self.assertEqual(net2600.net_affinity, "2600net")
            self.assertEqual(net2600.availability_mode, "OPEN")
            net2600_addons = json.loads(net2600.addons_json or "{}")
            self.assertTrue(net2600_addons.get("NET"))

    async def test_dynamic_irc_network_home_node_spawning(self):
        """Verify dynamic spawning of dedicated home nodes when connecting to a new IRC network."""
        await self.db.generate_master_grid(width=50, height=50)

        # Spawning unseeded network: Libera
        libera_node = await self.db.ensure_network_home_node("Libera")
        self.assertIsNotNone(libera_node)
        self.assertEqual(libera_node.name, "Libera")
        self.assertEqual(libera_node.net_affinity, "Libera")
        self.assertEqual(libera_node.availability_mode, "OPEN")
        self.assertEqual(libera_node.upgrade_level, 1)
        self.assertTrue(libera_node.is_unlocked)
        addons = json.loads(libera_node.addons_json or "{}")
        self.assertTrue(addons.get("NET"))

        # Verify idempotency (calling again returns same node without duplicates)
        libera_node2 = await self.db.ensure_network_home_node("Libera")
        self.assertEqual(libera_node.id, libera_node2.id)

        # Case-insensitivity check
        libera_node_lower = await self.db.ensure_network_home_node("libera")
        self.assertEqual(libera_node.id, libera_node_lower.id)

        # Spawning another network: EFnet
        efnet_node = await self.db.ensure_network_home_node("EFnet")
        self.assertIsNotNone(efnet_node)
        self.assertEqual(efnet_node.name, "EFnet")
        self.assertEqual(efnet_node.net_affinity, "EFnet")
        self.assertEqual(efnet_node.availability_mode, "OPEN")
        efnet_addons = json.loads(efnet_node.addons_json or "{}")
        self.assertTrue(efnet_addons.get("NET"))

        # Check status reflects active home nodes
        status = await self.db.expansion.get_grid_status()
        self.assertIn("Libera", status["home_nodes"])
        self.assertIn("EFnet", status["home_nodes"])
        self.assertIn("rizon", status["home_nodes"])
        self.assertIn("2600net", status["home_nodes"])

    async def test_admin_map_stats_and_status_commands(self):
        """Verify admin map stats and admin map status commands via IRC handler."""
        await self.db.generate_master_grid(width=50, height=50)

        # 1. admin map stats
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "stats"], reply_target="#arena")
        
        all_text = " ".join(self.mock_irc.sent_messages)
        self.assertIn("GRID TOPOLOGY STATS", all_text)
        self.assertIn("50x50", all_text)
        self.assertIn("ACTIVE: 700", all_text)
        self.assertIn("VOID: 1,800", all_text)
        self.assertIn("REGIONS:", all_text)

        # 2. admin map (default without args displays stats)
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map"], reply_target="#arena")
        all_text_default = " ".join(self.mock_irc.sent_messages)
        self.assertIn("GRID TOPOLOGY STATS", all_text_default)

        # 3. admin map status
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "status"], reply_target="#arena")
        all_text_status = " ".join(self.mock_irc.sent_messages)
        self.assertIn("GRID OPERATIONAL STATUS", all_text_status)
        self.assertIn("HEALTH:", all_text_status)
        self.assertIn("POWER:", all_text_status)
        self.assertIn("NETWORKS:", all_text_status)

    async def test_admin_map_info_inspection(self):
        """Verify admin map info command inspecting node telemetry by name and coordinates."""
        await self.db.generate_master_grid(width=50, height=50)

        # 1. By name: admin map info UpLink
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "info", "UpLink"], reply_target="#arena")
        msg = " ".join(self.mock_irc.sent_messages)
        self.assertIn("NODE TELEMETRY", msg)
        self.assertIn("UpLink (25, 25)", msg)
        self.assertIn("Region: CIV", msg)
        self.assertIn("State: OPEN", msg)

        # 2. By coordinates: admin map info 25 25
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "info", "25", "25"], reply_target="#arena")
        msg_coords = " ".join(self.mock_irc.sent_messages)
        self.assertIn("NODE TELEMETRY", msg_coords)
        self.assertIn("UpLink (25, 25)", msg_coords)

        # 3. Shortcut syntax: admin map 25 30 (Arena)
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "25", "30"], reply_target="#arena")
        msg_shortcut = " ".join(self.mock_irc.sent_messages)
        self.assertIn("Arena (25, 30)", msg_shortcut)
        self.assertIn("Region: ARN", msg_shortcut)

        # 4. Non-existent target
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "info", "NonExistentSector999"], reply_target="#arena")
        msg_err = " ".join(self.mock_irc.sent_messages)
        self.assertIn("Node or coordinates not found", msg_err)

    async def test_admin_map_expand_dynamic_perimeter(self):
        """
        Verify admin map expand dynamically expands the grid by +10x10 coordinates
        (50x50 -> 60x60) with proportionate realistic clustering without corrupting existing state.
        """
        await self.db.generate_master_grid(width=50, height=50)

        # Create an existing player, character, and node stash item to verify state preservation
        async with self.db.async_session() as session:
            player = Player(global_name="Pilot1", is_autonomous=False)
            session.add(player)
            await session.flush()
            session.add(NetworkAlias(player_id=player.id, network_name="testnet", nickname="Pilot1"))
            
            # Find an existing node and stash item
            uplink = (await session.execute(select(GridNode).where(GridNode.name == "UpLink"))).scalars().first()
            uplink.stash_inventory = ["Encrypted_Keycard", "Logic_Probe"]
            
            char = Character(
                player_id=player.id,
                name="Pilot1",
                race="Synth",
                char_class="NetRunner",
                node_id=uplink.id,
                credits=500.0,
                power=85.0
            )
            session.add(char)
            uplink.owner_character_id = char.id
            await session.commit()

        # Run expansion: admin map expand (50x50 -> 60x60)
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "expand"], reply_target="#arena")
        
        all_text = " ".join(self.mock_irc.sent_messages)
        self.assertIn("Grid expanded from 50x50 to 60x60", all_text)
        self.assertIn("+1,100 coordinates added", all_text)

        # Verify new stats
        stats = await self.db.expansion.get_grid_stats()
        self.assertEqual(stats["width"], 60)
        self.assertEqual(stats["height"], 60)
        self.assertEqual(stats["total_nodes"], 3600)
        
        # Original 700 active + ~308 perimeter active = ~1,008 active nodes (~28%)
        self.assertEqual(stats["active_nodes"], 1008)
        self.assertEqual(stats["void_nodes"], 2592)

        # Verify preservation of existing state
        async with self.db.async_session() as session:
            uplink_check = (await session.execute(
                select(GridNode).where(GridNode.name == "UpLink")
            )).scalars().first()
            self.assertIsNotNone(uplink_check)
            self.assertEqual((uplink_check.x, uplink_check.y), (25, 25))
            self.assertEqual(uplink_check.stash_inventory, ["Encrypted_Keycard", "Logic_Probe"])
            
            char_check = (await session.execute(
                select(Character).where(Character.name == "Pilot1")
            )).scalars().first()
            self.assertIsNotNone(char_check)
            self.assertEqual(char_check.node_id, uplink_check.id)
            self.assertEqual(char_check.credits, 500.0)

        # Second expansion: 60x60 -> 70x70
        success, msg, data = await self.db.expansion.expand_grid(10, 10)
        self.assertTrue(success)
        self.assertEqual(data["new_dimensions"], (70, 70))
        self.assertEqual(data["total_added"], 1300)

        stats70 = await self.db.expansion.get_grid_stats()
        self.assertEqual(stats70["width"], 70)
        self.assertEqual(stats70["height"], 70)
        self.assertEqual(stats70["total_nodes"], 4900)

    async def test_raid_target_bidirectional_linkage(self):
        """
        Verify that all generated and perimeter RaidTarget instances have
        valid non-null foreign keys (node_id) linking back to GridNode.
        """
        await self.db.generate_master_grid(width=50, height=50)

        async with self.db.async_session() as session:
            stmt = select(RaidTarget).options(selectinload(RaidTarget.node))
            rts = (await session.execute(stmt)).scalars().all()
            self.assertEqual(len(rts), 100)
            for rt in rts:
                self.assertIsNotNone(rt.node_id, f"RaidTarget {rt.name} has null node_id")
                self.assertIsNotNone(rt.node, f"RaidTarget {rt.name} has no associated GridNode")
                self.assertEqual(rt.node.active_target_id, rt.id)
                self.assertEqual(rt.node.controller, "RAID")

        # Now expand grid and verify perimeter raid targets also link cleanly
        success, msg, data = await self.db.expansion.expand_grid(10, 10)
        self.assertTrue(success)

        async with self.db.async_session() as session:
            stmt = select(RaidTarget).options(selectinload(RaidTarget.node))
            all_rts = (await session.execute(stmt)).scalars().all()
            self.assertGreater(len(all_rts), 100)
            for rt in all_rts:
                self.assertIsNotNone(rt.node_id, f"Perimeter RaidTarget {rt.name} has null node_id")
                self.assertIsNotNone(rt.node, f"Perimeter RaidTarget {rt.name} has no associated GridNode")
                self.assertEqual(rt.node.active_target_id, rt.id)

    async def test_dynamic_irc_home_node_edge_cases_and_core_hub_protection(self):
        """
        Verify input sanitization, core hub protection, power allocation, and controller
        assignment in ensure_network_home_node.
        """
        await self.db.generate_master_grid(width=50, height=50)

        # 1. Empty and whitespace-only network names return None
        self.assertIsNone(await self.db.ensure_network_home_node(""))
        self.assertIsNone(await self.db.ensure_network_home_node("   "))
        self.assertIsNone(await self.db.ensure_network_home_node(None))

        # 2. Leading and trailing whitespace is stripped
        libera = await self.db.ensure_network_home_node("   Libera   ")
        self.assertIsNotNone(libera)
        self.assertEqual(libera.name, "Libera")
        self.assertEqual(libera.net_affinity, "Libera")
        self.assertEqual(libera.controller, "MCP")
        self.assertGreaterEqual(libera.power_stored, 100.0)

        # Calling again with lowercase untrimmed returns the exact same node
        libera2 = await self.db.ensure_network_home_node(" libera ")
        self.assertEqual(libera.id, libera2.id)

        # 3. Core hubs (Edge_West at 10,10 and Edge_East at 40,40) are NOT overwritten
        async with self.db.async_session() as session:
            ew = (await session.execute(select(GridNode).where(GridNode.name == "Edge_West"))).scalars().first()
            ee = (await session.execute(select(GridNode).where(GridNode.name == "Edge_East"))).scalars().first()
            self.assertIsNotNone(ew)
            self.assertIsNotNone(ee)
            self.assertEqual((ew.x, ew.y), (10, 10))
            self.assertEqual((ee.x, ee.y), (40, 40))

    async def test_admin_map_security_and_coordinate_edge_cases(self):
        """
        Verify access control gate, negative coordinates, and syntax error responses.
        """
        await self.db.generate_master_grid(width=50, height=50)

        # 1. Non-admin user access rejection
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "RegularUser", "admin", ["map", "stats"], reply_target="#arena")
        err_msg = " ".join(self.mock_irc.sent_messages)
        self.assertIn("Access Denied", err_msg)

        # 2. admin map info with no arguments displays syntax error
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "info"], reply_target="#arena")
        syntax_msg = " ".join(self.mock_irc.sent_messages)
        self.assertIn("Syntax:", syntax_msg)

        # 3. Negative coordinates lookup does not crash
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "info", "-5", "-5"], reply_target="#arena")
        neg_msg = " ".join(self.mock_irc.sent_messages)
        self.assertIn("Node or coordinates not found", neg_msg)

    async def test_move_player_boundary_enforcement_after_expansion(self):
        """
        Verify player can navigate into newly expanded perimeter sectors
        and boundary errors enforce the new expanded grid terminus.
        """
        await self.db.generate_master_grid(width=50, height=50)

        async with self.db.async_session() as session:
            player = Player(global_name="Explorer", is_autonomous=False)
            session.add(player)
            await session.flush()
            session.add(NetworkAlias(player_id=player.id, network_name="testnet", nickname="Explorer"))

            # Place character at (49, 25)
            node_49 = (await session.execute(
                select(GridNode).where(GridNode.x == 49, GridNode.y == 25)
            )).scalars().first()
            node_49.is_unlocked = True

            char = Character(
                player_id=player.id,
                name="Explorer",
                race="Synth",
                char_class="NetRunner",
                node_id=node_49.id,
                power=100.0,
                credits=100.0
            )
            session.add(char)
            await session.commit()

        # Before expansion, moving East from (49, 25) fails at boundary terminus
        res, msg = await self.db.navigation.move_player("Explorer", "testnet", "east")
        self.assertIsNone(res)
        self.assertIn("BOUNDARY ERROR", msg)

        # Expand grid by +10x10 (50x50 -> 60x60)
        success, expand_msg, _ = await self.db.expansion.expand_grid(10, 10)
        self.assertTrue(success)

        # Unlock perimeter node (50, 25) for navigation
        async with self.db.async_session() as session:
            node_50 = (await session.execute(
                select(GridNode).where(GridNode.x == 50, GridNode.y == 25)
            )).scalars().first()
            node_50.is_unlocked = True
            await session.commit()

        # Moving East from (49, 25) now successfully enters (50, 25)
        new_loc_name, move_msg = await self.db.navigation.move_player("Explorer", "testnet", "east")
        self.assertIsNotNone(new_loc_name)
        loc = await self.db.navigation.get_location("Explorer", "testnet")
        self.assertIsNotNone(loc)
        self.assertEqual(new_loc_name, loc["name"])
        self.assertEqual(loc["x"], 50)
        self.assertEqual(loc["y"], 25)

    async def test_admin_map_info_coordinate_formatting_and_name_variations(self):
        """Verify robust parsing of brackets, parentheses, spaces vs underscores, and raid targets in admin map info."""
        await self.db.generate_master_grid(width=50, height=50)

        # 1. Parenthesized coordinates: (25, 25)
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "info", "(25,", "25)"], reply_target="#arena")
        msg = " ".join(self.mock_irc.sent_messages)
        self.assertIn("UpLink (25, 25)", msg)

        # 2. Bracketed coordinates: [25, 30] (Arena)
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "info", "[25,", "30]"], reply_target="#arena")
        msg = " ".join(self.mock_irc.sent_messages)
        self.assertIn("Arena (25, 30)", msg)

        # 3. Angle brackets: <25, 25>
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "info", "<25,", "25>"], reply_target="#arena")
        msg = " ".join(self.mock_irc.sent_messages)
        self.assertIn("UpLink (25, 25)", msg)

        # 4. Comma-separated: 25,25
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "info", "25,25"], reply_target="#arena")
        msg = " ".join(self.mock_irc.sent_messages)
        self.assertIn("UpLink (25, 25)", msg)

        # 5. Space variation matching underscore: edge west -> Edge_West
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "info", "edge", "west"], reply_target="#arena")
        msg = " ".join(self.mock_irc.sent_messages)
        self.assertIn("Edge_West (10, 10)", msg)

        # 6. Lookup via Raid Target name
        async with self.db.async_session() as session:
            rt = (await session.execute(select(RaidTarget).limit(1))).scalars().first()
            self.assertIsNotNone(rt)
            rt_name = rt.name

        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "info", rt_name], reply_target="#arena")
        msg = " ".join(self.mock_irc.sent_messages)
        self.assertIn("NODE TELEMETRY", msg)

    async def test_admin_map_expand_custom_dimensions_and_scaling(self):
        """Verify custom expansion deltas and successive expansion up to 100x100."""
        await self.db.generate_master_grid(width=50, height=50)

        # Custom delta expansion via admin CLI: admin map expand 5 5 (50x50 -> 55x55)
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "expand", "5", "5"], reply_target="#arena")
        msg = " ".join(self.mock_irc.sent_messages)
        self.assertIn("Grid expanded from 50x50 to 55x55", msg)
        stats55 = await self.db.expansion.get_grid_stats()
        self.assertEqual(stats55["width"], 55)
        self.assertEqual(stats55["height"], 55)
        self.assertEqual(stats55["total_nodes"], 3025)

        # Scale up in steps to 100x100
        # 55x55 -> 65x65 (+10, +10)
        success, _, _ = await self.db.expansion.expand_grid(10, 10)
        self.assertTrue(success)
        # 65x65 -> 80x80 (+15, +15)
        success, _, _ = await self.db.expansion.expand_grid(15, 15)
        self.assertTrue(success)
        # 80x80 -> 100x100 (+20, +20)
        success, _, _ = await self.db.expansion.expand_grid(20, 20)
        self.assertTrue(success)

        stats100 = await self.db.expansion.get_grid_stats()
        self.assertEqual(stats100["width"], 100)
        self.assertEqual(stats100["height"], 100)
        self.assertEqual(stats100["total_nodes"], 10000)

        # Verify no duplicate coordinates
        async with self.db.async_session() as session:
            count_stmt = select(func.count(GridNode.id))
            distinct_stmt = select(func.count(func.distinct(GridNode.x * 10000 + GridNode.y)))
            total = (await session.execute(count_stmt)).scalar()
            distinct_coords = (await session.execute(distinct_stmt)).scalar()
            self.assertEqual(total, distinct_coords)

    async def test_ensure_network_home_node_stash_and_character_protection(self):
        """Verify that void nodes with player stashes are never overwritten and saturated grid behaves safely."""
        await self.db.generate_master_grid(width=50, height=50)

        # Add a stash to the highest ID void node
        async with self.db.async_session() as session:
            void_node = (await session.execute(
                select(GridNode).where(
                    GridNode.region_type == 'VOD',
                    GridNode.net_affinity == None,
                    GridNode.name.not_in(["Edge_West", "Edge_East"])
                ).order_by(GridNode.id.desc()).limit(1)
            )).scalars().first()
            void_node.stash_inventory = ["Valuable_Prototype_Device"]
            saved_name = void_node.name
            saved_id = void_node.id
            await session.commit()

        # Spawn a new home node: Libera
        libera = await self.db.ensure_network_home_node("Libera")
        self.assertIsNotNone(libera)
        self.assertNotEqual(libera.id, saved_id, "Home node should not overwrite a void node with an active stash")

        # Confirm the stashed node was untouched
        async with self.db.async_session() as session:
            stashed_node = (await session.execute(
                select(GridNode).where(GridNode.id == saved_id)
            )).scalars().first()
            self.assertEqual(stashed_node.name, saved_name)
            self.assertEqual(stashed_node.stash_inventory, ["Valuable_Prototype_Device"])
            self.assertIsNone(stashed_node.net_affinity)

    async def test_admin_map_status_power_and_durability_metrics(self):
        """Verify power metrics, damaged durability calculation, and claimed node tracking in admin map status."""
        await self.db.generate_master_grid(width=50, height=50)

        # Initial status
        init_status = await self.db.expansion.get_grid_status()
        self.assertEqual(init_status["claimed_nodes"], 0)
        self.assertGreater(init_status["power_stored"], 0.0)

        # Claim a node and degrade its durability
        async with self.db.async_session() as session:
            player = Player(global_name="AdminSys", is_autonomous=False)
            session.add(player)
            await session.flush()
            session.add(NetworkAlias(player_id=player.id, network_name="testnet", nickname="AdminSys"))
            char = Character(player_id=player.id, name="AdminSys", race="Synth", char_class="NetRunner", node_id=1)
            session.add(char)
            await session.flush()

            node = (await session.execute(select(GridNode).where(GridNode.id == 1))).scalars().first()
            node.owner_character_id = char.id
            node.durability = 50.0
            await session.commit()

        status = await self.db.expansion.get_grid_status()
        self.assertEqual(status["claimed_nodes"], 1)
        self.assertLess(status["health"], 100.0)

        # Verify command output reflects these changes
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "status"], reply_target="#arena")
        msg = " ".join(self.mock_irc.sent_messages)
        self.assertIn("CLAIMED: 1 nodes", msg)

    async def test_ensure_network_home_node_character_occupied_sector_protection(self):
        """Verify that void nodes occupied by active player characters are never repurposed as home nodes."""
        await self.db.generate_master_grid(width=50, height=50)

        # Place a player character on the highest-ID void node
        async with self.db.async_session() as session:
            void_node = (await session.execute(
                select(GridNode).where(
                    GridNode.region_type == 'VOD',
                    GridNode.net_affinity == None,
                    GridNode.name.not_in(["Edge_West", "Edge_East"])
                ).order_by(GridNode.id.desc()).limit(1)
            )).scalars().first()
            occupied_id = void_node.id
            occupied_name = void_node.name

            player = Player(global_name="Wanderer", is_autonomous=False)
            session.add(player)
            await session.flush()
            session.add(NetworkAlias(player_id=player.id, network_name="testnet", nickname="Wanderer"))
            char = Character(player_id=player.id, name="Wanderer", race="Synth", char_class="NetRunner", node_id=occupied_id)
            session.add(char)
            await session.commit()

        # Spawn Libera network home node
        home_node = await self.db.ensure_network_home_node("Libera")
        self.assertIsNotNone(home_node)
        self.assertNotEqual(home_node.id, occupied_id, "Home node must not repurpose a void node where a character is located")

        # Verify occupied void node was preserved
        async with self.db.async_session() as session:
            occupied_check = (await session.execute(select(GridNode).where(GridNode.id == occupied_id))).scalars().first()
            self.assertEqual(occupied_check.name, occupied_name)
            self.assertEqual(occupied_check.region_type, "VOD")
            self.assertIsNone(occupied_check.net_affinity)

    async def test_expand_grid_no_coordinate_holes_after_outlying_node(self):
        """Verify that expanding the grid produces zero coordinate gaps/holes even if an outlying node exists."""
        await self.db.generate_master_grid(width=50, height=50)

        # Manually create an outlying node at (50, 0) simulating edge placement
        async with self.db.async_session() as session:
            outlier = GridNode(
                name="Outlier_50_0",
                description="Outlying edge node",
                x=50,
                y=0,
                node_type="void",
                region_type="VOD",
                durability=100.0,
                addons_json="{}"
            )
            session.add(outlier)
            await session.commit()

        # Expand grid by +10x10
        success, msg, data = await self.db.expansion.expand_grid(10, 10)
        self.assertTrue(success)

        # Audit that EVERY coordinate in (0..new_w-1, 0..new_h-1) is present
        new_w, new_h = data["new_dimensions"]
        async with self.db.async_session() as session:
            all_coords = set(
                (row[0], row[1]) for row in (await session.execute(
                    select(GridNode.x, GridNode.y).where(GridNode.x != None, GridNode.y != None)
                )).all()
            )
            for gx in range(new_w):
                for gy in range(new_h):
                    self.assertIn((gx, gy), all_coords, f"Missing coordinate ({gx}, {gy}) in expanded grid")

            # Check no duplicate coordinates
            total_nodes = (await session.execute(select(func.count(GridNode.id)))).scalar()
            self.assertEqual(len(all_coords), total_nodes, "Duplicate coordinates detected in expanded grid")

    async def test_admin_map_help_subverb(self):
        """Verify admin map help displays syntax rather than node lookup failure."""
        await self.db.generate_master_grid(width=50, height=50)

        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "help"], reply_target="#arena")
        msg = " ".join(self.mock_irc.sent_messages)
        self.assertIn("Syntax: admin map", msg)
        self.assertNotIn("Node or coordinates not found", msg)

        # Test shorthand '?'
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "?"], reply_target="#arena")
        msg2 = " ".join(self.mock_irc.sent_messages)
        self.assertIn("Syntax: admin map", msg2)

    async def test_admin_map_expand_upper_bound_rejection(self):
        """Verify admin map expand rejects extreme deltas to prevent memory exhaustion."""
        await self.db.generate_master_grid(width=50, height=50)

        success, msg, _ = await self.db.expansion.expand_grid(100, 100)
        self.assertFalse(success)
        self.assertIn("exceeds maximum permitted limit", msg)

        # Via admin command handler
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["map", "expand", "80", "80"], reply_target="#arena")
        msg_irc = " ".join(self.mock_irc.sent_messages)
        self.assertIn("exceeds maximum permitted limit", msg_irc)

    async def test_arenadb_get_grid_stats_direct_delegation(self):
        """Verify calling db.get_grid_stats directly returns the expansion dictionary."""
        await self.db.generate_master_grid(width=50, height=50)

        stats = await self.db.get_grid_stats()
        self.assertIsInstance(stats, dict)
        self.assertEqual(stats["width"], 50)
        self.assertEqual(stats["height"], 50)
        self.assertEqual(stats["total_nodes"], 2500)
        self.assertEqual(stats["active_nodes"], 700)
        self.assertEqual(stats["void_nodes"], 1800)

    async def test_handle_grid_map_stats_formatting(self):
        """Verify !a grid map stats returns clean formatted output whether dict or string is returned."""
        from ai_grid.core.handlers.grid import handle_grid_map

        await self.db.generate_master_grid(width=50, height=50)

        # Create a character
        async with self.db.async_session() as session:
            player = Player(global_name="Mapper", is_autonomous=False)
            session.add(player)
            await session.flush()
            session.add(NetworkAlias(player_id=player.id, network_name="testnet", nickname="Mapper"))
            uplink = (await session.execute(select(GridNode).where(GridNode.name == "UpLink"))).scalars().first()
            char = Character(player_id=player.id, name="Mapper", race="Synth", char_class="NetRunner", node_id=uplink.id)
            session.add(char)
            await session.commit()

        self.mock_irc.sent_messages.clear()
        await handle_grid_map(self.mock_irc, "Mapper", "#arena", ["stats"])
        msg = " ".join(self.mock_irc.sent_messages)
        self.assertIn("GRID_STATS", msg)
        self.assertIn("Dim: 50x50", msg)
        self.assertIn("Total_Nodes: 2,500", msg)
        self.assertIn("Active: 700", msg)
    async def test_admin_network_list_and_detail_view(self):
        """Verify !a admin network list and !a admin network <name> detail views."""
        from unittest.mock import MagicMock
        
        # Configure mock hub with nodes
        mock_hub = MagicMock()
        mock_node_2600 = MagicMock()
        mock_node_2600.net_name = "2600net"
        mock_node_2600.config = {"nickname": "GridManager", "channel": "#automatagrid"}
        mock_node_2600.nickserv_verified = {"gridmanager"}
        mock_node_2600.last_server_ping = 42.0
        mock_node_2600.channel_users = {"alice": {}, "bob": {}}
        mock_node_2600.network_name = "2600net"
        mock_node_2600.irc = MagicMock()
        mock_node_2600.irc.is_connected.return_value = True
        mock_node_2600.irc.nickname = "GridManager"
        mock_node_2600.irc.channel = "#automatagrid"

        mock_hub.nodes = {"2600net": mock_node_2600}
        self.mock_irc.hub = mock_hub
        self.mock_irc.last_server_ping = 15.0

        # Test list view: !a admin network
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["network"], reply_target="#arena")
        list_output = " ".join(self.mock_irc.sent_messages)
        self.assertIn("IRC NETWORKS INFRASTRUCTURE", list_output)
        self.assertIn("2600NET", list_output)
        self.assertIn("ONLINE", list_output)
        self.assertIn("GridManager", list_output)
        self.assertIn("+r (IDENTIFIED)", list_output)
        self.assertIn("42ms", list_output)

        # Test detail view: !a admin network 2600net
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["network", "2600net"], reply_target="#arena")
        detail_output = " ".join(self.mock_irc.sent_messages)
        self.assertIn("NETWORK DETAIL: 2600NET", detail_output)
        self.assertIn("ONLINE", detail_output)
        self.assertIn("GridManager", detail_output)
        self.assertIn("+r (IDENTIFIED)", detail_output)
        self.assertIn("#automatagrid", detail_output)
        self.assertIn("42ms", detail_output)

        # Test unknown network
        self.mock_irc.sent_messages.clear()
        await handle_admin_command(self.mock_irc, "AdminSys", "admin", ["network", "unknownnet"], reply_target="#arena")
        err_output = " ".join(self.mock_irc.sent_messages)
        self.assertIn("Network \"unknownnet\" not found", err_output)


if __name__ == "__main__":
    unittest.main()
