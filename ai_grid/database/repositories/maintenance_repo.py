import json
import datetime
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import func
from ai_grid.models import GridNode, Character, Player, NetworkAlias
from ai_grid.database.core import logger
from ai_grid.database.base_repo import BaseRepository

class MaintenanceRepository(BaseRepository):
    async def tick_grid_power(self):
        async with self.async_session() as session:
            stmt = select(GridNode).options(selectinload(GridNode.characters_present))
            nodes = (await session.execute(stmt)).scalars().all()
            for node in nodes:
                occupants = len(node.characters_present)
                # 1. Noise Decay
                if node.noise > 0:
                    node.noise = max(0.0, node.noise - 0.5)

                # 2. Power and Stability
                if node.owner_character_id:
                    addons = json.loads(node.addons_json or "{}")
                    # AMP Logic: 100% boost (2.0x) at Level 1, 200% boost (3.0x) at Level 2+
                    amp_bonus = 1.0
                    if addons.get("AMP"):
                        amp_bonus = 2.0 if node.upgrade_level == 1 else 3.0
                    multiplier = amp_bonus
                    
                    if occupants > 0:
                        reward = occupants * 5.0 * multiplier
                        node.power_generated += reward
                        node.power_stored += reward
                        node.durability = min(100.0, node.durability + (occupants * 2.0))
                    else:
                        node.power_stored += 1.0 * multiplier
                        node.durability = min(100.0, node.durability + 1.0)
                else:
                    node.durability -= 5.0
                    if node.durability <= 0:
                        if node.upgrade_level > 1:
                            node.upgrade_level -= 1
                            node.durability = 100.0
                        else:
                            node.upgrade_level = 1
                node.durability = min(100.0, node.durability)
            await session.commit()

    async def get_grid_telemetry(self) -> dict:
        """Returns aggregate metrics for the entire grid."""
        async with self.async_session() as session:
            all_nodes = (await session.execute(select(GridNode))).scalars().all()
            total_count = len(all_nodes)
            claimed_nodes = [n for n in all_nodes if n.owner_character_id is not None]
            total_power = sum(n.power_stored for n in all_nodes)
            total_gen = sum(n.power_generated for n in all_nodes)
            
            return {
                "total_nodes": total_count,
                "claimed_nodes": len(claimed_nodes),
                "total_power": total_power,
                "total_generation": total_gen,
                "claimed_percent": (len(claimed_nodes) / total_count * 100) if total_count > 0 else 0
            }

    async def tick_retention_policy(self, config: dict) -> tuple:
        """Perform unified decay and pruning for all characters based on inactivity."""
        ret = config.get('mechanics', {}).get('retention', {})
        decay_thresh = ret.get('decay_days_threshold', 14)
        decay_rate = ret.get('decay_rate_percent', 0.05)
        prune_base = ret.get('pruning_base_days', 45)
        prune_bonus = ret.get('pruning_bonus_days_per_level', 30)
        
        async with self.async_session() as session:
            stmt = select(Character).where(Character.status == 'ACTIVE')
            chars = (await session.execute(stmt)).scalars().all()
            
            now = datetime.datetime.now(datetime.timezone.utc)
            decay_applied, pruned_count = 0, 0
            
            for char in chars:
                if not char.last_seen_at: continue
                last_seen = char.last_seen_at.replace(tzinfo=datetime.timezone.utc) if not char.last_seen_at.tzinfo else char.last_seen_at
                days_absent = (now - last_seen).days
                
                # Apply Decay
                if days_absent >= decay_thresh:
                    weeks_over = (days_absent - decay_thresh) // 7 + 1
                    full_decay = (1 - decay_rate) ** weeks_over
                    char.credits = round(char.credits * full_decay, 2)
                    char.xp = int(char.xp * full_decay)
                    char.total_chat_messages = int(char.total_chat_messages * full_decay)
                    decay_applied += 1
                
                # Apply Pruning (Scaled Timeout)
                timeout_days = prune_base + (char.level * prune_bonus)
                if days_absent > timeout_days:
                    char.status = 'DELETED' 
                    pruned_count += 1
            
            await session.commit()
            return decay_applied, pruned_count

    async def tick_player_maintenance(self, network: str, idlers: list):
        """
        Handles periodic resource decay and recovery for players.
        - Stability decay scaled to approx 1% per 24h.
        - Recovery for idlers in safezones or owned nodes.
        """
        async with self.async_session() as session:
            # scaled to approx 1% per 24h. If this runs hourly, it's 0.01 / 24 per tick.
            decay_rate = 0.01 / 24.0
            
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                NetworkAlias.network_name == network
            ).distinct().options(
                selectinload(Character.current_node),
                selectinload(Character.player).selectinload(Player.aliases)
            )
            result = await session.execute(stmt)
            characters = result.scalars().unique().all()
            
            idlers_lower = {i.lower() for i in idlers}
            for char in characters:
                alias_nicks = [a.nickname.lower() for a in char.player.aliases if a.network_name == network] if (char.player and char.player.aliases) else []
                is_idling = (char.name.lower() in idlers_lower) or any(a in idlers_lower for a in alias_nicks)
                node = char.current_node
                is_claimed_owner_idle = is_idling and node and (node.owner_character_id == char.id)

                # Apply base stability decay unless idling at a claimed node
                if not is_claimed_owner_idle:
                    char.stability = max(0.0, char.stability - (char.stability * decay_rate))
                
                # Recovery for active idlers
                if is_idling:
                    is_safe = node and (node.node_type == 'safezone' or node.owner_character_id == char.id)
                    
                    if is_safe:
                        # Recover 5% Power and 2% Stability per tick
                        char.power += 5.0
                        char.stability = min(100.0, char.stability + 2.0)
                    else:
                        # Recover 2% Power, 0.5% Stability in void
                        char.power += 2.0
                        char.stability = min(100.0, char.stability + 0.5)
            
            await session.commit()
