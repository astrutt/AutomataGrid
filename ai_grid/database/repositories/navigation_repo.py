# ai_grid/database/navigation_repo.py
import json
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import func
from ai_grid.models import Character, Player, NetworkAlias, GridNode, NodeConnection, DiscoveryRecord, RaidTarget
from ai_grid.database.core import CONFIG
from ai_grid.database.base_repo import BaseRepository

class NavigationRepository(BaseRepository):
    async def get_spawn_node_name(self) -> str:
        """Returns the current name of the functional Nexus/Spawn node."""
        async with self.async_session() as session:
            spawn = (await session.execute(select(GridNode).where(GridNode.is_spawn_node == True))).scalars().first()
            return spawn.name if spawn else "UpLink"

    async def set_spawn_node(self, target_name: str) -> tuple[bool, str]:
        """Relocates the Grid Nexus to a specific node."""
        async with self.async_session() as session:
            # 1. Clear old spawn bits
            await session.execute(GridNode.__table__.update().values(is_spawn_node=False))
            
            # 2. Set new spawn
            stmt = select(GridNode).where(func.lower(GridNode.name) == target_name.lower())
            new_spawn = (await session.execute(stmt)).scalars().first()
            if not new_spawn:
                return False, f"Target node '{target_name}' not found on grid mesh."
            
            new_spawn.is_spawn_node = True
            await session.commit()
            return True, f"Nexus translocation complete. All new entities will manifest at: {new_spawn.name}."

    async def get_claimed_nodes(self, name: str, network: str) -> int:
        """Returns the count of nodes owned by a character."""
        async with self.async_session() as session:
            char = await self.get_character_by_nick(name, network, session)
            if not char: return 0
            
            count_stmt = select(func.count(GridNode.id)).where(GridNode.owner_character_id == char.id)
            res = await session.execute(count_stmt)
            return res.scalar() or 0

    async def get_location(self, name: str, network: str):
        async with self.async_session() as session:
            char = await self.get_character_by_nick(name, network, session)
            if not char or not char.current_node:
                return None
            if char.current_node and not getattr(char.current_node, 'owner', None) and char.current_node.owner_character_id:
                await session.refresh(char.current_node, ['owner'])

            
            node = char.current_node
            
            # --- INTEL DISCOVERY FILTER ---
            disc_stmt = select(DiscoveryRecord).where(DiscoveryRecord.character_id == char.id, DiscoveryRecord.node_id == node.id)
            disc = (await session.execute(disc_stmt)).scalars().first()
            intel = disc.intel_level if disc else "NONE"
            
            # Check expiration
            from datetime import datetime, timezone
            if intel == "PROBE" and disc.intel_expires_at and datetime.now(timezone.utc) > disc.intel_expires_at:
                intel = "NONE"

            # Auto-discover current node
            if intel == "NONE":
                intel = "EXPLORE" 
                if not disc:
                    session.add(DiscoveryRecord(character_id=char.id, node_id=node.id, intel_level="EXPLORE"))
                else:
                    disc.intel_level = "EXPLORE"
                    disc.intel_expires_at = None
                await session.commit()

            # v2.0 Cardinal Exit Mapping
            exits = []
            directions = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
            for d, _ in directions.items():
                exits.append(d) # In v2.0, all cardinal moves are technically exits

            # Tiered Data Masking
            desc = node.description if intel in ["EXPLORE", "PROBE"] else "DATA_ENCRYPTED: Explore node to decrypt."
            power_info = {
                'power_stored': node.power_stored if intel == "PROBE" else -1,
                'power_consumed': node.power_consumed if intel == "PROBE" else -1,
                'power_generated': node.power_generated if intel == "PROBE" else -1,
            }

            return {
                'name': node.name,
                'x': node.x, 'y': node.y,
                'description': desc,
                'type': node.node_type,
                'region_type': getattr(node, 'region_type', None) or 'VOD',
                'intel_level': intel,
                'exits': exits,
                'credits': char.credits,
                'level': char.level,
                **power_info,
                'owner': node.owner.name if node.owner else "Unclaimed",
                'upgrade_level': node.upgrade_level,
                'durability': node.durability,
                'availability_mode': node.availability_mode,
                'net_affinity': node.net_affinity if intel == "PROBE" else "HIDDEN"
            }

    async def move_player(self, name: str, network: str, direction: str):
        """Move 1 sector in global coordinates."""
        move_cost = CONFIG.get('mechanics', {}).get('action_costs', {}).get('move', 1.0)
        async with self.async_session() as session:
            char = await self.get_character_by_nick(name, network, session)
            if not char or not char.current_node: return None, "System offline."
            
            node = char.current_node
            
            # 1. Coordinate Math
            dx, dy = 0, 0
            d = direction.lower()
            if d in ['north', 'n']: dy = -1
            elif d in ['south', 's']: dy = 1
            elif d in ['east', 'e']: dx = 1
            elif d in ['west', 'w']: dx = -1
            elif d in ['northeast', 'ne']: dx = 1; dy = -1
            elif d in ['northwest', 'nw']: dx = -1; dy = -1
            elif d in ['southeast', 'se']: dx = 1; dy = 1
            elif d in ['southwest', 'sw']: dx = -1; dy = 1
            
            if dx != 0 or dy != 0:
                if node.x is None or node.y is None:
                    return None, "POSITION ERROR: Current sector coordinates are indeterminate."
                tx, ty = node.x + dx, node.y + dy
                bounds_res = (await session.execute(select(func.max(GridNode.x), func.max(GridNode.y)))).first()
                max_w = (bounds_res[0] + 1) if bounds_res and bounds_res[0] is not None else 50
                max_h = (bounds_res[1] + 1) if bounds_res and bounds_res[1] is not None else 50
                if tx < 0 or tx >= max_w or ty < 0 or ty >= max_h:
                    return None, f"BOUNDARY ERROR: Global grid terminus reached at ({tx}, {ty})."
                
                # Find Target Node
                stmt_target = select(GridNode).where(GridNode.x == tx, GridNode.y == ty)
                target = (await session.execute(stmt_target)).scalars().first()
                if not target: return None, f"VOID ERROR: Sector ({tx}, {ty}) is unallocated."
                
                # Logic Gate: Sector Unlock
                if not target.is_unlocked and target.owner_character_id != char.id:
                    return None, f"ACCESS DENIED: Sector ({tx}, {ty}) is currently OFFLINE. Grid expansion required."

                # Power Check
                if char.power < move_cost:
                    return None, f"Insufficient POWER. Need {move_cost} uP."
                
                char.node_id = target.id
                char.power -= move_cost
                
                # AUTO-DISCOVERY
                disc_stmt = select(DiscoveryRecord).where(DiscoveryRecord.character_id == char.id, DiscoveryRecord.node_id == target.id)
                if not (await session.execute(disc_stmt)).scalars().first():
                    session.add(DiscoveryRecord(character_id=char.id, node_id=target.id, intel_level="EXPLORE"))
                
                await session.commit()
                msg = f"Traversed {direction} to {target.name} ({tx}, {ty}). (-{move_cost} power)"
                return target.name, msg

            # 3. Bridge Traversal Logic (The Uplink - Task 021)
            # If direction matches the affinity of the node, we are attempting to "jump" networks
            if char.current_node.net_affinity and direction.lower() == char.current_node.net_affinity.lower():
                target_net = char.current_node.net_affinity
                
                # Check for NET_BRIDGE (NET) Hardware on Current Node
                addons = json.loads(char.current_node.addons_json or "{}")
                if not addons.get("NET"):
                    return None, "BRIDGE OFFLINE: Local network entry requires 'NET_BRIDGE' hardware module to be online."

                if not CONFIG.get('networks', {}).get(target_net.lower(), {}).get('enabled'):
                    return None, f"CONNECTION REFUSED: Remote network '{target_net}' is offline."
                
                # Bridging requires local node to be OPEN or owned
                if char.current_node.availability_mode == 'CLOSED' and char.current_node.owner_character_id != char.id:
                    return None, f"BRIDGE ACCESS DENIED: Local gateway '{char.current_node.name}' is CLOSED. Breach required."

                # Find landing node on the target network (one that points BACK to this network)
                stmt_entry = select(GridNode).where(GridNode.net_affinity.ilike(network))
                entry_node = (await session.execute(stmt_entry)).scalars().first()
                if not entry_node:
                    return None, f"ROUTING ERROR: No landing sector found on network '{target_net}'."

                # Bridge Cost: 2x Move Cost
                bridge_cost = move_cost * 2
                if char.power < bridge_cost:
                    return None, f"Insufficient POWER. Bridge jump requires {bridge_cost} uP."

                char.node_id = entry_node.id
                char.power -= bridge_cost

                # --- AUTO-DISCOVERY ON BRIDGE ---
                disc_stmt = select(DiscoveryRecord).where(DiscoveryRecord.character_id == char.id, DiscoveryRecord.node_id == entry_node.id)
                if not (await session.execute(disc_stmt)).scalars().first():
                    session.add(DiscoveryRecord(character_id=char.id, node_id=entry_node.id, intel_level="EXPLORE"))

                await session.commit()
                
                msg = f"BRIDGE ESTABLISHED: Pivoted to {target_net} grid at sector {entry_node.name}. (-{move_cost*2} power)"
                if entry_node.availability_mode == 'CLOSED':
                    msg += " [GRID][SITREP] STATUS=CLOSED | MSG=Grid is CLOSED. Exploration or more required to open it."
                
                return entry_node.name, msg

            return None, f"No valid route found for '{direction}'."

    async def get_grid_stats(self) -> str:
        """Returns aggregate statistics for the entire grid."""
        async with self.async_session() as session:
            # 1. Total Nodes
            total_nodes = await session.scalar(select(func.count(GridNode.id)))
            # 2. Claimed Nodes
            claimed_nodes = await session.scalar(select(func.count(GridNode.id)).where(GridNode.owner_character_id != None))
            # 3. Total Stored Power
            total_power = await session.scalar(select(func.sum(GridNode.power_stored)))
            # 4. Total Credits Pool
            total_credits = await session.scalar(select(func.sum(RaidTarget.credits_pool)))
            
            return f"GRID_STATS | Total_Nodes: {total_nodes} | Claimed: {claimed_nodes} | Global_Power: {total_power or 0.0:.1f} uP | Global_Credits: {total_credits or 0.0:.1f}c"

    async def move_player_to_node(self, name: str, network: str, node_name: str) -> bool:
        async with self.async_session() as session:
            char = await self.get_character_by_nick(name, network, session)
            if not char: return False
            node = (await session.execute(
                select(GridNode).where(GridNode.name == node_name)
            )).scalars().first()
            if not node: return False
            char.node_id = node.id
            await session.commit()
            return True
