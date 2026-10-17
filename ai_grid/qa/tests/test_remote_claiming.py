import asyncio
import datetime
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_grid.models import (
    Base,
    Character,
    Player,
    NetworkAlias,
    GridNode,
    BreachRecord,
    DiscoveryRecord,
    Memo,
    ItemTemplate,
    InventoryItem,
)
from ai_grid.database.repositories.remote_net_repo import RemoteNetRepository, get_net_device_state
from ai_grid.grid_db import ArenaDB
import ai_grid.core.handlers as handlers
from ai_grid.core.command_router import CommandRouter


class TestRemoteClaimingAndAdmin(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # In-memory SQLite with StaticPool for sub-second execution without raw sockets
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=False,
        )
        self.async_session = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.repo = RemoteNetRepository(self.async_session)

        # Seed initial state
        async with self.async_session() as session:
            # Add item templates
            session.add(ItemTemplate(name="NET", item_type="node_addon", base_value=1000, effects_json='{"type": "NET"}'))
            session.add(ItemTemplate(name="IDS", item_type="node_addon", base_value=500, effects_json='{"type": "IDS"}'))
            session.add(ItemTemplate(name="FIREWALL", item_type="node_addon", base_value=500, effects_json='{"type": "FIREWALL"}'))

            # Attacker on 2600net
            att_player = Player(global_name="Attacker", is_autonomous=False)
            session.add(att_player)
            await session.flush()
            session.add(NetworkAlias(player_id=att_player.id, network_name="2600net", nickname="Attacker"))

            self.att_char = Character(
                player_id=att_player.id,
                name="Attacker",
                race="Cyborg",
                char_class="Netrunner",
                credits=2000.0,
                power=200.0,
                data_units=0.0,
                level=1,
                xp=0,
            )
            session.add(self.att_char)
            await session.flush()

            # Attacker local operating node on 2600net with active NET device pointed at Rizon
            self.att_node = GridNode(
                name="AttackerCore",
                node_type="safezone",
                owner_character_id=self.att_char.id,
                net_affinity="Rizon",
                addons_json=json.dumps({"NET": True, "NET_STATE": "OPEN"}),
                availability_mode="OPEN",
                power_stored=500.0,
                durability=100.0,
            )
            session.add(self.att_node)
            await session.flush()
            self.att_char.node_id = self.att_node.id

            # Defender on Rizon
            def_player = Player(global_name="Defender", is_autonomous=False)
            session.add(def_player)
            await session.flush()
            session.add(NetworkAlias(player_id=def_player.id, network_name="Rizon", nickname="Defender"))

            self.def_char = Character(
                player_id=def_player.id,
                name="Defender",
                race="Ghost",
                char_class="Sentinel",
                credits=1000.0,
                power=200.0,
            )
            session.add(self.def_char)
            await session.flush()

            # Target Nodes on Rizon
            # 1. Unowned OPEN node
            self.node_open = GridNode(
                name="Rizon_Open_Unowned",
                node_type="void",
                owner_character_id=None,
                net_affinity="Rizon",
                addons_json=json.dumps({"NET": True, "NET_STATE": "OPEN"}),
                availability_mode="OPEN",
                power_stored=100.0,
                durability=100.0,
                upgrade_level=1,
            )
            session.add(self.node_open)

            # 2. Unowned CLOSED node
            self.node_closed = GridNode(
                name="Rizon_Closed_Unowned",
                node_type="void",
                owner_character_id=None,
                net_affinity="Rizon",
                addons_json=json.dumps({"NET": True, "NET_STATE": "CLOSED"}),
                availability_mode="CLOSED",
                power_stored=100.0,
                durability=100.0,
                upgrade_level=1,
            )
            session.add(self.node_closed)

            # 3. Rival Owned node (owned by Defender)
            self.node_rival = GridNode(
                name="Rizon_Rival_Owned",
                node_type="void",
                owner_character_id=self.def_char.id,
                net_affinity="Rizon",
                addons_json=json.dumps({"NET": True, "NET_STATE": "OPEN"}),
                availability_mode="OPEN",
                power_stored=200.0,
                durability=100.0,
                upgrade_level=1,
            )
            session.add(self.node_rival)

            # 4. Node already owned by Attacker on Rizon (for management testing)
            self.node_owned = GridNode(
                name="Rizon_Owned_Outpost",
                node_type="void",
                owner_character_id=self.att_char.id,
                net_affinity="Rizon",
                addons_json=json.dumps({"NET": True, "NET_STATE": "OPEN", "data_stored": 30.0}),
                availability_mode="OPEN",
                power_stored=150.0,
                durability=60.0,
                upgrade_level=1,
                stash_inventory=["MicroPatch", "CipherKey"],
            )
            session.add(self.node_owned)

            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    # =========================================================================
    # R1 & Acceptance Criteria: Remote Claiming Tests
    # =========================================================================

    async def test_claim_unowned_open_node_success(self):
        """Claiming an unowned remote OPEN node via net <network> claim <target> succeeds."""
        res = await self.repo.remote_claim_node("Attacker", "2600net", "Rizon", "Rizon_Open_Unowned")
        self.assertTrue(res["success"], f"Expected claim success: {res.get('msg')}")
        self.assertIn("Control established", res["msg"])
        self.assertIsNone(res.get("alert_data"))

        # Verify database state
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Open_Unowned"))).scalars().first()
            char = (await session.execute(select(Character).where(Character.name == "Attacker"))).scalars().first()
            self.assertEqual(node.owner_character_id, char.id)
            self.assertEqual(node.power_stored, 100.0)
            self.assertEqual(node.durability, 100.0)
            self.assertGreater(char.xp, 0)
            tasks = json.loads(char.daily_tasks or "{}")
            self.assertEqual(tasks.get("Claim a Node"), 1)

    async def test_claim_unowned_closed_node_unbreached_rejected(self):
        """Claiming a remote CLOSED node requires an active breach record; unbreached attempts fail."""
        res = await self.repo.remote_claim_node("Attacker", "2600net", "Rizon", "Rizon_Closed_Unowned")
        self.assertFalse(res["success"])
        self.assertIn("requires an active breach", res["msg"].lower())

        # Verify ownership not changed
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Closed_Unowned"))).scalars().first()
            self.assertIsNone(node.owner_character_id)

    async def test_claim_unowned_closed_node_with_active_breach_success(self):
        """Claiming a remote CLOSED node succeeds when an active breach window exists and dispatches alert."""
        # Create active breach record
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Closed_Unowned"))).scalars().first()
            char = (await session.execute(select(Character).where(Character.name == "Attacker"))).scalars().first()
            session.add(BreachRecord(
                character_id=char.id,
                node_id=node.id,
                breached_at=datetime.datetime.now(datetime.timezone.utc),
                is_silent=False,
            ))
            await session.commit()

        res = await self.repo.remote_claim_node("Attacker", "2600net", "Rizon", "Rizon_Closed_Unowned")
        self.assertTrue(res["success"], f"Breached claim failed: {res.get('msg')}")
        self.assertIn("Control established", res["msg"])
        # Contested node triggers cross-network alert (R3)
        self.assertIsNotNone(res.get("alert_data"))
        self.assertEqual(res["alert_data"]["target_network"], "Rizon")
        self.assertIn("Remote Node Seizure", res["alert_data"]["alert_msg"])

        # Verify DB ownership
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Closed_Unowned"))).scalars().first()
            self.assertEqual(node.owner_character_id, self.att_char.id)

    async def test_claim_unowned_closed_node_with_expired_breach_rejected(self):
        """Expired breach (>300s old) is rejected."""
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Closed_Unowned"))).scalars().first()
            char = (await session.execute(select(Character).where(Character.name == "Attacker"))).scalars().first()
            old_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=400)
            session.add(BreachRecord(
                character_id=char.id,
                node_id=node.id,
                breached_at=old_time,
                is_silent=False,
            ))
            await session.commit()

        res = await self.repo.remote_claim_node("Attacker", "2600net", "Rizon", "Rizon_Closed_Unowned")
        self.assertFalse(res["success"])
        self.assertIn("requires an active breach", res["msg"].lower())

    async def test_claim_without_net_device_rejected(self):
        """Attempting to claim without an active NET device fails."""
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "AttackerCore"))).scalars().first()
            node.addons_json = json.dumps({"AMP": True})
            await session.commit()

        res = await self.repo.remote_claim_node("Attacker", "2600net", "Rizon", "Rizon_Open_Unowned")
        self.assertFalse(res["success"])
        self.assertIn("NET device required", res["msg"])

    async def test_claim_with_mismatched_affinity_rejected(self):
        """Attempting to claim with NET affinity pointed elsewhere fails."""
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "AttackerCore"))).scalars().first()
            node.net_affinity = "Undernet"
            await session.commit()

        res = await self.repo.remote_claim_node("Attacker", "2600net", "Rizon", "Rizon_Open_Unowned")
        self.assertFalse(res["success"])
        self.assertIn("Alignment Error", res["msg"])

    async def test_claim_from_unowned_local_node_rejected(self):
        """Attempting to claim from a local node not owned by caller fails."""
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "AttackerCore"))).scalars().first()
            node.owner_character_id = None
            await session.commit()

        res = await self.repo.remote_claim_node("Attacker", "2600net", "Rizon", "Rizon_Open_Unowned")
        self.assertFalse(res["success"])
        self.assertIn("Access Denied", res["msg"])

    async def test_claim_rival_owned_node_rejected(self):
        """Attempting to claim a node owned by another player is rejected."""
        res = await self.repo.remote_claim_node("Attacker", "2600net", "Rizon", "Rizon_Rival_Owned")
        self.assertFalse(res["success"])
        self.assertIn("controlled by a rival", res["msg"].lower())

    async def test_claim_already_owned_node_rejected(self):
        """Attempting to claim a node already owned by caller returns commanded notice."""
        res = await self.repo.remote_claim_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost")
        self.assertFalse(res["success"])
        self.assertIn("already command this node", res["msg"].lower())

    async def test_claim_local_node_conflict_rejected(self):
        """Cannot target local operating node via remote NET claim."""
        res = await self.repo.remote_claim_node("Attacker", "2600net", "Rizon", "AttackerCore")
        self.assertFalse(res["success"])

    # =========================================================================
    # R1 & Acceptance Criteria: Remote Administration (remote_manage_node)
    # =========================================================================

    async def test_remote_manage_non_owner_rejected(self):
        """Managing a node not owned by caller is rejected."""
        res = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Rival_Owned", "status")
        self.assertFalse(res["success"])
        self.assertIn("Permission Denied", res["msg"])

    async def test_remote_manage_upgrade_success(self):
        """net <network> upgrade <target> upgrades the remote node with +20% remote fee."""
        res = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "upgrade")
        self.assertTrue(res["success"], f"Upgrade failed: {res.get('msg')}")
        self.assertEqual(res["level"], 2)
        # Base cost 1 * 500 = 500, +20% = 600c
        self.assertEqual(res["cost"], 600)
        self.assertIn("upgraded to Level 2", res["msg"])

        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Owned_Outpost"))).scalars().first()
            char = (await session.execute(select(Character).where(Character.name == "Attacker"))).scalars().first()
            self.assertEqual(node.upgrade_level, 2)
            self.assertEqual(node.durability, 100.0)
            self.assertEqual(char.credits, 2000.0 - 600)

    async def test_remote_manage_upgrade_insufficient_credits(self):
        """Upgrade fails if caller lacks required credits."""
        async with self.async_session() as session:
            char = (await session.execute(select(Character).where(Character.name == "Attacker"))).scalars().first()
            char.credits = 100.0
            await session.commit()

        res = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "upgrade")
        self.assertFalse(res["success"])
        self.assertIn("Insufficient credits", res["msg"])

    async def test_remote_manage_upgrade_max_level(self):
        """Upgrade fails if node reached max level (4)."""
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Owned_Outpost"))).scalars().first()
            node.upgrade_level = 4
            await session.commit()

        res = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "upgrade")
        self.assertFalse(res["success"])
        self.assertIn("Architecture peak reached", res["msg"])

    async def test_remote_manage_repair_success(self):
        """net <network> repair <target> repairs remote node durability using power."""
        # Initial durability is 60.0, power is 200.0
        res = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "repair")
        self.assertTrue(res["success"], f"Repair failed: {res.get('msg')}")
        self.assertIn("integrity to 80.0%", res["msg"])
        self.assertEqual(res["durability"], 80.0)

        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Owned_Outpost"))).scalars().first()
            char = (await session.execute(select(Character).where(Character.name == "Attacker"))).scalars().first()
            self.assertEqual(node.durability, 80.0)
            self.assertEqual(char.power, 200.0 - 25.0) # 25 uP cost
            tasks = json.loads(char.daily_tasks or "{}")
            self.assertEqual(tasks.get("Repair a Node"), 1)

    async def test_remote_manage_repair_insufficient_power(self):
        """Repair fails if caller lacks required power (25 uP)."""
        async with self.async_session() as session:
            char = (await session.execute(select(Character).where(Character.name == "Attacker"))).scalars().first()
            char.power = 10.0
            await session.commit()

        res = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "repair")
        self.assertFalse(res["success"])
        self.assertIn("Insufficient POWER", res["msg"])

    async def test_remote_manage_status(self):
        """net <network> status <target> returns remote node telemetry."""
        res = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "status")
        self.assertTrue(res["success"])
        self.assertEqual(res["name"], "Rizon_Owned_Outpost")
        self.assertEqual(res["level"], 1)
        self.assertEqual(res["durability"], 60.0)
        self.assertEqual(res["power"], 150.0)
        self.assertEqual(res["stash_count"], 2)
        self.assertEqual(res["net_state"], "OPEN")
        self.assertIn("REMOTE TELEMETRY", res["msg"])

    async def test_remote_manage_stash(self):
        """net <network> stash <target> displays remote stash contents to owner."""
        res = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "stash")
        self.assertTrue(res["success"])
        self.assertEqual(res["count"], 2)
        self.assertIn("MicroPatch", res["items"])
        self.assertIn("CipherKey", res["items"])
        self.assertIn("Remote Rizon_Owned_Outpost stash", res["msg"])

    async def test_remote_manage_siphon_all(self):
        """net <network> siphon <target> transfers power and data fragments with -10% dampening."""
        # node_owned has 150.0 uP and 30.0 Data
        # Attacker has 200.0 uP and 0.0 Data
        res = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "siphon")
        self.assertTrue(res["success"], f"Siphon failed: {res.get('msg')}")
        # 150 * 0.90 = 135.0 uP, 30 * 0.90 = 27.0 Data
        self.assertAlmostEqual(res["power_yield"], 135.0, places=1)
        self.assertAlmostEqual(res["data_yield"], 27.0, places=1)
        self.assertIn("135.0 uP and 27.0 Data", res["msg"])

        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Owned_Outpost"))).scalars().first()
            char = (await session.execute(select(Character).where(Character.name == "Attacker"))).scalars().first()
            self.assertEqual(node.power_stored, 0.0)
            addons = json.loads(node.addons_json or "{}")
            self.assertEqual(addons.get("data_stored"), 0.0)
            self.assertEqual(char.power, 200.0 + 135.0)
            self.assertEqual(char.data_units, 27.0)

    async def test_remote_manage_siphon_selective_power_percentage(self):
        """net <network> siphon <target> power 50 transfers 50% power only."""
        res = await self.repo.remote_manage_node(
            "Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "siphon", args=["power", "50%"]
        )
        self.assertTrue(res["success"])
        # 150 * 0.50 = 75.0 -> -10% dampening = 67.5 uP
        self.assertAlmostEqual(res["power_yield"], 67.5, places=1)
        self.assertEqual(res["data_yield"], 0.0)

        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Owned_Outpost"))).scalars().first()
            self.assertEqual(node.power_stored, 75.0)
            addons = json.loads(node.addons_json or "{}")
            self.assertEqual(addons.get("data_stored"), 30.0) # untouched

    async def test_remote_manage_siphon_selective_data(self):
        """net <network> siphon <target> data transfers data only."""
        res = await self.repo.remote_manage_node(
            "Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "siphon", args=["data", "100"]
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["power_yield"], 0.0)
        self.assertAlmostEqual(res["data_yield"], 27.0, places=1)

    async def test_remote_manage_siphon_depleted(self):
        """Siphoning depleted node returns appropriate message."""
        # Deplete reserves
        await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "siphon")
        # Second attempt
        res = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "siphon")
        self.assertFalse(res["success"])
        self.assertIn("No available all reserves", res["msg"])

    async def test_remote_manage_ids_trigger(self):
        """Remote administration triggers IDS notice if target node has IDS installed."""
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Owned_Outpost"))).scalars().first()
            addons = json.loads(node.addons_json or "{}")
            addons["IDS"] = True
            node.addons_json = json.dumps(addons)
            await session.commit()

        res = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "status")
        self.assertTrue(res["success"])
        self.assertIsNotNone(res.get("alert_data"))
        self.assertEqual(res["alert_data"]["target_network"], "Rizon")
        self.assertIn("Remote Administration (STATUS) detected", res["alert_data"]["alert_msg"])

    # =========================================================================
    # Facade Delegates in ArenaDB and GridFacade
    # =========================================================================

    async def test_grid_db_facade_delegates(self):
        """Verify ArenaDB and GridFacade properly proxy remote_claim_node and remote_manage_node."""
        db = ArenaDB(":memory:")
        # Wire repositories to use our initialized session
        db.remote_net = self.repo
        self.assertTrue(hasattr(db, "remote_claim_node"))
        self.assertTrue(hasattr(db, "remote_manage_node"))
        self.assertTrue(hasattr(db.grid, "remote_claim_node"))
        self.assertTrue(hasattr(db.grid, "remote_manage_node"))

        # Test direct call through db facade
        res_claim = await db.remote_claim_node("Attacker", "2600net", "Rizon", "Rizon_Open_Unowned")
        self.assertTrue(res_claim["success"])

        res_manage = await db.grid.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "status")
        self.assertTrue(res_manage["success"])

    # =========================================================================
    # IRC Interface & Command Routing (handle_remote_net_command)
    # =========================================================================

    async def test_irc_handler_claim_flow(self):
        """Test handle_remote_net_command claim dispatch and response formatting."""
        mock_node = MagicMock()
        mock_node.net_name = "2600net"
        mock_node.send = AsyncMock()
        mock_node.add_xp = MagicMock(return_value=asyncio.sleep(0))
        mock_node.db = MagicMock()
        mock_node.db.async_session = self.async_session
        mock_node.db.remote_claim_node = self.repo.remote_claim_node

        await handlers.remote_net.handle_remote_net_command(
            mock_node, "Attacker", "#2600net", "Rizon", ["claim", "Rizon_Open_Unowned"]
        )

        mock_node.send.assert_called_once()
        sent_line = mock_node.send.call_args[0][0]
        self.assertIn("NET_CLAIM", sent_line)
        self.assertIn("Control established", sent_line)
        mock_node.add_xp.assert_called_once_with("Attacker", 50, "#2600net")

    async def test_irc_handler_manage_upgrade(self):
        """Test handle_remote_net_command upgrade dispatch."""
        mock_node = MagicMock()
        mock_node.net_name = "2600net"
        mock_node.send = AsyncMock()
        mock_node.add_xp = MagicMock(return_value=asyncio.sleep(0))
        mock_node.db = MagicMock()
        mock_node.db.async_session = self.async_session
        mock_node.db.remote_manage_node = self.repo.remote_manage_node

        await handlers.remote_net.handle_remote_net_command(
            mock_node, "Attacker", "#2600net", "Rizon", ["upgrade", "Rizon_Owned_Outpost"]
        )

        mock_node.send.assert_called_once()
        sent_line = mock_node.send.call_args[0][0]
        self.assertIn("NET_UPGRADE", sent_line)
        self.assertIn("upgraded to Level 2", sent_line)

    async def test_irc_handler_manage_siphon_owned_branch(self):
        """Test handle_remote_net_command branches to remote_manage_node for owned node."""
        mock_node = MagicMock()
        mock_node.net_name = "2600net"
        mock_node.send = AsyncMock()
        mock_node.add_xp = MagicMock(return_value=asyncio.sleep(0))
        mock_node.db = MagicMock()
        mock_node.db.async_session = self.async_session
        mock_node.db.remote_manage_node = self.repo.remote_manage_node
        mock_node.db.remote_net = self.repo
        mock_node.db.is_node_owner = self.repo.is_node_owner
        mock_node.db.get_character_by_nick = AsyncMock(return_value=self.att_char)

        await handlers.remote_net.handle_remote_net_command(
            mock_node, "Attacker", "#2600net", "Rizon", ["siphon", "Rizon_Owned_Outpost", "power", "50%"]
        )

        mock_node.send.assert_called_once()
        sent_line = mock_node.send.call_args[0][0]
        self.assertIn("NET_SIPHON", sent_line)
        self.assertIn("Transferred", sent_line)

    async def test_command_router_raid_claim_alias(self):
        """Test !a raid <network> claim <target>, !a net <network> claim <target>, and !a grid net <network> claim <target>."""
        mock_node = MagicMock()
        mock_node.prefix = "!a"
        mock_node.config = {"nickname": "bot"}
        mock_node.net_name = "2600net"
        mock_node.send = AsyncMock()
        mock_node.active_engine = None
        mock_node.db = MagicMock()
        mock_node.db.async_session = self.async_session

        router = CommandRouter(mock_node)

        with patch("ai_grid.core.handlers.handle_remote_net_command", new_callable=AsyncMock) as mock_remote, \
             patch("ai_grid.core.handlers.remote_net.handle_remote_net_command", mock_remote):
            # 1. Alias via raid: !a raid Rizon claim Rizon_Open_Unowned
            await router.dispatch("Attacker", "PRIVMSG", "#2600net", "!a raid Rizon claim Rizon_Open_Unowned", is_admin=False)
            await asyncio.sleep(0.01)
            mock_remote.assert_called_with(
                mock_node, "Attacker", "#2600net", "Rizon", ["claim", "Rizon_Open_Unowned"]
            )

            # 2. Direct via net: !a net Rizon claim Rizon_Open_Unowned
            mock_remote.reset_mock()
            await router.dispatch("Attacker", "PRIVMSG", "#2600net", "!a net Rizon claim Rizon_Open_Unowned", is_admin=False)
            await asyncio.sleep(0.01)
            mock_remote.assert_called_with(
                mock_node, "Attacker", "#2600net", "Rizon", ["claim", "Rizon_Open_Unowned"]
            )

            # 3. Aliased via grid net: !a grid net Rizon claim Rizon_Open_Unowned
            mock_remote.reset_mock()
            await router.dispatch("Attacker", "PRIVMSG", "#2600net", "!a grid net Rizon claim Rizon_Open_Unowned", is_admin=False)
            await asyncio.sleep(0.01)
            mock_remote.assert_called_with(
                mock_node, "Attacker", "#2600net", "Rizon", ["claim", "Rizon_Open_Unowned"]
            )


    # =========================================================================
    # Edge Cases & Robustness Tests
    # =========================================================================

    async def test_claim_target_none_or_empty_rejected(self):
        """remote_claim_node rejects None or empty target_node/target_network gracefully."""
        res1 = await self.repo.remote_claim_node("Attacker", "2600net", "Rizon", "")
        self.assertFalse(res1["success"])
        self.assertIn("required", res1["msg"].lower())

        res2 = await self.repo.remote_claim_node("Attacker", "2600net", "", "Rizon_Open_Unowned")
        self.assertFalse(res2["success"])
        self.assertIn("required", res2["msg"].lower())

        res3 = await self.repo.remote_claim_node("Attacker", "2600net", "Rizon", None)
        self.assertFalse(res3["success"])
        self.assertIn("required", res3["msg"].lower())

    async def test_manage_target_none_or_empty_or_invalid_action(self):
        """remote_manage_node rejects empty targets and invalid actions gracefully."""
        res1 = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "", "status")
        self.assertFalse(res1["success"])
        self.assertIn("required", res1["msg"].lower())

        res2 = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "")
        self.assertFalse(res2["success"])
        self.assertIn("required", res2["msg"].lower())

        res3 = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "defenestrate")
        self.assertFalse(res3["success"])
        self.assertIn("Invalid remote management action", res3["msg"])

    async def test_claim_atomic_concurrency_race(self):
        """Two concurrent claims on the same unowned node allow only one winner and reject the rival."""
        # Create second attacker on 2600net
        async with self.async_session() as session:
            p2 = Player(global_name="Attacker2", is_autonomous=False)
            session.add(p2)
            await session.flush()
            session.add(NetworkAlias(player_id=p2.id, network_name="2600net", nickname="Attacker2"))
            c2 = Character(
                player_id=p2.id,
                name="Attacker2",
                race="Cyborg",
                char_class="Netrunner",
                credits=2000.0,
                power=200.0,
            )
            session.add(c2)
            await session.flush()

            # Second attacker core node
            node2 = GridNode(
                name="Attacker2Core",
                node_type="safezone",
                owner_character_id=c2.id,
                net_affinity="Rizon",
                addons_json=json.dumps({"NET": True, "NET_STATE": "OPEN"}),
                availability_mode="OPEN",
                power_stored=500.0,
                durability=100.0,
            )
            session.add(node2)
            await session.flush()
            c2.node_id = node2.id
            await session.commit()

        # Run concurrent claims on Rizon_Open_Unowned
        res1, res2 = await asyncio.gather(
            self.repo.remote_claim_node("Attacker", "2600net", "Rizon", "Rizon_Open_Unowned"),
            self.repo.remote_claim_node("Attacker2", "2600net", "Rizon", "Rizon_Open_Unowned"),
        )

        successes = [r for r in [res1, res2] if r["success"]]
        failures = [r for r in [res1, res2] if not r["success"]]

        self.assertEqual(len(successes), 1, "Exactly one concurrent claim must succeed")
        self.assertEqual(len(failures), 1, "Exactly one concurrent claim must fail")
        self.assertIn("rival", failures[0]["msg"].lower())

    async def test_manage_ids_alert_persisted_on_failure(self):
        """IDS alert and Memo are committed to the database even when admin action returns an error."""
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Owned_Outpost"))).scalars().first()
            addons = json.loads(node.addons_json or "{}")
            addons["IDS"] = True
            node.addons_json = json.dumps(addons)
            node.ids_alerts = 0
            # Set credits too low for upgrade
            char = (await session.execute(select(Character).where(Character.name == "Attacker"))).scalars().first()
            char.credits = 10.0
            await session.commit()

        res = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "upgrade")
        self.assertFalse(res["success"])
        self.assertIsNotNone(res.get("alert_data"))

        # Verify IDS alert and Memo were persisted in DB despite the failure
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Owned_Outpost"))).scalars().first()
            memos = (await session.execute(select(Memo).where(Memo.source_node_id == node.id))).scalars().all()
            self.assertEqual(node.ids_alerts, 1)
            self.assertGreaterEqual(len(memos), 1)

    async def test_manage_repair_with_credits(self):
        """Remote repair supports credits when args contains 'credits'."""
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Owned_Outpost"))).scalars().first()
            node.durability = 50.0
            char = (await session.execute(select(Character).where(Character.name == "Attacker"))).scalars().first()
            char.credits = 500.0
            char.power = 0.0
            await session.commit()

        res = await self.repo.remote_manage_node(
            "Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "repair", args=["credits"]
        )
        self.assertTrue(res["success"], f"Credit repair failed: {res.get('msg')}")
        self.assertEqual(res["durability"], 70.0)

        async with self.async_session() as session:
            char = (await session.execute(select(Character).where(Character.name == "Attacker"))).scalars().first()
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Owned_Outpost"))).scalars().first()
            self.assertEqual(char.credits, 500.0 - 120.0)
            self.assertEqual(node.durability, 70.0)

    async def test_manage_repair_insufficient_credits(self):
        """Remote repair with credits fails when caller has insufficient credits."""
        async with self.async_session() as session:
            char = (await session.execute(select(Character).where(Character.name == "Attacker"))).scalars().first()
            char.credits = 50.0
            await session.commit()

        res = await self.repo.remote_manage_node(
            "Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "repair", args=["credits"]
        )
        self.assertFalse(res["success"])
        self.assertIn("Insufficient credits", res["msg"])

    async def test_manage_siphon_corrupted_addons_json(self):
        """Siphon and status handle malformed / corrupted addons_json gracefully."""
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Owned_Outpost"))).scalars().first()
            node.addons_json = "corrupted{not-valid-json"
            node.power_stored = 50.0
            await session.commit()

        # Should not raise JSONDecodeError
        res_status = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "status")
        self.assertTrue(res_status["success"])
        self.assertGreater(res_status["data"], 0.0)

        res_siphon = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "siphon")
        self.assertTrue(res_siphon["success"])

    async def test_is_node_owner_repository_method(self):
        """Verify is_node_owner correctly identifies owned, rival, and unowned nodes."""
        is_owned = await self.repo.is_node_owner("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost")
        self.assertTrue(is_owned)

        is_rival = await self.repo.is_node_owner("Attacker", "2600net", "Rizon", "Rizon_Rival_Owned")
        self.assertFalse(is_rival)

        is_unowned = await self.repo.is_node_owner("Attacker", "2600net", "Rizon", "Rizon_Open_Unowned")
        self.assertFalse(is_unowned)

        is_invalid = await self.repo.is_node_owner("Attacker", "2600net", "Rizon", "NonexistentNode")
        self.assertFalse(is_invalid)

    async def test_claim_resets_contested_counters(self):
        """Claiming a previously contested node resets ids_alerts and firewall_hits to 0."""
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Open_Unowned"))).scalars().first()
            node.ids_alerts = 5
            node.firewall_hits = 3
            node.durability = 45.0
            await session.commit()

        res = await self.repo.remote_claim_node("Attacker", "2600net", "Rizon", "Rizon_Open_Unowned")
        self.assertTrue(res["success"])
        self.assertIsNotNone(res.get("alert_data"))

        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Open_Unowned"))).scalars().first()
            self.assertEqual(node.ids_alerts, 0)
            self.assertEqual(node.firewall_hits, 0)
            self.assertEqual(node.durability, 100.0)

    async def test_manage_stash_and_status_with_json_string_inventory(self):
        """Resiliently parse stash_inventory when stored as a serialized JSON string or empty list."""
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Owned_Outpost"))).scalars().first()
            node.stash_inventory = '["MicroPatch", "CipherKey"]'
            await session.commit()

        # Test status parses string stash_inventory count without character iteration
        res_status = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "status")
        self.assertTrue(res_status["success"])
        self.assertEqual(res_status["stash_count"], 2)

        # Test stash parses items correctly
        res_stash = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "stash")
        self.assertTrue(res_stash["success"])
        self.assertEqual(res_stash["count"], 2)
        self.assertEqual(res_stash["items"], ["MicroPatch", "CipherKey"])
        self.assertIn("MicroPatch, CipherKey", res_stash["msg"])

        # Test empty stash returns empty notice
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Owned_Outpost"))).scalars().first()
            node.stash_inventory = []
            await session.commit()

        res_empty = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "stash")
        self.assertTrue(res_empty["success"])
        self.assertEqual(res_empty["count"], 0)
        self.assertIn("stash is empty", res_empty["msg"])

    async def test_claim_remote_node_equips_net_device_for_reconfiguration(self):
        """Claiming an unowned remote node installs NET device in addons_json so the owner can configure states."""
        async with self.async_session() as session:
            node_plain = GridNode(
                name="Rizon_Plain_Node",
                node_type="void",
                owner_character_id=None,
                net_affinity="Rizon",
                addons_json="{}",
                availability_mode="OPEN",
                power_stored=50.0,
                durability=100.0,
            )
            session.add(node_plain)
            await session.commit()

        res = await self.repo.remote_claim_node("Attacker", "2600net", "Rizon", "Rizon_Plain_Node")
        self.assertTrue(res["success"])

        # Verify NET is installed in addons
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(GridNode.name == "Rizon_Plain_Node"))).scalars().first()
            addons = json.loads(node.addons_json or "{}")
            self.assertTrue(addons.get("NET"))

        # Verify new owner can reconfigure its access state via set_net_device_state
        ok, state_msg = await self.repo.set_net_device_state("Attacker", "2600net", "CLOSED", node_name="Rizon_Plain_Node")
        self.assertTrue(ok, f"Failed to reconfigure state: {state_msg}")
        self.assertIn("access state set to CLOSED", state_msg)

    async def test_remote_manage_unowned_node_rejected(self):
        """Managing an unowned remote node is rejected with a claim directive."""
        res = await self.repo.remote_manage_node("Attacker", "2600net", "Rizon", "Rizon_Open_Unowned", "status")
        self.assertFalse(res["success"])
        self.assertIn("is unowned. Claim it first.", res["msg"])

    async def test_command_router_all_raid_admin_aliases(self):
        """Test !a raid <network> <upgrade|repair|status|stash|siphon> routes through handle_grid_loot to handle_remote_net_command."""
        mock_node = MagicMock()
        mock_node.prefix = "!a"
        mock_node.config = {"nickname": "bot"}
        mock_node.net_name = "2600net"
        mock_node.send = AsyncMock()
        mock_node.active_engine = None
        mock_node.db = MagicMock()
        mock_node.db.async_session = self.async_session

        router = CommandRouter(mock_node)

        with patch("ai_grid.core.handlers.handle_remote_net_command", new_callable=AsyncMock) as mock_remote, \
             patch("ai_grid.core.handlers.remote_net.handle_remote_net_command", mock_remote):

            for verb in ["upgrade", "repair", "status", "stash"]:
                mock_remote.reset_mock()
                await router.dispatch("Attacker", "PRIVMSG", "#2600net", f"!a raid Rizon {verb} Rizon_Owned_Outpost", is_admin=False)
                await asyncio.sleep(0.01)
                mock_remote.assert_called_with(
                    mock_node, "Attacker", "#2600net", "Rizon", [verb, "Rizon_Owned_Outpost"]
                )

            # Siphon with args
            mock_remote.reset_mock()
            await router.dispatch("Attacker", "PRIVMSG", "#2600net", "!a raid Rizon siphon Rizon_Owned_Outpost power 50%", is_admin=False)
            await asyncio.sleep(0.01)
            mock_remote.assert_called_with(
                mock_node, "Attacker", "#2600net", "Rizon", ["siphon", "Rizon_Owned_Outpost", "power", "50%"]
            )

    async def test_handle_remote_alert_routing_dispatch(self):
        """R3: Test alert is dispatched to remote network channel via target_bot or hub relay, never local channel."""
        mock_node = MagicMock()
        mock_node.net_name = "2600net"
        mock_hub = MagicMock()
        mock_node.hub = mock_hub

        target_bot = MagicMock()
        target_bot.config = {"channel": "#rizon"}
        target_bot.send = AsyncMock()
        mock_hub.nodes = {"Rizon": target_bot}

        alert_data = {
            "target_network": "Rizon",
            "alert_msg": "TEST ALERT: Cross-network intrusion detected.",
            "recipient_id": 1,
        }

        # 1. Target bot on hub
        await handlers.remote_net.handle_remote_alert_routing(mock_node, alert_data)
        target_bot.send.assert_called_once()
        sent_line = target_bot.send.call_args[0][0]
        self.assertIn("PRIVMSG #rizon", sent_line)
        self.assertIn("Cross-network intrusion detected", sent_line)
        # Ensure local node never received the alert
        mock_node.send.assert_not_called()

        # 2. Hub relay fallback
        mock_hub.nodes = {}
        mock_hub.relay_message = AsyncMock(return_value=True)
        await handlers.remote_net.handle_remote_alert_routing(mock_node, alert_data)
        mock_hub.relay_message.assert_called_once()
        self.assertEqual(mock_hub.relay_message.call_args[0][0], "Rizon")
        self.assertEqual(mock_hub.relay_message.call_args[0][1], "#rizon")

    async def test_claim_and_manage_case_insensitive_nick(self):
        """Verify remote_claim_node, remote_manage_node, and set_net_device_state resolve lowercase/mixed-case nicks."""
        # Lowercase nick 'attacker' against registered 'Attacker'
        res_claim = await self.repo.remote_claim_node("attacker", "2600net", "Rizon", "Rizon_Open_Unowned")
        self.assertTrue(res_claim["success"], f"Case-insensitive claim failed: {res_claim.get('msg')}")

        res_manage = await self.repo.remote_manage_node("attacker", "2600net", "Rizon", "Rizon_Owned_Outpost", "status")
        self.assertTrue(res_manage["success"], f"Case-insensitive manage failed: {res_manage.get('msg')}")

        ok, msg = await self.repo.set_net_device_state("attacker", "2600net", "CLOSED", node_name="Rizon_Owned_Outpost")
        self.assertTrue(ok, f"Case-insensitive state set failed: {msg}")

    async def test_claim_and_manage_with_distinct_alias_and_char_name(self):
        """Verify operations resolve correctly when NetworkAlias nickname differs from Character name."""
        async with self.async_session() as session:
            p = Player(global_name="AliasMaster")
            session.add(p)
            await session.flush()
            session.add(NetworkAlias(player_id=p.id, network_name="2600net", nickname="ShadowRunner"))
            c = Character(player_id=p.id, name="GhostAgent", race="Cyborg", char_class="Netrunner", credits=2000.0, power=200.0)
            session.add(c)
            await session.flush()
            node_base = GridNode(
                name="ShadowBase",
                owner_character_id=c.id,
                net_affinity="Rizon",
                addons_json=json.dumps({"NET": True, "NET_STATE": "OPEN"}),
                availability_mode="OPEN",
            )
            session.add(node_base)
            await session.flush()
            c.node_id = node_base.id

            target_outpost = GridNode(
                name="Rizon_Alias_Target",
                node_type="void",
                owner_character_id=None,
                net_affinity="Rizon",
                addons_json=json.dumps({"NET": True}),
                availability_mode="OPEN",
            )
            session.add(target_outpost)
            await session.commit()

        # Claim using NetworkAlias nickname 'ShadowRunner'
        res = await self.repo.remote_claim_node("ShadowRunner", "2600net", "Rizon", "Rizon_Alias_Target")
        self.assertTrue(res["success"], f"Claim via alias failed: {res.get('msg')}")

        # Manage using lowercase alias
        res_m = await self.repo.remote_manage_node("shadowrunner", "2600net", "Rizon", "Rizon_Alias_Target", "status")
        self.assertTrue(res_m["success"], f"Manage via lowercase alias failed: {res_m.get('msg')}")


if __name__ == "__main__":
    unittest.main()

