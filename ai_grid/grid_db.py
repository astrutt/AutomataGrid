import asyncio
import argparse
import logging
import os
import shutil
import json
import datetime
import sys

# --- Path Injection (Allows running from within the package directory) ---
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text, func, insert
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.future import select

from ai_grid.models import (
    Base, Player, NetworkAlias, Character, GridNode, NodeConnection, 
    PulseEvent, IncursionEvent, IncursionDefender, RaidTarget,
    DiscoveryRecord, BreachRecord, ItemTemplate, InventoryItem, 
    MainframeTask, AuctionListing, Leaderboard, CipherSession, GlobalMarket, Memo,
    ArenaBet, CharacterSkill, Bounty
)
from ai_grid.database.core import DB_FILE, logger, CONFIG, GRID_EXPANSION, GRID_CONNECTIONS, BRIDGE_MAPPING, LOOT_TEMPLATES
from ai_grid.database.repositories.skill_repo import SkillRepository
from ai_grid.database.repositories.navigation_repo import NavigationRepository
from ai_grid.database.repositories.territory_repo import TerritoryRepository
from ai_grid.database.repositories.discovery_repo import DiscoveryRepository
from ai_grid.database.repositories.infiltration_repo import InfiltrationRepository
from ai_grid.database.repositories.maintenance_repo import MaintenanceRepository
from ai_grid.database.repositories.identity_repo import IdentityRepository
from ai_grid.database.repositories.character_repo import CharacterRepository
from ai_grid.database.repositories.communication_repo import CommunicationRepository
from ai_grid.database.repositories.activity_repo import ActivityRepository
from ai_grid.database.repositories.progression_repo import ProgressionRepository
from ai_grid.database.repositories.economy_repo import EconomyRepository
from ai_grid.database.repositories.mainframe_repo import MainframeRepository
from ai_grid.database.repositories.minigame_repo import MiniGameRepository
from ai_grid.database.repositories.combat_repo import CombatRepository
from ai_grid.database.repositories.pulse_repo import PulseRepository
from ai_grid.database.repositories.spectator_repo import SpectatorRepository
from ai_grid.database.spectator_repo import SpectatorRepository as SpectatorCoreRepository
from ai_grid.database.repositories.incursion_repo import IncursionRepository
from ai_grid.database.repositories.expansion_repo import ExpansionRepository
from ai_grid.database.repositories.remote_net_repo import RemoteNetRepository, get_net_device_state
from ai_grid.database.repositories.betting_repo import BettingRepository
from ai_grid.database.repositories.reputation_repo import ReputationRepository
from ai_grid.database.repositories.bounty_repo import BountyRepository

class ArenaDB:
    def __init__(self, db_path=DB_FILE):
        if db_path == ":memory:":
            self.db_path = "sqlite+aiosqlite://"
            self.engine = create_async_engine(
                "sqlite+aiosqlite://",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
                echo=False
            )
        else:
            self.db_path = f"sqlite+aiosqlite:///{db_path}"
            self.engine = create_async_engine(self.db_path, echo=False)
        self.async_session = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        
        # Repositories (Domain Partitions)
        self.identity = IdentityRepository(self.async_session)
        self.character = CharacterRepository(self.async_session)
        self.comm = CommunicationRepository(self.async_session)
        self.activity = ActivityRepository(self.async_session)
        self.progression = ProgressionRepository(self.async_session)
        self.economy = EconomyRepository(self.async_session)
        self.combat = CombatRepository(self.async_session)
        self.mainframe = MainframeRepository(self.async_session)
        self.minigame = MiniGameRepository(self.async_session)
        self.spectator = SpectatorRepository(self.async_session)
        self.spectator_repo = SpectatorCoreRepository(self.async_session)
        self.reputation = ReputationRepository(self.async_session)
        self.betting = BettingRepository(self.async_session)
        self.skill = SkillRepository(self.async_session)
        self.bounty = BountyRepository(self.async_session)
        
        # Grid domains
        self.navigation = NavigationRepository(self.async_session)
        self.territory = TerritoryRepository(self.async_session)
        self.discovery = DiscoveryRepository(self.async_session)
        self.infiltration = InfiltrationRepository(self.async_session)
        self.maintenance = MaintenanceRepository(self.async_session)
        self.pulse = PulseRepository(self.async_session)
        self.incursion = IncursionRepository(self.async_session)
        self.expansion = ExpansionRepository(self.async_session)
        self.remote_net = RemoteNetRepository(self.async_session)

        # Legacy Facade Compatibility (grid object proxy)
        class GridFacade:
            def __init__(self, db):
                self.db = db
            async def get_spawn_node_name(self, *a, **k): return await self.db.navigation.get_spawn_node_name(*a, **k)
            async def set_spawn_node(self, *a, **k): return await self.db.navigation.set_spawn_node(*a, **k)
            async def get_claimed_nodes(self, *a, **k): return await self.db.navigation.get_claimed_nodes(*a, **k)
            async def get_location(self, *a, **k): return await self.db.navigation.get_location(*a, **k)
            async def move_player(self, *a, **k): return await self.db.navigation.move_player(*a, **k)
            async def move_player_to_node(self, *a, **k): return await self.db.navigation.move_player_to_node(*a, **k)
            async def claim_node(self, *a, **k): return await self.db.territory.claim_node(*a, **k)
            async def upgrade_node(self, *a, **k): return await self.db.territory.upgrade_node(*a, **k)
            async def set_grid_mode(self, *a, **k): return await self.db.territory.set_grid_mode(*a, **k)
            async def grid_repair(self, *a, **k): return await self.db.territory.grid_repair(*a, **k)
            async def grid_recharge(self, *a, **k): return await self.db.territory.grid_recharge(*a, **k)
            async def install_node_addon(self, *a, **k): return await self.db.territory.install_node_addon(*a, **k)
            async def get_stash(self, *a, **k): return await self.db.territory.get_stash(*a, **k)
            async def stash_store(self, *a, **k): return await self.db.territory.stash_store(*a, **k)
            async def stash_take(self, *a, **k): return await self.db.territory.stash_take(*a, **k)
            async def bolster_node(self, *a, **k): return await self.db.territory.bolster_node(*a, **k)
            async def link_network(self, *a, **k): return await self.db.territory.link_network(*a, **k)
            async def rename_node(self, *a, **k): return await self.db.territory.rename_node(*a, **k)
            async def community_rename_node(self, *a, **k): return await self.db.territory.community_rename_node(*a, **k)
            async def update_node_description(self, *a, **k): return await self.db.territory.update_node_description(*a, **k)
            async def explore_node(self, *a, **k): return await self.db.discovery.explore_node(*a, **k)
            async def probe_node(self, *a, **k): return await self.db.discovery.probe_node(*a, **k)
            async def hack_node(self, *a, **k): return await self.db.infiltration.hack_node(*a, **k)
            async def exploit_node(self, *a, **k): return await self.db.infiltration.exploit_node(*a, **k)
            async def siphon_node(self, *a, **k): return await self.db.infiltration.siphon_node(*a, **k)
            async def raid_node(self, *a, **k): return await self.db.infiltration.raid_node(*a, **k)
            async def tick_grid_power(self, *a, **k): return await self.db.maintenance.tick_grid_power(*a, **k)
            async def get_grid_telemetry(self, *a, **k): return await self.db.maintenance.get_grid_telemetry(*a, **k)
            async def remote_explore(self, *a, **k): return await self.db.remote_net.remote_explore(*a, **k)
            async def remote_probe(self, *a, **k): return await self.db.remote_net.remote_probe(*a, **k)
            async def remote_hack(self, *a, **k): return await self.db.remote_net.remote_hack(*a, **k)
            async def remote_siphon(self, *a, **k): return await self.db.remote_net.remote_siphon(*a, **k)
            async def remote_exploit(self, *a, **k): return await self.db.remote_net.remote_exploit(*a, **k)
            async def remote_raid(self, *a, **k): return await self.db.remote_net.remote_raid(*a, **k)
            async def set_net_device_state(self, *a, **k): return await self.db.remote_net.set_net_device_state(*a, **k)
            async def is_node_pvp_pve_accessible(self, *a, **k): return await self.db.remote_net.is_node_pvp_pve_accessible(*a, **k)
            async def check_territory_defense(self, *a, **k): return await self.db.territory.check_territory_defense(*a, **k)

        self.grid = GridFacade(self)

        # Player Facade Compatibility (Legacy repository proxy)
        class PlayerFacade:
            def __init__(self, db):
                self.db = db
            async def add_experience(self, *a, **k): return await self.db.progression.add_experience(*a, **k)
            async def get_player(self, *a, **k): return await self.db.character.get_player(*a, **k)
            async def rank_up_stat(self, *a, **k): return await self.db.progression.rank_up_stat(*a, **k)
            async def mark_memos_read(self, *a, **k): return await self.db.comm.mark_memos_read(*a, **k)
            async def get_memos(self, *a, **k): return await self.db.comm.get_memos(*a, **k)
            async def complete_task(self, *a, **k): return await self.db.activity.complete_task(*a, **k)
            async def register_player(self, *a, **k): return await self.db.identity.register_player(*a, **k)
            async def login_player(self, *a, **k): return await self.db.identity.login_player(*a, **k)
            async def authenticate_player(self, *a, **k): return await self.db.identity.authenticate_player(*a, **k)
            async def get_character_by_nick(self, *a, **k): return await self.db.identity.get_character_by_nick(*a, **k)
            async def list_players(self, *a, **k): return await self.db.character.list_players(*a, **k)
            async def update_last_seen(self, *a, **k): return await self.db.character.update_last_seen(*a, **k)
            async def update_activity_stats(self, *a, **k): return await self.db.character.update_activity_stats(*a, **k)
            async def get_prefs(self, *a, **k): return await self.db.character.get_prefs(*a, **k)
            async def get_prefs_by_id(self, *a, **k): return await self.db.character.get_prefs_by_id(*a, **k)
            async def set_pref(self, *a, **k): return await self.db.character.set_pref(*a, **k)
            async def get_nickname_by_id(self, *a, **k): return await self.db.identity.get_nickname_by_id(*a, **k)
            async def active_powergen(self, *a, **k): return await self.db.activity.active_powergen(*a, **k)
            async def active_training(self, *a, **k): return await self.db.activity.active_training(*a, **k)
            async def get_available_skills(self, *a, **k): return await self.db.skill.get_available_skills(*a, **k)
            async def get_character_skills(self, *a, **k): return await self.db.skill.get_character_skills(*a, **k)
            async def get_skill_info(self, *a, **k): return await self.db.skill.get_skill_info(*a, **k)
            async def start_skill(self, *a, **k): return await self.db.skill.start_skill(*a, **k)
            async def train_skill(self, *a, **k): return await self.db.skill.train_skill(*a, **k)
            async def forget_skill(self, *a, **k): return await self.db.skill.forget_skill(*a, **k)
            async def quit_training(self, *a, **k): return await self.db.skill.quit_training(*a, **k)
            async def get_skill_level(self, *a, **k): return await self.db.skill.get_skill_level(*a, **k)
            async def get_skill_modifiers_by_nick(self, *a, **k): return await self.db.skill.get_skill_modifiers_by_nick(*a, **k)

        self.player = PlayerFacade(self)

    # Primary Facade Methods (Direct delegation for ArenaDB level calls)
    async def get_available_skills(self, *a, **k): return await self.skill.get_available_skills(*a, **k)
    async def get_character_skills(self, *a, **k): return await self.skill.get_character_skills(*a, **k)
    async def get_skill_info(self, *a, **k): return await self.skill.get_skill_info(*a, **k)
    async def get_skill_level(self, *a, **k): return await self.skill.get_skill_level(*a, **k)
    async def start_skill(self, *a, **k): return await self.skill.start_skill(*a, **k)
    async def train_skill(self, *a, **k): return await self.skill.train_skill(*a, **k)
    async def forget_skill(self, *a, **k): return await self.skill.forget_skill(*a, **k)
    async def quit_training(self, *a, **k): return await self.skill.quit_training(*a, **k)
    async def get_skill_modifiers_by_nick(self, *a, **k): return await self.skill.get_skill_modifiers_by_nick(*a, **k)
    async def get_spawn_node_name(self, *a, **k): return await self.navigation.get_spawn_node_name(*a, **k)
    async def set_spawn_node(self, *a, **k): return await self.navigation.set_spawn_node(*a, **k)
    async def get_location(self, *a, **k): return await self.navigation.get_location(*a, **k)
    async def move_player(self, *a, **k): return await self.navigation.move_player(*a, **k)
    async def update_rep(self, *a, **k): return await self.reputation.update_rep(*a, **k)
    async def update_heat(self, *a, **k): return await self.reputation.update_heat(*a, **k)
    async def get_rep_summary(self, *a, **k): return await self.reputation.get_rep_summary(*a, **k)
    async def decay_reputation_and_heat(self, *a, **k): return await self.reputation.decay_reputation_and_heat(*a, **k)
    async def apply_passive_decay(self, *a, **k): return await self.reputation.apply_passive_decay(*a, **k)
    async def check_defender_spawn(self, *a, **k): return await self.reputation.check_defender_spawn(*a, **k)
    async def check_node_entry_encounter(self, *a, **k): return await self.reputation.check_node_entry_encounter(*a, **k)
    async def is_bounty_eligible(self, *a, **k): return await self.reputation.is_bounty_eligible(*a, **k)
    async def get_active_bounties(self, *a, **k): return await self.bounty.get_active_bounties(*a, **k)
    async def get_bounty_for_target(self, *a, **k): return await self.bounty.get_bounty_for_target(*a, **k)
    async def post_bounty(self, *a, **k): return await self.bounty.post_bounty(*a, **k)
    async def ensure_mcp_bounty(self, *a, **k): return await self.bounty.ensure_mcp_bounty(*a, **k)
    async def claim_bounty(self, *a, **k): return await self.bounty.claim_bounty(*a, **k)
    async def craft_item(self, *a, **k): return await self.economy.craft_item(*a, **k)
    async def get_craft_menu(self, *a, **k): return await self.economy.get_craft_menu(*a, **k)
    async def claim_node(self, *a, **k): return await self.territory.claim_node(*a, **k)
    async def upgrade_node(self, *a, **k): return await self.territory.upgrade_node(*a, **k)
    async def grid_repair(self, *a, **k): return await self.territory.grid_repair(*a, **k)
    async def grid_recharge(self, *a, **k): return await self.territory.grid_recharge(*a, **k)
    async def siphon_node(self, *a, **k): return await self.infiltration.siphon_node(*a, **k)
    async def hack_node(self, *a, **k): return await self.infiltration.hack_node(*a, **k)
    async def raid_node(self, *a, **k): return await self.infiltration.raid_node(*a, **k)
    async def install_node_addon(self, *a, **k): return await self.territory.install_node_addon(*a, **k)
    async def get_stash(self, *a, **k): return await self.territory.get_stash(*a, **k)
    async def stash_store(self, *a, **k): return await self.territory.stash_store(*a, **k)
    async def stash_take(self, *a, **k): return await self.territory.stash_take(*a, **k)
    async def bolster_node(self, *a, **k): return await self.territory.bolster_node(*a, **k)
    async def link_network(self, *a, **k): return await self.territory.link_network(*a, **k)
    async def explore_node(self, *a, **k): return await self.discovery.explore_node(*a, **k)
    async def exploit_node(self, *a, **k): return await self.infiltration.exploit_node(*a, **k)
    async def probe_node(self, *a, **k): return await self.discovery.probe_node(*a, **k)
    async def remote_explore(self, *a, **k): return await self.remote_net.remote_explore(*a, **k)
    async def remote_probe(self, *a, **k): return await self.remote_net.remote_probe(*a, **k)
    async def remote_hack(self, *a, **k): return await self.remote_net.remote_hack(*a, **k)
    async def remote_siphon(self, *a, **k): return await self.remote_net.remote_siphon(*a, **k)
    async def remote_exploit(self, *a, **k): return await self.remote_net.remote_exploit(*a, **k)
    async def remote_raid(self, *a, **k): return await self.remote_net.remote_raid(*a, **k)
    async def set_net_device_state(self, *a, **k): return await self.remote_net.set_net_device_state(*a, **k)
    async def is_node_pvp_pve_accessible(self, *a, **k): return await self.remote_net.is_node_pvp_pve_accessible(*a, **k)
    async def get_grid_stats(self, *a, **k): return await self.expansion.get_grid_stats(*a, **k)
    async def get_grid_status(self, *a, **k): return await self.expansion.get_grid_status(*a, **k)
    async def get_node_info(self, *a, **k): return await self.expansion.get_node_info(*a, **k)
    async def expand_grid(self, *a, **k): return await self.expansion.expand_grid(*a, **k)
    async def ensure_network_home_node(self, *a, **k): return await self.expansion.ensure_network_home_node(*a, **k)

    async def close(self):
        await self.engine.dispose()

    async def generate_master_grid(self, width: int = 50, height: int = 50):
        """
        Procedurally generates a 50x50 coordinate map (2,500 nodes) with realistic
        network topology, 14+ region types, and calibrated population targets:
        - 700 Active nodes (~28%):
          * 400 MCP controlled nodes
          * 150 NPC / merchant nodes
          * 100 Raid targets (backed by RaidTarget models)
          * 50 Player claimable nodes
        - 1,800 Unrouted / Void nodes (~72%)
        Topological grouping:
        - Critical Infrastructure: ICS + UTL paired
        - Corporate / Financial: CRP + DTC + POS clustered
        - Public / Civic: CIV + SMB + EDU (+ ORG, MED) clustered
        - Defense Perimeter: GOV + LEA + MIL clustered
        - Conflict / Arena: ARN + WAR clustered
        - Fixed core hubs (UpLink, Arena, Vault, Rizon, 2600net, Edges) preserved.
        """
        import math
        logger.info(f"Generating procedural grid topology ({width}x{height})...")
        async with self.async_session() as session:
            # 1. Clear existing topology
            await session.execute(text("DELETE FROM discovery_records"))
            await session.execute(text("DELETE FROM node_connections"))
            await session.execute(text("DELETE FROM raid_targets"))
            await session.execute(text("DELETE FROM grid_nodes"))
            await session.flush()

            # 2. Fixed Core Hubs
            # (x, y, name, node_type, region_type, net_affinity, is_spawn, is_unlocked, level, power, controller)
            fixed_hubs = [
                (25, 25, "UpLink", "safezone", "CIV", None, True, True, 1, 100.0, "MCP"),
                (25, 30, "Arena", "arena", "ARN", None, False, True, 4, 1000000.0, "MCP"),
                (25, 45, "Vault", "safezone", "CIV", None, False, False, 4, 1000000.0, "MCP"),
                (10, 40, "Rizon", "void", "DTC", "rizon", False, True, 1, 1000.0, "MCP"),
                (40, 10, "2600net", "void", "DTC", "2600net", False, True, 1, 1000.0, "MCP"),
                (10, 10, "Edge_West", "void", "VOD", None, False, False, 4, 0.0, None),
                (40, 40, "Edge_East", "void", "VOD", None, False, False, 4, 0.0, None)
            ]
            fixed_hub_coords = set((h[0], h[1]) for h in fixed_hubs)

            # 3. Functional Zone Anchors and Topological Grouping
            zones = [
                {
                    "id": 0,
                    "name": "Civic_Nexus",
                    "center": (25, 25),
                    "target_count": 196,
                    "types": ["CIV", "SMB", "EDU", "ORG", "MED"]
                },
                {
                    "id": 1,
                    "name": "Corporate_Financial",
                    "center": (38, 26),
                    "target_count": 150,
                    "types": ["CRP", "DTC", "POS"]
                },
                {
                    "id": 2,
                    "name": "Critical_Infrastructure",
                    "center": (12, 26),
                    "target_count": 135,
                    "types": ["ICS", "UTL"]
                },
                {
                    "id": 3,
                    "name": "Defense_Perimeter",
                    "center": (25, 12),
                    "target_count": 140,
                    "types": ["GOV", "LEA", "MIL"]
                },
                {
                    "id": 4,
                    "name": "Conflict_Arena",
                    "center": (25, 36),
                    "target_count": 74,
                    "types": ["ARN", "WAR"]
                }
            ]

            # Collect non-fixed candidate coordinates
            candidate_coords = []
            for gy in range(height):
                for gx in range(width):
                    if (gx, gy) in fixed_hub_coords:
                        continue
                    min_dist = 99999.0
                    best_zone = zones[0]
                    for z in zones:
                        zx, zy = z["center"]
                        d = math.hypot(gx - zx, gy - zy)
                        if d < min_dist:
                            min_dist = d
                            best_zone = z
                    candidate_coords.append((min_dist, gx, gy, best_zone))

            # Sort candidate coordinates by proximity to their closest zone anchor
            candidate_coords.sort(key=lambda item: item[0])

            # The top 695 closest coordinates become active nodes (plus 5 active fixed hubs = 700 active nodes)
            active_chosen = candidate_coords[:695]
            active_coords_map = {}

            for min_dist, gx, gy, z in active_chosen:
                zid = z["id"]
                cx, cy = z["center"]

                if zid == 0:
                    # Civic Nexus (CIV, SMB, EDU, ORG, MED)
                    d2 = (gx - cx)**2 + (gy - cy)**2
                    if d2 <= 9:
                        r_type = "CIV"
                    elif gx >= cx and gy >= cy:
                        r_type = "SMB"
                    elif gx < cx and gy <= cy:
                        r_type = "EDU"
                    elif gx < cx and gy > cy:
                        r_type = "MED"
                    else:
                        r_type = "ORG"
                elif zid == 1:
                    # Corporate / Financial (CRP, DTC, POS)
                    if gy <= cy:
                        r_type = "CRP"
                    elif gx >= cx:
                        r_type = "DTC"
                    else:
                        r_type = "POS"
                elif zid == 2:
                    # Critical Infrastructure (ICS, UTL)
                    r_type = "ICS" if gx <= cx else "UTL"
                elif zid == 3:
                    # Defense Perimeter (GOV, LEA, MIL)
                    d2 = (gx - cx)**2 + (gy - cy)**2
                    if d2 <= 9:
                        r_type = "GOV"
                    elif gy >= cy:
                        r_type = "LEA"
                    else:
                        r_type = "MIL"
                else:
                    # Conflict / Arena (ARN, WAR)
                    d2 = (gx - cx)**2 + (gy - cy)**2
                    r_type = "ARN" if d2 <= 9 else "WAR"

                active_coords_map[(gx, gy)] = (z, r_type)

            # Population Target distribution across the 695 active coordinates:
            # - Claimable: 50
            # - Raid targets: 100
            # - NPC / merchant: 150
            # - MCP controlled: 395 (plus 5 active fixed hubs = 400 MCP controlled)
            # Total active = 695 + 5 = 700!

            claimable_candidates = [c for c, (z, rt) in active_coords_map.items() if rt in ["CIV", "CRP", "SMB"]]
            if len(claimable_candidates) < 50:
                claimable_candidates.extend([c for c in active_coords_map if c not in claimable_candidates])
            claimable_set = set(claimable_candidates[:50])

            raid_candidates = [c for c, (z, rt) in active_coords_map.items() if c not in claimable_set and rt in ["GOV", "MIL", "LEA", "CRP", "DTC", "ICS", "UTL", "SMB"]]
            if len(raid_candidates) < 100:
                raid_candidates.extend([c for c in active_coords_map if c not in claimable_set and c not in raid_candidates])
            raid_set = set(raid_candidates[:100])

            npc_candidates = [c for c, (z, rt) in active_coords_map.items() if c not in claimable_set and c not in raid_set and rt in ["SMB", "POS", "ORG", "MED", "CIV", "EDU", "CRP"]]
            if len(npc_candidates) < 150:
                npc_candidates.extend([c for c in active_coords_map if c not in claimable_set and c not in raid_set and c not in npc_candidates])
            npc_set = set(npc_candidates[:150])

            node_dicts = []
            raid_dicts = []
            node_counter = 1
            rt_counter = 1

            # 1. Add Fixed Core Hubs
            for gx, gy, cname, ctype, caff_reg, caff_net, is_spawn, is_unl, lvl, pwr, ctrl in fixed_hubs:
                addons = {"NET": True} if caff_net else {}
                node_dicts.append({
                    "id": node_counter,
                    "name": cname,
                    "description": f"Core Hub: {cname}.",
                    "x": gx, "y": gy,
                    "node_type": ctype,
                    "region_type": caff_reg,
                    "controller": ctrl,
                    "net_affinity": caff_net,
                    "is_spawn_node": is_spawn,
                    "is_unlocked": is_unl,
                    "upgrade_level": lvl,
                    "power_stored": pwr,
                    "durability": 100.0,
                    "availability_mode": 'OPEN' if (caff_net or is_spawn or is_unl) else 'CLOSED',
                    "addons_json": json.dumps(addons),
                    "active_target_id": None
                })
                node_counter += 1

            # 2. Add All Other Grid Coordinates
            for gy in range(height):
                for gx in range(width):
                    coord = (gx, gy)
                    if coord in fixed_hub_coords:
                        continue

                    current_node_id = node_counter
                    node_counter += 1

                    if coord in active_coords_map:
                        z, r_type = active_coords_map[coord]
                        is_near_spawn = (abs(gx - 25) <= 3 and abs(gy - 25) <= 3)

                        if coord in claimable_set:
                            controller_role = "CLAIMABLE"
                            avail_mode = "OPEN"
                            node_op_type = "void" if r_type not in ["CIV", "MED", "ORG"] else "safezone"
                        elif coord in raid_set:
                            controller_role = "RAID"
                            avail_mode = "CLOSED"
                            node_op_type = "void"
                        elif coord in npc_set:
                            controller_role = "NPC"
                            avail_mode = "OPEN"
                            node_op_type = "merchant"
                        else:
                            controller_role = "MCP"
                            avail_mode = "CLOSED" if not is_near_spawn else "OPEN"
                            if r_type == "ARN":
                                node_op_type = "arena"
                            elif r_type in ["CIV", "MED", "ORG"]:
                                node_op_type = "safezone"
                            elif r_type in ["SMB", "POS"]:
                                node_op_type = "merchant"
                            else:
                                node_op_type = "void"

                        lvl = 1 + ((gx + gy) % 3)
                        at_id = None
                        if controller_role == "RAID":
                            at_id = rt_counter
                            rt_counter += 1
                            raid_dicts.append({
                                "id": at_id,
                                "node_id": current_node_id,
                                "name": f"[{r_type}] {r_type}_{gx}_{gy}",
                                "target_type": r_type,
                                "difficulty": 10 + (lvl * 5),
                                "credits_pool": 500.0 * lvl,
                                "data_pool": 50.0 * lvl,
                                "is_active": True,
                                "availability_mode": "CLOSED"
                            })

                        node_dicts.append({
                            "id": current_node_id,
                            "name": f"{r_type}_{gx}_{gy}",
                            "description": f"Active grid node {r_type} sector ({gx}, {gy}).",
                            "x": gx, "y": gy,
                            "node_type": node_op_type,
                            "region_type": r_type,
                            "controller": controller_role,
                            "upgrade_level": lvl,
                            "durability": 100.0,
                            "power_stored": 100.0 * lvl,
                            "availability_mode": avail_mode,
                            "is_unlocked": is_near_spawn,
                            "is_spawn_node": False,
                            "net_affinity": None,
                            "active_target_id": at_id,
                            "addons_json": "{}"
                        })
                    else:
                        is_near_spawn = (abs(gx - 25) <= 3 and abs(gy - 25) <= 3)
                        node_dicts.append({
                            "id": current_node_id,
                            "name": f"Sector_{gx}_{gy}",
                            "description": f"Unrouted wasteland sector ({gx}, {gy}).",
                            "x": gx, "y": gy,
                            "node_type": "void",
                            "region_type": "VOD",
                            "controller": None,
                            "upgrade_level": 1,
                            "durability": 100.0,
                            "power_stored": 0.0,
                            "availability_mode": "CLOSED",
                            "is_unlocked": is_near_spawn,
                            "is_spawn_node": False,
                            "net_affinity": None,
                            "active_target_id": None,
                            "addons_json": "{}"
                        })

            await session.execute(insert(GridNode), node_dicts)
            if raid_dicts:
                await session.execute(insert(RaidTarget), raid_dicts)
            await session.commit()
            logger.info(f"Procedural grid generated: {len(node_dicts)} nodes (700 active, 1800 void).")

    async def init_schema(self):
        logger.info("Initializing v2.0 database schema...")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        
        # v2.0 Procedural Initialization
        await self.generate_master_grid()
        
        async with self.async_session() as session:
            # Seed Item Templates (Task 021+)
            for tpl_data in LOOT_TEMPLATES:
                session.add(ItemTemplate(**tpl_data))
            
            await session.commit()
        logger.info("v2.0 Schema successfully initialized.")

    async def create_snapshot(self):
        """Creates a timestamped backup of the current database."""
        if not os.path.exists(DB_FILE):
             logger.warning("No database file found to snapshot.")
             return False
        
        bak_file = f"{DB_FILE}.bak"
        shutil.copy2(DB_FILE, bak_file)
        logger.info(f"Database snapshot created: {bak_file}")
        return True

    async def rollback_schema(self):
        """Reverts the database to the latest .bak snapshot."""
        bak_file = f"{DB_FILE}.bak"
        if not os.path.exists(bak_file):
            logger.error("No snapshot found to rollback to.")
            return False, "No snapshot file detected."
            
        shutil.copy2(bak_file, DB_FILE)
        logger.info("Database rolled back to snapshot successfully.")
        return True, "Rollback successful."

    async def update_schema(self):
        """Non-destructive schema update (Reflective Migration)."""
        logger.info("Starting reflective schema update...")
        await self.create_snapshot()
        
        def sync_columns(conn):
            inspector = inspect(conn)
            existing_tables = inspector.get_table_names()
            
            for table_name, table in Base.metadata.tables.items():
                if table_name not in existing_tables:
                    logger.info(f"Creating missing table: {table_name}")
                    table.create(conn)
                    continue

                existing_cols = [c['name'] for c in inspector.get_columns(table_name)]
                for col_name, col in table.columns.items():
                    if col_name not in existing_cols:
                        logger.info(f"Adding missing column: {table_name}.{col_name}")
                        # SQLite-specific ALTER TABLE logic
                        col_type = col.type.compile(dialect=conn.dialect)
                        default_val = ""
                        if col.default is not None:
                            try:
                                default_arg = col.default.arg
                            except AttributeError:
                                default_arg = col.default

                            if callable(default_arg):
                                try:
                                    sample = default_arg()
                                    if isinstance(sample, dict):
                                        default_val = " DEFAULT '{}'"
                                    elif isinstance(sample, list):
                                        default_val = " DEFAULT '[]'"
                                    elif isinstance(sample, (int, float)):
                                        default_val = f" DEFAULT {sample}"
                                    elif isinstance(sample, str):
                                        default_val = f" DEFAULT '{sample}'"
                                except Exception:
                                    default_val = ""
                            elif isinstance(default_arg, (int, float)):
                                default_val = f" DEFAULT {default_arg}"
                            elif isinstance(default_arg, str):
                                default_val = f" DEFAULT '{default_arg}'"
                            elif isinstance(default_arg, dict):
                                default_val = " DEFAULT '{}'"

                        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}{default_val}"))

        async with self.engine.begin() as conn:
            await conn.run_sync(sync_columns)
        
        logger.info("Schema update complete.")
        return True

    async def verify_integrity(self):
        """Audit the database for structural sync and logical consistency."""
        logger.info("Running database integrity audit...")
        issues = []
        
        # 1. Structural Audit (Missing Tables/Columns)
        def get_db_schema(conn):
            from sqlalchemy import inspect
            inspector = inspect(conn)
            schema = {}
            for table_name in inspector.get_table_names():
                schema[table_name] = [c['name'] for c in inspector.get_columns(table_name)]
            return schema

        try:
            async with self.engine.connect() as conn:
                db_schema = await conn.run_sync(get_db_schema)
                
                for table_name, table in Base.metadata.tables.items():
                    if table_name not in db_schema:
                        issues.append(f"[CRITICAL] Missing table: {table_name}")
                        continue
                    
                    for col in table.columns:
                        if col.name not in db_schema[table_name]:
                            issues.append(f"[CRITICAL] Missing column: {table_name}.{col.name}")
        except Exception as e:
            issues.append(f"[CRITICAL] Failed to inspect database schema: {e}")

        # If structural issues found, instruct admin and return early
        if any("[CRITICAL]" in i for i in issues):
            logger.error("Database schema desync detected!")
            logger.error("Run 'python3 ai_grid/grid_db.py update' to safely synchronize without data loss.")
            return issues

        # 2. Logical Audit
        async with self.async_session() as session:
            # 1. Check Uplink
            uplink = (await session.execute(select(GridNode).where(GridNode.name == "UpLink"))).scalars().first()
            if not uplink: issues.append("[CRITICAL] UpLink node is missing.")
            
            # 2. Check Item Templates
            items = (await session.execute(select(ItemTemplate))).scalars().all()
            if len(items) < 2: issues.append("[WARNING] Core item templates (Food/Weapon) are missing.")
            
            # 3. Check for Ghost Characters (no owner)
            ghosts = (await session.execute(select(Character).where(Character.player_id == None))).scalars().all()
            if ghosts: issues.append(f"[WARNING] Detected {len(ghosts)} orphaned 'Ghost' characters.")

        if not issues: logger.info("Integrity check passed: No issues detected.")
        else:
            for issue in issues: logger.warning(issue)
        return issues

    async def run_repairs(self):
        """Self-heal core data and connections."""
        logger.info("Executing database self-repair...")
        
        # 1. Item Seeding (Always additive)
        await self.seed_items_only()
        
        # 2. Spawn Node Check
        async with self.async_session() as session:
            spawn = (await session.execute(select(GridNode).where(GridNode.is_spawn_node == True))).scalars().first()
            if not spawn:
                # Fallback: Find UpLink or first safezone
                fallback = (await session.execute(select(GridNode).where(GridNode.name == "UpLink"))).scalars().first()
                if not fallback:
                    fallback = (await session.execute(select(GridNode).where(GridNode.node_type == "safezone"))).scalars().first()
                
                if fallback:
                    fallback.is_spawn_node = True
                    logger.info(f"Restored Spawn Flag to existing node: {fallback.name}")
                else:
                    new_uplink = GridNode(name="UpLink", description="Central nexus.", node_type="safezone", is_spawn_node=True)
                    session.add(new_uplink)
                    logger.info("Restored missing central nexus (Uplink).")
                await session.commit()
        
        logger.info("Repair sequence finished.")
        return True

    async def seed_items_only(self):
        """Add missing item templates without touching the map."""
        async with self.async_session() as session:
            for tpl_data in LOOT_TEMPLATES:
                exists = (await session.execute(select(ItemTemplate).where(ItemTemplate.name == tpl_data["name"]))).scalars().first()
                if not exists: session.add(ItemTemplate(**tpl_data))
            await session.commit()

    async def seed_grid_expansion(self):
        """Smart Seeding: Add missing expansion nodes and connections."""
        async with self.async_session() as session:
            # Seed nodes additively
            for name, desc, node_type in GRID_EXPANSION:
                exists = (await session.execute(select(GridNode).where(GridNode.name == name))).scalars().first()
                if not exists:
                    is_spawn = (name == "UpLink")
                    session.add(GridNode(name=name, description=desc, node_type=node_type, is_spawn_node=is_spawn))
            
            await session.flush()

            # --- SEED BRIDGE AFFINITIES (Task 021 Fix) ---
            for node_name, net_target in BRIDGE_MAPPING.items():
                node = (await session.execute(select(GridNode).where(GridNode.name == node_name))).scalars().first()
                if node:
                    node.net_affinity = net_target
                    logger.info(f"Seeded net_affinity: {node_name} -> {net_target}")

            # --- SEED NETWORK HOME NODES (Task 021) ---
            for net_name in CONFIG.get('networks', {}).keys():
                await self.ensure_network_home_node(net_name)

            # Node Fixes (Merchant assignment & Cleanup)
            type_map = {
                "Black_Market_Port": "merchant",
                "Dark_Web_Exchange": "merchant",
                "Neural_Nexus": "safezone",
                "The_Arena": "arena",
                "Gladiator_Pit": "arena"
            }
            for node_name, n_type in type_map.items():
                node = (await session.execute(select(GridNode).where(GridNode.name == node_name))).scalars().first()
                if node:
                    node.node_type = n_type
                else:
                    session.add(GridNode(name=node_name, description=f"{node_name} sector.", node_type=n_type, upgrade_level=1, durability=100.0, is_unlocked=True))

            await session.commit()
            logger.info("Grid expansion seeded successfully.")

    # Delegation methods
    async def get_prefs(self, name, network): return await self.character.get_prefs(name, network)
    async def set_pref(self, name, network, key, value): return await self.character.set_pref(name, network, key, value)
    async def get_daily_tasks(self, name, network): return await self.activity.get_daily_tasks(name, network)
    async def complete_task(self, name, network, task_key): return await self.activity.complete_task(name, network, task_key)
    async def register_player(self, name, network, race, bot_class, bio, stats): return await self.identity.register_player(name, network, race, bot_class, bio, stats)
    async def login_player(self, nick, network, name, key): return await self.identity.login_player(nick, network, name, key)
    async def get_player(self, name, network): return await self.character.get_player(name, network)
    async def authenticate_player(self, name, network, provided_token): return await self.identity.authenticate_player(name, network, provided_token)
    async def list_players(self, network=None): return await self.character.list_players(network)
    async def get_character_by_nick(self, nick: str, network: str, session): return await self.identity.get_character_by_nick(nick, network, session)
    async def update_last_seen(self, nick: str, network: str): return await self.character.update_last_seen(nick, network)
    async def update_activity_stats(self, nick, net, chat, idle): return await self.character.update_activity_stats(nick, net, chat, idle)
    async def get_spectator_stats(self, nick, net, config): return await self.progression.get_spectator_stats(nick, net, config)
    async def tick_retention_policy(self, config): return await self.maintenance.tick_retention_policy(config)
    async def tick_player_maintenance(self, network, idlers): return await self.maintenance.tick_player_maintenance(network, idlers)
    async def active_powergen(self, name, network): return await self.activity.active_powergen(name, network)
    async def active_training(self, name, network): return await self.activity.active_training(name, network)
    async def explore_node(self, name, network): return await self.grid.explore_node(name, network)
    async def raid_node(self, name, network): return await self.grid.raid_node(name, network)

    async def get_location(self, name, network): return await self.grid.get_location(name, network)
    async def move_player(self, name, network, direction): return await self.grid.move_player(name, network, direction)
    async def move_player_to_node(self, name, network, node_name): return await self.grid.move_player_to_node(name, network, node_name)
    async def grid_repair(self, name, network): return await self.grid.grid_repair(name, network)
    async def grid_recharge(self, name, network): return await self.grid.grid_recharge(name, network)
    async def claim_node(self, name, network): return await self.grid.claim_node(name, network)
    async def upgrade_node(self, name, network): return await self.grid.upgrade_node(name, network)
    async def siphon_node(self, name, network, percentage=100.0): return await self.grid.siphon_node(name, network, percentage)
    async def hack_node(self, name, network): return await self.grid.hack_node(name, network)
    async def probe_node(self, name, network): return await self.grid.probe_node(name, network)
    async def install_node_addon(self, name, network, item_name): return await self.grid.install_node_addon(name, network, item_name)
    async def bolster_node(self, name, network, amount): return await self.grid.bolster_node(name, network, amount)
    async def link_network(self, name, network, local_net_name): return await self.grid.link_network(name, network, local_net_name)
    async def tick_grid_power(self): return await self.grid.tick_grid_power()
    async def get_grid_telemetry(self): return await self.grid.get_grid_telemetry()
    async def rename_node(self, old, new): return await self.grid.rename_node(old, new)
    async def check_territory_defense(self, nickname, network): return await self.grid.check_territory_defense(nickname, network)
    async def get_prefs_by_id(self, char_id): return await self.character.get_prefs_by_id(char_id)
    async def get_nickname_by_id(self, char_id): return await self.identity.get_nickname_by_id(char_id)

    async def list_shop_items(self): return await self.economy.list_shop_items()
    async def award_credits_bulk(self, payouts, network): return await self.economy.award_credits_bulk(payouts, network)
    async def process_transaction(self, name, network, action, item_name): return await self.economy.process_transaction(name, network, action, item_name)
    async def get_global_economy(self): return await self.economy.get_global_economy()

    async def record_match_result(self, winner_name, loser_name, network, was_surrender=False, winner_up=None, loser_up=None): 
        return await self.combat.record_match_result(winner_name, loser_name, network, was_surrender=was_surrender, winner_up=winner_up, loser_up=loser_up)
    async def resolve_mob_encounter(self, name, network): 
        return await self.combat.resolve_mob_encounter(name, network)
    async def grid_attack(self, attacker_name, target_name, network): return await self.combat.grid_attack(attacker_name, target_name, network)
    async def grid_hack(self, attacker_name, target_name, network): return await self.combat.grid_hack(attacker_name, target_name, network)
    async def grid_rob(self, attacker_name, target_name, network): return await self.combat.grid_rob(attacker_name, target_name, network)
    async def use_item(self, name, network, item_name): return await self.economy.use_item(name, network, item_name)
    
    # Economy (Auctions)
    async def list_active_auctions(self): return await self.economy.list_active_auctions()
    async def create_auction(self, name, network, item, start, dur): return await self.economy.create_auction(name, network, item, start, dur)
    async def bid_on_auction(self, name, network, aid, amt): return await self.economy.bid_on_auction(name, network, aid, amt)
    async def tick_auctions(self): return await self.economy.tick_auctions()
    async def update_market_rates(self, rates, text=None): return await self.economy.update_market_rates(rates, text)
    async def get_market_status(self): return await self.economy.get_market_status()

    # Mini-Games & Leaderboards
    async def roll_dice(self, name, net, bet, choice): return await self.minigame.roll_dice(name, net, bet, choice)
    async def start_cipher(self, name, net): return await self.minigame.start_cipher(name, net)
    async def guess_cipher(self, name, net, guess): return await self.minigame.submit_guess(name, net, guess)
    async def get_leaderboard(self, cat): return await self.minigame.get_leaderboard(cat)


    # Mainframe (The Gibson)
    async def get_gibson_status(self, name, network): return await self.mainframe.get_gibson_status(name, network)
    async def start_compilation(self, name, network, amount): return await self.mainframe.start_compilation(name, network, amount)
    async def start_assembly(self, name, network): return await self.mainframe.start_assembly(name, network)
    async def tick_mainframe_tasks(self): return await self.mainframe.tick_mainframe_tasks()

    # Spectator System (v1.8.0)
    async def spectator_drop(self, name, net, target=None, item_name=None): return await self.spectator.spectator_drop(name, net, target, item_name)
    async def trickle_spectator_power(self, net): return await self.spectator.trickle_power(net)
    async def rename_spectator_rank(self, name, net, title): return await self.spectator.rename_rank(name, net, title)
    async def collect_daily_spectator_bonus(self, name, net): return await self.spectator.award_daily_dividend(name, net)
    async def award_daily_dividend(self, name, net): return await self.spectator.award_daily_dividend(name, net)

    # Spectator System (v1.5.0 core passive accrual)
    async def upsert_spectator(self, nick, net): return await self.spectator_repo.upsert_spectator(nick, net)
    async def record_spectator_message(self, nick, net): return await self.spectator_repo.record_message(nick, net)
    async def get_spectator(self, nick, net): return await self.spectator_repo.get_spectator(nick, net)
    async def get_all_active_spectators(self, *a, **k): return await self.spectator_repo.get_all_active(*a, **k)
    async def apply_spectator_payout(self, nick, net, xp, credits): return await self.spectator_repo.apply_payout(nick, net, xp, credits)
    async def record_spectator_idle_hours(self, nick, net, hours=1.0): return await self.spectator_repo.record_idle_hours(nick, net, hours)
    async def reset_spectator_message_count(self, nick, net): return await self.spectator_repo.reset_message_count(nick, net)

    # Arena Gambling (v2.0)
    async def place_bet(self, *a, **k): return await self.betting.place_bet(*a, **k)
    async def resolve_bets(self, *a, **k): return await self.betting.resolve_bets(*a, **k)
    async def refund_bets(self, *a, **k): return await self.betting.refund_bets(*a, **k)
    async def get_match_bets(self, *a, **k): return await self.betting.get_match_bets(*a, **k)

    # Territory Community Actions (v2.0)
    async def community_rename_node(self, *a, **k): return await self.territory.community_rename_node(*a, **k)

async def async_main():
    parser = argparse.ArgumentParser(description="AutomataArena Async SQLAlchemy DB Manager")
    parser.add_argument("--network", type=str, help="Filter by network")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    subparsers.add_parser("init", help="Initialize the database schema")
    subparsers.add_parser("update", help="Non-destructive schema sync + seed maps")
    subparsers.add_parser("check", help="Run database integrity audit")
    subparsers.add_parser("rollback", help="Revert to last .bak snapshot")
    subparsers.add_parser("repair", help="Run automatic self-repair sequence")
    subparsers.add_parser("reseed", help="Update grid expansion nodes and connections")
    subparsers.add_parser("list", help="List all registered fighters")
    del_parser = subparsers.add_parser("delete", help="Delete a player")
    del_parser.add_argument("--name", type=str, required=True, help="Player nickname")
    args = parser.parse_args()
    db = ArenaDB()
    if args.command == "init":
        await db.init_schema()
        print("[*] Database schema initialized.")
    elif args.command == "update":
        await db.update_schema()
        await db.seed_grid_expansion()
        print("[*] Reflective update complete. Map seeded.")
    elif args.command == "check":
        issues = await db.verify_integrity()
        if not issues: print("[*] Integrity check passed.")
        else:
            print("[!] Found the following issues:")
            for i in issues: print(f"  - {i}")
    elif args.command == "rollback":
        success, msg = await db.rollback_schema()
        print(f"[{'*' if success else '!'}] {msg}")
    elif args.command == "repair":
        await db.run_repairs()
        print("[*] Repair sequence completed.")
    elif args.command == "reseed":
        await db.seed_grid_expansion()
        print("[*] Grid expansion re-seeded.")
    elif args.command == "list":
        players = await db.list_players(args.network)
        print(f"\n--- Registered Players ({len(players)}) ---")
        print(f"{'Name':<15} | {'Network':<10} | {'Elo':<6} | {'W/L':<7} | {'Credits'}")
        print("-" * 55)
        for p in players:
            wl = f"{p['wins']}/{p['losses']}"
            print(f"{p['name']:<15} | {p['network']:<10} | {p['elo']:<6} | {wl:<7} | {p['credits']}")
    elif args.command == "delete":
        async with db.async_session() as session:
            stmt = select(Player).join(NetworkAlias).where(NetworkAlias.nickname.ilike(args.name))
            p = (await session.execute(stmt)).scalars().first()
            if p:
                await session.delete(p)
                await session.commit()
                print(f"[*] Player {args.name} deleted.")
            else:
                print(f"[!] Player {args.name} not found.")
    else:
        parser.print_help()
    await db.close()

if __name__ == "__main__":
    asyncio.run(async_main())
