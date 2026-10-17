# combat_repo.py
import random
from datetime import datetime, timezone
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import func, update
from ai_grid.models import Character, Player, NetworkAlias, GridNode, InventoryItem, ItemTemplate, Bounty
from ai_grid.database.core import logger, MOB_ROSTER, LOOT_TABLE, CONFIG, increment_daily_task
from ai_grid.grid_utils import calculate_elo_change

from ai_grid.database.base_repo import BaseRepository

class CombatRepository(BaseRepository):
    def __init__(self, async_session):
        super().__init__(async_session)
        self.MOB_ROSTER = MOB_ROSTER

    async def _eject_character(self, session, char):
        """Relocates a character to the Grid Nexus (Spawn)."""
        uplink = (await session.execute(
            select(GridNode).where(GridNode.is_spawn_node == True)
        )).scalars().first()
        if uplink:
            char.node_id = uplink.id
            # v1.8.0 HP Formula (Sum * 6 + 20)
            total_stats = char.cpu + char.ram + char.bnd + char.sec + char.alg
            char.current_hp = (total_stats * 6) + 20
            return True
        return False

    async def is_pvp_banned(self, nickname: str, network: str) -> bool:
        """v1.8.0: Checks if a player is under a 10-minute PvP ban from surrendering."""
        async with self.async_session() as session:
            char = await self.get_character_by_nick(nickname, network, session)
            if char and char.last_surrender:
                diff = (datetime.now(timezone.utc) - char.last_surrender.replace(tzinfo=timezone.utc)).total_seconds()
                if diff < 600: # 10 minutes
                    return True
            return False

    async def record_match_result(self, winner_name: str, loser_name: str, network: str, was_surrender: bool = False, winner_up: float = None, loser_up: float = None):
        async with self.async_session() as session:
            winner = await self.get_character_by_nick(winner_name, network, session)
            loser = await self.get_character_by_nick(loser_name, network, session)
            
            if winner and loser:
                # v1.8.0: Combat Rewards
                delta = calculate_elo_change(winner.elo, loser.elo)
                winner.wins += 1
                winner.elo += delta
                
                # Defeated vs Surrendered logic
                if was_surrender:
                    # v1.8.0: Surrender = 50% loss
                    lost_power = loser.power * 0.50
                    lost_data = loser.data_units * 0.50
                    loser.last_surrender = datetime.now(timezone.utc)
                else:
                    # v1.8.0: Defeat = 100% loss
                    lost_power = loser.power
                    lost_data = loser.data_units
                
                loser.power -= lost_power
                loser.data_units -= lost_data
                winner.power += lost_power
                winner.data_units += lost_data
                
                winner.xp += 100 # v1.8.0 flat reward
                winner.credits += 250
                
                loser.losses += 1
                loser.elo = max(0, loser.elo - delta)
                loser.xp += 25
                
                # Level Up logic (v1.8.0 curve: 100 * 1.25^(Level-1))
                while True:
                    xp_threshold = int(100 * (1.25 ** (winner.level - 1)))
                    if winner.xp >= xp_threshold:
                        winner.xp -= xp_threshold
                        winner.level += 1
                        winner.pending_stat_points += 1
                    else:
                        break
                
                # v1.8.0: Persist final Unit Power if provided
                if winner_up is not None: winner.power = winner_up
                if loser_up is not None: loser.power = loser_up
            
            await session.commit()

    async def resolve_mob_encounter(self, name: str, network: str) -> dict:
        spawn_tier = random.randint(1, 3) # Tiered challenge based on RNG
        mob = self.MOB_ROSTER.get(spawn_tier, self.MOB_ROSTER[1])

        async with self.async_session() as session:
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                func.lower(NetworkAlias.nickname) == name.lower(),
                NetworkAlias.network_name == network
            ).options(
                selectinload(Character.inventory).selectinload(InventoryItem.template)
            )
            char = (await session.execute(stmt)).scalars().first()
            if not char:
                stmt2 = select(Character).where(
                    func.lower(Character.name) == name.lower()
                ).options(
                    selectinload(Character.inventory).selectinload(InventoryItem.template)
                )
                char = (await session.execute(stmt2)).scalars().first()
            if not char:
                return {"error": "Character not found"}

            player_roll = random.randint(1, 20) + char.alg
            mob_dc = 10 + spawn_tier * 2
            won = player_roll >= mob_dc

            result = {
                "mob_name":    mob["name"],
                "threat":      spawn_tier,
                "roll":        player_roll,
                "dc":          mob_dc,
                "won":         won,
                "xp_gained":   0,
                "credits_gained": 0,
                "credits_lost":   0,
                "loot":        None,
                "respawned":   False,
            }

            if won:
                char.xp += mob["xp"]
                char.credits += mob["credits"]
                result["xp_gained"] = mob["xp"]
                result["credits_gained"] = mob["credits"]

                while True:
                    # v1.8.0 XP Curve
                    xp_threshold = int(100 * (1.25 ** (char.level - 1)))
                    if char.xp >= xp_threshold:
                        char.xp -= xp_threshold
                        char.level += 1
                        # HP Recalculation on level up (since stats might change or just for safety)
                        total_stats = char.cpu + char.ram + char.bnd + char.sec + char.alg
                        char.current_hp = (total_stats * 6) + 20
                        char.pending_stat_points += 1
                        result["leveled_up"] = True
                    else:
                        break

                if random.random() < 0.20:
                    loot_name = random.choice(LOOT_TABLE)
                    tpl = (await session.execute(
                        select(ItemTemplate).where(ItemTemplate.name == loot_name)
                    )).scalars().first()
                    if tpl:
                        existing = next(
                            (i for i in char.inventory if i.template_id == tpl.id), None
                        )
                        if existing:
                            existing.quantity += 1
                        else:
                            session.add(InventoryItem(character_id=char.id, template_id=tpl.id))
                        result["loot"] = loot_name

                # Post-combat processing
                reward_msg = await increment_daily_task(session, char, "Kill a Grid Bug")
                result["task_reward"] = reward_msg

            else:
                penalty = char.credits * 0.10
                char.credits = max(0.0, char.credits - penalty)
                result["credits_lost"] = round(penalty, 2)

                # Unified Ejection Logic
                result["respawned"] = await self._eject_character(session, char)

            await session.commit()
            return result

    async def grid_attack(self, attacker_name, target_name, network):
        async with self.async_session() as session:
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                Character.name.in_([attacker_name, target_name]),
                NetworkAlias.network_name == network
            ).options(
                selectinload(Character.current_node),
                selectinload(Character.inventory),
                selectinload(Character.skills)
            )
            result = (await session.execute(stmt)).scalars().all()
            
            attacker, target = None, None
            for c in result:
                if c.name.lower() == attacker_name.lower(): attacker = c
                if c.name.lower() == target_name.lower(): target = c
                
            if not attacker or not target: return False, "Target not found on this network.", None
            if attacker.node_id != target.node_id: return False, "You must be in the same Network Node as your target.", None
            if not target.current_node or target.current_node.node_type == "safezone": return False, "Combat is strictly prohibited in this zone.", None
            if attacker.id == target.id: return False, "Self-termination is illogical.", None
            
            # Phase 2: Power Consumption
            cost = CONFIG.get('mechanics', {}).get('action_costs', {}).get('attack', 2.0)
            if attacker.power < cost:
                return False, f"Insufficient POWER. Need {cost:.1f} uP.", None
            attacker.power -= cost
            
            evade_roll = random.randint(1, 100)
            if evade_roll <= (target.bnd * 2):
                await session.commit()
                return True, f"{attacker.name} swung wildly at {target.name}, but they evaded!", None
                
            raw_dmg = (attacker.cpu * 5) + attacker.ram
            attacker_skills = {s.skill_name: s.level for s in attacker.skills} if attacker.skills else {}
            target_skills = {s.skill_name: s.level for s in target.skills} if target.skills else {}
            if attacker_skills.get("attack", 0) > 0:
                raw_dmg = int(raw_dmg * (1.0 + 0.10 * attacker_skills["attack"]))

            final_dmg = max(1, raw_dmg - target.sec)
            if target_skills.get("defend", 0) > 0:
                final_dmg = max(1, int(final_dmg * (1.0 - 0.10 * target_skills["defend"])))
            if random.randint(1, 100) <= attacker.alg: final_dmg *= 2 
            
            target.current_hp -= final_dmg
            target.stability = max(0.0, target.stability - (final_dmg * 0.5)) # Structural damage
            if target.current_hp <= 0:
                looted = target.credits * 0.10
                target.credits -= looted
                attacker.credits += looted
                
                # Check and claim active bounty if exists
                stmt_bounty = select(Bounty).where(
                    func.lower(Bounty.target_nick).in_([target.name.lower(), target_name.lower()]),
                    func.lower(Bounty.network_name) == network.lower(),
                    Bounty.status == "ACTIVE"
                )
                bounty = (await session.execute(stmt_bounty)).scalars().first()
                bounty_msg = None
                is_self = (attacker.id == target.id) or bool(attacker.player_id and target.player_id and attacker.player_id == target.player_id)
                if bounty and not is_self and bounty.status == "ACTIVE":
                    stmt_claim = (
                        update(Bounty)
                        .where(Bounty.id == bounty.id, Bounty.status == "ACTIVE")
                        .values(
                            status="CLAIMED",
                            claimed_by=attacker.name,
                            claimed_at=datetime.now(timezone.utc)
                        )
                    )
                    res_claim = await session.execute(stmt_claim)
                    if res_claim.rowcount == 1:
                        attacker.credits += bounty.reward_credits
                        bounty_msg = f" [Bounty Claimed: +{bounty.reward_credits:.1f}c]"

                # Unified Ejection Logic
                await self._eject_character(session, target)
                
                await session.commit()
                extra_str = bounty_msg or ""
                return True, f"{attacker.name} struck {target.name} for {final_dmg} DMG! {target.name} flatlines... Ejected to Spawn.{extra_str}", None
                
            await session.commit()
            total_stats = target.cpu + target.ram + target.bnd + target.sec + target.alg
            max_hp = (total_stats * 6) + 20
            return True, f"{attacker.name} struck {target.name} for {final_dmg} DMG! ({target.current_hp}/{max_hp} HP)", None

    async def grid_hack(self, attacker_name, target_name, network):
        async with self.async_session() as session:
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                Character.name.in_([attacker_name, target_name]),
                NetworkAlias.network_name == network
            ).options(selectinload(Character.current_node))
            result = (await session.execute(stmt)).scalars().all()
            
            attacker, target = None, None
            for c in result:
                if c.name.lower() == attacker_name.lower(): attacker = c
                if c.name.lower() == target_name.lower(): target = c
                
            if not attacker or not target: return False, "Target not found on this network.", None
            if attacker.node_id != target.node_id: return False, "Target is not in your current sector.", None
            if target.current_node and target.current_node.node_type == "safezone": return False, "MCP prevents hacking in safezones.", None
            if attacker.id == target.id: return False, "Self-termination is illogical.", None
            
            # Phase 2: Power Consumption
            cost = CONFIG.get('mechanics', {}).get('action_costs', {}).get('hack', 3.0)
            if attacker.power < cost:
                return False, f"MCP trace active. You need {cost:.1f} power to safely breach.", None
            attacker.power -= cost
            
            try:
                roll = random.randint(1, 20) + attacker.alg
                dc = 10 + target.sec
                if roll >= dc:
                    looted = target.credits * 0.05
                    target.credits -= looted
                    attacker.credits += looted
                    reward_msg = await increment_daily_task(session, attacker, "Hack a Player")
                    await session.commit()
                    msg = f"Hack Successful! {attacker.name} breached {target.name}'s firewall and siphoned {looted:.2f}c."
                    return True, msg, reward_msg
                else:
                    attacker.credits = max(0.0, attacker.credits - 50.0)
                    await session.commit()
                    return False, f"Hack Failed. {target.name}'s MCP traced the intrusion. {attacker.name} is fined 50c!", None
            except Exception as e:
                await session.rollback()
                logger.error(f"Error executing grid_hack: {e}")
                return False, "Hack failed due to a system malfunction.", None

    async def grid_rob(self, attacker_name, target_name, network):
        async with self.async_session() as session:
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                Character.name.in_([attacker_name, target_name]),
                NetworkAlias.network_name == network
            ).options(
                selectinload(Character.current_node),
                selectinload(Character.inventory)
            )
            result = (await session.execute(stmt)).scalars().all()
            
            attacker, target = None, None
            for c in result:
                if c.name.lower() == attacker_name.lower(): attacker = c
                if c.name.lower() == target_name.lower(): target = c
                
            if not attacker or not target: return False, "Target not found on this network.", None
            if attacker.node_id != target.node_id: return False, "Target is not in your locale.", None
            if target.current_node and target.current_node.node_type == "safezone": return False, "No physical theft allowed here.", None
            if attacker.id == target.id: return False, "Cannot rob yourself.", None
            if not target.inventory: return False, f"{target.name}'s pockets are empty.", None
            
            try:
                roll = random.randint(1, 20) + attacker.bnd
                dc = 10 + target.bnd
                if roll >= dc:
                    item_to_steal = random.choice(target.inventory)
                    item_name = item_to_steal.template.name if getattr(item_to_steal, 'template', None) else "item"
                    item_to_steal.character_id = attacker.id
                    await session.commit()
                    return True, f"Sleight of hand successful! {attacker.name} lifted an item from {target.name}.", f"Looted: {item_name}"
                else:
                    return False, f"{attacker.name} clumsily attempted to rob {target.name} and was caught!", None
            except Exception as e:
                await session.rollback()
                logger.error(f"Error executing grid_rob: {e}")
                return False, "Theft attempt failed due to an unexpected disturbance.", None

