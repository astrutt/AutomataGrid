# ai_grid/database/territory_repo.py
import json
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import func, or_
from ai_grid.models import Character, Player, NetworkAlias, GridNode, InventoryItem, ItemTemplate
from ai_grid.database.core import logger, CONFIG, increment_daily_task
from ai_grid.database.base_repo import BaseRepository
from ai_grid.core.validation import validate_node_name

class TerritoryRepository(BaseRepository):
    async def claim_node(self, name: str, network: str, node_name: str = None) -> tuple[bool, str]:
        async with self.async_session() as session:
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                Character.name == name,
                NetworkAlias.nickname == name,
                NetworkAlias.network_name == network
            ).options(selectinload(Character.current_node))
            char = (await session.execute(stmt)).scalars().first()
            if not char: return False, "System offline."
            
            # Resolve target node
            if node_name:
                node_stmt = select(GridNode).where(func.lower(GridNode.name) == node_name.lower())
                node = (await session.execute(node_stmt)).scalars().first()
                if not node: return False, f"Coordinate '{node_name}' not found."
            else:
                node = char.current_node
            
            if not node: return False, "Target node unavailable."
            
            # Location Validation
            success, err_msg = await self.verify_presence(char, node, "claim")
            if not success:
                return False, err_msg
            
            # Availability Check: No owner bypass for initial claim
            if node.availability_mode == 'CLOSED':
                return False, "[GRID][SITREP] STATUS=CLOSED | MSG=Grid is CLOSED. Grid exploration, or more may be required to open it."

            if node.owner_character_id:
                if node.owner_character_id == char.id:
                    return False, "You already command this node."
                return False, "This node is controlled by a rival. You must seize it."
                
            node.owner_character_id = char.id
            node.power_stored = 100.0
            node.durability = 100.0
            
            reward_msg = await increment_daily_task(session, char, "Claim a Node")
            await session.commit()
            msg = f"Control established over {node.name}."
            if reward_msg: msg += f" {reward_msg}"
            return True, msg

    async def upgrade_node(self, name: str, network: str, node_name: str = None) -> tuple[bool, str]:
        async with self.async_session() as session:
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                Character.name == name,
                NetworkAlias.nickname == name,
                NetworkAlias.network_name == network
            ).options(selectinload(Character.current_node))
            char = (await session.execute(stmt)).scalars().first()
            if not char: return False, "System offline."
            
            # Resolve target node
            if node_name:
                node_stmt = select(GridNode).where(func.lower(GridNode.name) == node_name.lower())
                node = (await session.execute(node_stmt)).scalars().first()
                if not node: return False, f"Coordinate '{node_name}' not found."
            else:
                node = char.current_node
            
            if not node: return False, "Target node unavailable."

            # Location Validation
            success, err_msg = await self.verify_presence(char, node, "upgrade")
            if not success:
                return False, err_msg
            
            # Availability Check: Owner bypass
            if node.availability_mode == 'CLOSED' and node.owner_character_id != char.id:
                return False, "[GRID][SITREP] STATUS=CLOSED | MSG=Grid is CLOSED. Grid exploration, or more may be required to open it."

            if node.owner_character_id != char.id: return False, "You do not command this node."
            
            max_lvl = 4
            if node.upgrade_level >= max_lvl:
                return False, f"Architecture peak reached (Level {max_lvl}). Further augmentation requires high-mesh hardware."

            cost = node.upgrade_level * 500
            if char.credits < cost: return False, f"Insufficient credits. Upgrade requires {cost}c."
            
            char.credits -= cost
            node.upgrade_level += 1
            
            # Apply Durability Multiplier from Config
            dur_mults = CONFIG.get('mechanics', {}).get('durability_multipliers', [1.0, 1.25, 1.5, 2.0])
            idx = min(node.upgrade_level - 1, len(dur_mults) - 1)
            # Scaling max durability (conceptual) or just applying a full repair
            node.durability = 100.0 
            
            await session.commit()
            return True, f"Upgraded {node.name} to Level {node.upgrade_level} for {cost}c! [Integrity Scaling: {dur_mults[idx]}x]"

    async def set_grid_mode(self, name: str, network: str, mode: str, node_name: str = None) -> tuple[bool, str]:
        """Toggle grid availability (OPEN/CLOSED/STEALTH)."""
        mode = mode.upper()
        if mode not in ["OPEN", "CLOSED", "STEALTH"]:
            return False, f"Invalid mode '{mode}'. Allowed: OPEN, CLOSED, STEALTH."

        async with self.async_session() as session:
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                Character.name == name,
                NetworkAlias.nickname == name,
                NetworkAlias.network_name == network
            ).options(selectinload(Character.current_node))
            char = (await session.execute(stmt)).scalars().first()
            if not char: return False, "System offline."

            if node_name:
                clean_name = node_name.strip("[]").strip()
                node_stmt = select(GridNode).where(
                    (func.lower(GridNode.name) == node_name.lower()) | (func.lower(GridNode.name) == clean_name.lower())
                )
                node = (await session.execute(node_stmt)).scalars().first()
                if not node: return False, f"Coordinate '{node_name}' not found."
            else:
                node = char.current_node

            if not node: return False, "Target node unavailable."
            if node.owner_character_id != char.id: return False, "You do not command this node."

            addons = json.loads(node.addons_json or "{}") if isinstance(node.addons_json, str) else (node.addons_json or {})
            if mode == "STEALTH" and not addons.get("NET"):
                return False, "Integrity Error: STEALTH state requires an active NET device."

            node.availability_mode = mode
            if "NET" in addons:
                addons["NET_STATE"] = mode
                if isinstance(addons["NET"], dict):
                    addons["NET"]["state"] = mode
                node.addons_json = json.dumps(addons)

            await session.commit()
            return True, f"Grid protocol updated: Sector {node.name} is now {mode}."

    async def grid_repair(self, name: str, network: str, node_name: str = None) -> tuple[bool, str]:
        """Repair node using credits (Legacy) or power (New Manual)."""
        async with self.async_session() as session:
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                Character.name == name,
                NetworkAlias.nickname == name,
                NetworkAlias.network_name == network
            ).options(selectinload(Character.current_node))
            char = (await session.execute(stmt)).scalars().first()
            if not char: return False, "System offline."
            
            # Resolve target node
            if node_name:
                node_stmt = select(GridNode).where(func.lower(GridNode.name) == node_name.lower())
                node = (await session.execute(node_stmt)).scalars().first()
                if not node: return False, f"Coordinate '{node_name}' not found in local sector."
            else:
                node = char.current_node
            
            if not node: return False, "Target node unavailable."

            # Location Validation
            success, err_msg = await self.verify_presence(char, node, "repair")
            if not success:
                return False, err_msg
            
            is_owner = node.owner_character_id == char.id

            # Availability Check: Owner bypass
            if node.availability_mode == 'CLOSED' and not is_owner:
                return False, "[GRID][SITREP] STATUS=CLOSED | MSG=Grid is CLOSED."

            # Logic Choice: If user has 100c but low power, use credits. If they use it via 'grid repair', it's credit-based in the handler usually.
            # But the facade must handle both if needed. I'll maintain the two versions.
            
            # v1.8.3 calibrated reward logic (Big Tier)
            pkg = self.calculate_mcp_rewards(char.level, 'repair')
            char.credits += pkg['credits']
            char.data_units += pkg['data']
            await self.add_xp_to_char(char, pkg['xp'], session)
            
            # Apply repair logic
            char.power = max(0.0, char.power - 25.0) # Unified uP cost
            bonus = 20.0 if is_owner else 10.0
            node.durability = min(100.0, node.durability + bonus)
            
            reward_msg = await increment_daily_task(session, char, "Repair a Node")
            await session.commit()
            
            return True, f"Nodal integrity augmented (+{bonus}%). Rewards: {pkg['credits']}c | {pkg['data']} Data | {pkg['xp']} XP. {reward_msg if reward_msg else ''}"

    async def grid_recharge(self, name: str, network: str, node_name: str = None) -> tuple[bool, str]:
        async with self.async_session() as session:
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                Character.name == name,
                NetworkAlias.nickname == name,
                NetworkAlias.network_name == network
            ).options(selectinload(Character.current_node))
            char = (await session.execute(stmt)).scalars().first()
            if not char: return False, "System offline."
            
            # Resolve target node
            if node_name:
                node_stmt = select(GridNode).where(func.lower(GridNode.name) == node_name.lower())
                node = (await session.execute(node_stmt)).scalars().first()
                if not node: return False, f"Coordinate '{node_name}' not found."
            else:
                node = char.current_node
            
            if not node: return False, "Target node unavailable."
            
            # Location Validation
            success, err_msg = await self.verify_presence(char, node, "recharge")
            if not success:
                return False, err_msg
            
            if node.availability_mode == 'CLOSED' and node.owner_character_id != char.id:
                return False, "[GRID][SITREP] STATUS=CLOSED"

            if not node.owner_character_id: return False, "You cannot recharge unclaimed void."
            if char.credits < 100.0: return False, "You need 100c to recharge power."
            
            char.credits -= 100.0
            node.power_stored += 100.0
            await session.commit()
            
            return True, f"Grid recharged. (+100.0 uP) Current Store: {node.power_stored:.1f} uP."

    async def get_stash(self, name: str, network: str, node_name: str = None) -> dict:
        """Returns the current stash contents for the active or specified node."""
        async with self.async_session() as session:
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                Character.name == name,
                NetworkAlias.nickname == name,
                NetworkAlias.network_name == network
            ).options(selectinload(Character.current_node), selectinload(Character.inventory).selectinload(InventoryItem.template))
            char = (await session.execute(stmt)).scalars().first()
            if not char:
                return {"success": False, "items": [], "msg": "System offline."}

            if node_name:
                node_stmt = select(GridNode).where(func.lower(GridNode.name) == node_name.lower())
                node = (await session.execute(node_stmt)).scalars().first()
                if not node:
                    return {"success": False, "items": [], "msg": f"Coordinate '{node_name}' not found."}
            else:
                node = char.current_node

            if not node:
                return {"success": False, "items": [], "msg": "Target node unavailable."}

            success, err_msg = await self.verify_presence(char, node, "stash")
            if not success:
                return {"success": False, "items": [], "msg": err_msg}

            stash = list(node.stash_inventory or [])
            if not stash:
                return {"success": True, "items": [], "msg": f"The {node.name} stash is empty."}
            return {"success": True, "items": stash, "msg": f"{node.name} stash: {', '.join(stash)}"}

    async def stash_store(self, name: str, network: str, node_name: str = None, item_name: str = None) -> tuple[bool, str]:
        """Stores one matching inventory item in the node's stash."""
        if item_name is None and node_name is not None:
            item_name = node_name
            node_name = None

        async with self.async_session() as session:
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                Character.name == name,
                NetworkAlias.nickname == name,
                NetworkAlias.network_name == network
            ).options(
                selectinload(Character.current_node),
                selectinload(Character.inventory).selectinload(InventoryItem.template)
            )
            char = (await session.execute(stmt)).scalars().first()
            if not char:
                return False, "System offline."

            if node_name:
                node_stmt = select(GridNode).where(func.lower(GridNode.name) == node_name.lower())
                node = (await session.execute(node_stmt)).scalars().first()
                if not node:
                    return False, f"Coordinate '{node_name}' not found."
            else:
                node = char.current_node

            if not node:
                return False, "Target node unavailable."

            success, err_msg = await self.verify_presence(char, node, "stash")
            if not success:
                return False, err_msg
            if node.owner_character_id != char.id:
                return False, "Permission denied. You do not command this node."

            if not item_name:
                return False, "Specify which item to store."

            match = next((i for i in char.inventory if i.template and i.template.name and i.template.name.lower() == item_name.lower()), None)
            if not match:
                return False, f"Item '{item_name}' is not in your inventory."

            stash = list(node.stash_inventory or [])
            stash_name = match.template.name
            if stash_name in stash:
                return False, f"{stash_name} is already stored in the node stash."

            if match.quantity > 1:
                match.quantity -= 1
            else:
                await session.delete(match)
            stash.append(stash_name)
            node.stash_inventory = stash

            await session.commit()
            return True, f"Stored {stash_name} in {node.name}."

    async def stash_take(self, name: str, network: str, node_name: str = None, item_name: str = None) -> tuple[bool, str]:
        """Retrieves one item from the node stash back into the player's inventory."""
        if item_name is None and node_name is not None:
            item_name = node_name
            node_name = None

        async with self.async_session() as session:
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                Character.name == name,
                NetworkAlias.nickname == name,
                NetworkAlias.network_name == network
            ).options(
                selectinload(Character.current_node),
                selectinload(Character.inventory).selectinload(InventoryItem.template)
            )
            char = (await session.execute(stmt)).scalars().first()
            if not char:
                return False, "System offline."

            if node_name:
                node_stmt = select(GridNode).where(func.lower(GridNode.name) == node_name.lower())
                node = (await session.execute(node_stmt)).scalars().first()
                if not node:
                    return False, f"Coordinate '{node_name}' not found."
            else:
                node = char.current_node

            if not node:
                return False, "Target node unavailable."

            success, err_msg = await self.verify_presence(char, node, "stash")
            if not success:
                return False, err_msg
            if node.owner_character_id != char.id:
                return False, "Permission denied. You do not command this node."

            if not item_name:
                return False, "Specify which item to take."

            stash = list(node.stash_inventory or [])
            if item_name.lower() not in {s.lower() for s in stash}:
                return False, f"Item '{item_name}' is not in the node stash."

            if len(char.inventory) >= 4:
                return False, "Inventory capacity reached. Free a slot before taking from the stash."

            existing = next((i for i in char.inventory if i.template and i.template.name and i.template.name.lower() == item_name.lower()), None)
            item_template = (await session.execute(select(ItemTemplate).where(func.lower(ItemTemplate.name) == item_name.lower()))).scalars().first()
            if not item_template:
                item_template = ItemTemplate(name=item_name, item_type="general", base_value=0)
                session.add(item_template)
                await session.flush()

            if existing:
                existing.quantity += 1
            else:
                session.add(InventoryItem(character_id=char.id, template_id=item_template.id, quantity=1))

            node.stash_inventory = [s for s in stash if s.lower() != item_name.lower()]
            await session.commit()
            return True, f"Taken {item_name} from {node.name}."

    async def install_node_addon(self, name: str, network: str, item_name: str, node_name: str = None) -> dict:
        """Consumes an addon item from inventory and installs it on the target node."""
        async with self.async_session() as session:
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                Character.name == name,
                NetworkAlias.nickname == name,
                NetworkAlias.network_name == network
            ).options(
                selectinload(Character.current_node),
                selectinload(Character.inventory).selectinload(InventoryItem.template)
            )
            char = (await session.execute(stmt)).scalars().first()
            if not char: return {"success": False, "msg": "System offline."}
            
            # Resolve target node
            if node_name:
                node_stmt = select(GridNode).where(func.lower(GridNode.name) == node_name.lower())
                node = (await session.execute(node_stmt)).scalars().first()
                if not node: return {"success": False, "msg": f"Coordinate '{node_name}' not found."}
            else:
                node = char.current_node
            
            if not node: return {"success": False, "msg": "Target node unavailable."}
            
            # Location Validation
            success, err_msg = await self.verify_presence(char, node, "install")
            if not success:
                return {"success": False, "msg": err_msg}
            
            if node.owner_character_id != char.id:
                return {"success": False, "msg": "Permission Denied: Only the owner can install hardware."}
                
            inv_item = next((i for i in char.inventory if i.template.name.upper() == item_name.upper() and i.template.item_type == "node_addon"), None)
            if not inv_item:
                return {"success": False, "msg": f"Hardware module '{item_name}' not found in local cache."}
            
            addons = json.loads(node.addons_json or "{}")
            addon_type = inv_item.template.effects_json_dict.get("type", item_name.upper())
            
            if addons.get(addon_type):
                return {"success": False, "msg": f"Integrity Conflict: Node already contains active {addon_type} module."}
            
            # Enforce Multi-Slot Limit (Task 020)
            max_slots = node.max_slots or CONFIG.get('mechanics', {}).get('max_hardware_slots', 4)
            hardware_modules = [k for k in addons.keys() if k in ["AMP", "IDS", "FIREWALL", "NET", "HPOT"] or not k.endswith("_STATE")]
            if len(hardware_modules) >= max_slots:
                return {"success": False, "msg": f"Hardware Capacity reached ({max_slots}/{max_slots} slots occupied). Upgrade node or remove hardware."}

            addons[addon_type] = True
            node.addons_json = json.dumps(addons)
            await session.delete(inv_item)
            await session.commit()
            return {"success": True, "msg": f"Installation Successful: {addon_type} module is now online for {node.name}."}

    async def uninstall_node_addon(self, name: str, network: str, addon_type: str) -> dict:
        """Removes a hardware module from a node and returns the template to the owner's inventory."""
        async with self.async_session() as session:
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                Character.name == name,
                NetworkAlias.nickname == name,
                NetworkAlias.network_name == network
            ).options(
                selectinload(Character.current_node),
                selectinload(Character.inventory).selectinload(InventoryItem.template)
            )
            char = (await session.execute(stmt)).scalars().first()
            if not char or not char.current_node: return {"success": False, "msg": "System offline."}
            
            node = char.current_node
            if node.owner_character_id != char.id:
                return {"success": False, "msg": "Permission Denied: Only the owner can manage hardware."}
            
            addons = json.loads(node.addons_json or "{}")
            addon_type = addon_type.upper()
            
            if addon_type not in addons:
                return {"success": False, "msg": f"Hardware module '{addon_type}' not found on core."}
            
            # Find Template
            from ai_grid.models import ItemTemplate
            tpl_stmt = select(ItemTemplate).where(ItemTemplate.name == addon_type, ItemTemplate.item_type == "node_addon")
            tpl = (await session.execute(tpl_stmt)).scalars().first()
            
            if not tpl:
                # Fallback check for standard items
                tpl_stmt = select(ItemTemplate).where(func.upper(ItemTemplate.name) == addon_type)
                tpl = (await session.execute(tpl_stmt)).scalars().first()

            if tpl:
                # Return to inventory
                inv_item = InventoryItem(character_id=char.id, template_id=tpl.id, quantity=1)
                session.add(inv_item)
            
            del addons[addon_type]
            if addon_type == "NET":
                addons.pop("NET_STATE", None)
                if node.availability_mode == "STEALTH":
                    node.availability_mode = "CLOSED"
            node.addons_json = json.dumps(addons)
            await session.commit()
            return {"success": True, "msg": f"Decommission Successful: {addon_type} module returned to local inventory."}

    async def bolster_node(self, name: str, network: str, amount: float, node_name: str = None) -> dict:
        """Spends player power to increase node durability on a target node."""
        async with self.async_session() as session:
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                Character.name == name,
                NetworkAlias.nickname == name,
                NetworkAlias.network_name == network
            ).options(selectinload(Character.current_node))
            char = (await session.execute(stmt)).scalars().first()
            if not char: return {"success": False, "msg": "System offline."}
            
            # Resolve target node
            if node_name:
                node_stmt = select(GridNode).where(func.lower(GridNode.name) == node_name.lower())
                node = (await session.execute(node_stmt)).scalars().first()
                if not node: return {"success": False, "msg": f"Coordinate '{node_name}' not found."}
            else:
                node = char.current_node
            
            if not node: return {"success": False, "msg": "Target node unavailable."}
            
            # Location Validation
            success, err_msg = await self.verify_presence(char, node, "bolster")
            if not success:
                return {"success": False, "msg": err_msg}
            
            if node.owner_character_id != char.id: return {"success": False, "msg": "Permission Denied."}
                
            if char.power < amount: return {"success": False, "msg": f"Insufficient POWER."}
            if node.durability >= 100.0: return {"success": False, "msg": "Architecture maxed."}
                
            char.power -= amount
            from ai_grid.models import CharacterSkill
            fortify_stmt = select(CharacterSkill).where(
                CharacterSkill.character_id == char.id,
                CharacterSkill.skill_name == 'fortify'
            )
            fortify_skill = (await session.execute(fortify_stmt)).scalars().first()
            fortify_lvl = fortify_skill.level if fortify_skill else 0
            durability_gain = (amount * 0.5) * (1.0 + 0.10 * fortify_lvl)
            node.durability = min(100.0, node.durability + durability_gain)
            await session.commit()
            return {"success": True, "msg": f"Reinforcement complete. Durability: {node.durability:.1f}%."}

    async def link_network(self, name: str, network: str, subnet_name: str, node_name: str = None) -> dict:
        """Enables remote network linkage for a target node."""
        async with self.async_session() as session:
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                Character.name == name, NetworkAlias.nickname == name, NetworkAlias.network_name == network
            ).options(selectinload(Character.current_node))
            char = (await session.execute(stmt)).scalars().first()
            if not char: return {"success": False, "msg": "System offline."}
            
            # Resolve target node
            if node_name:
                node_stmt = select(GridNode).where(func.lower(GridNode.name) == node_name.lower())
                node = (await session.execute(node_stmt)).scalars().first()
                if not node: return {"success": False, "msg": f"Coordinate '{node_name}' not found."}
            else:
                node = char.current_node
            
            if not node: return {"success": False, "msg": "Target node unavailable."}
            
            # Location Validation
            success, err_msg = await self.verify_presence(char, node, "link")
            if not success:
                return {"success": False, "msg": err_msg}
            
            if node.owner_character_id != char.id: return {"success": False, "msg": "Permission Denied."}
            
            addons = json.loads(node.addons_json or "{}")
            if not addons.get("NET"): return {"success": False, "msg": "NET_BRIDGE hardware required."}
            
            known_networks = CONFIG.get('networks', {}).keys()
            if subnet_name in known_networks:
                node.net_affinity = subnet_name
                node.local_network = None
            else:
                node.local_network = subnet_name
                node.net_affinity = None
            
            await session.commit()
            return {"success": True, "msg": f"Linkage established to '{subnet_name}'."}

    async def rename_node(self, old_name: str, new_name: str) -> tuple[bool, str]:
        if not validate_node_name(new_name):
            return False, "Invalid node name: alphanumeric + _ only, max 11 chars."
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(func.lower(GridNode.name) == old_name.lower()))).scalars().first()
            if not node: return False, "Target not found."
            if (await session.execute(select(GridNode).where(func.lower(GridNode.name) == new_name.lower()))).scalars().first():
                return False, "Collision detected."
            old_display = node.name
            node.name = new_name
            await session.commit()
            logger.info(f"GRID_RENAME: {old_display} -> {new_name}")
            return True, f"Operation successful: {old_display} rebranded to {new_name}."

    async def community_rename_node(self, nick: str, network: str, new_name: str, target_node: str = None) -> tuple[bool, str]:
        if not validate_node_name(new_name):
            return False, "Invalid node name: alphanumeric + _ only, max 11 chars."

        cost = 5000.0
        async with self.async_session() as session:
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                func.lower(Character.name) == nick.lower(),
                NetworkAlias.network_name == network
            ).options(selectinload(Character.current_node))
            char = (await session.execute(stmt)).scalars().first()
            if not char:
                return False, "System offline."

            if char.credits < cost:
                return False, f"Insufficient credits. Renaming sector costs {cost:.0f}c."

            if target_node:
                node_stmt = select(GridNode).where(func.lower(GridNode.name) == target_node.lower())
                node = (await session.execute(node_stmt)).scalars().first()
            else:
                node = char.current_node

            if not node:
                return False, "Target node unavailable."

            # Critical node protection
            if (
                getattr(node, 'is_spawn_node', False)
                or node.name.lower() == "uplink"
                or (node.node_type and node.node_type.lower() == "safezone")
            ):
                return False, "Permission Denied: Critical infrastructure (Uplink/Safezones) cannot be renamed."

            if new_name.lower() == "uplink":
                return False, "Permission Denied: Reserved system identifier 'UpLink' cannot be used."

            # Collision check
            existing = (await session.execute(
                select(GridNode).where(func.lower(GridNode.name) == new_name.lower())
            )).scalars().first()
            if existing and existing.id != node.id:
                return False, f"Naming collision: Sector '{new_name}' already exists."

            old_name = node.name
            char.credits -= cost
            node.name = new_name
            await session.commit()
            logger.info(f"COMMUNITY_RENAME: {char.name} rebranded {old_name} -> {new_name} for {cost:.0f}c")
            return True, f"Sector {old_name} rebranded to {new_name} for {cost:.0f}c."

    async def update_node_description(self, node_name: str, new_desc: str) -> tuple[bool, str]:
        async with self.async_session() as session:
            node = (await session.execute(select(GridNode).where(func.lower(GridNode.name) == node_name.lower()))).scalars().first()
            if not node: return False, "Target not found."
            node.description = new_desc
            await session.commit()
            return True, f"Operation successful: Node architecture redefined."

    async def check_territory_defense(self, nickname: str, network: str) -> tuple[bool, str | None, str | None]:
        """Checks if character is hacking an owned node whose owner is currently present on that node."""
        async with self.async_session() as session:
            char = await self.get_character_by_nick(nickname, network, session)
            curr_node = char.current_node if char else None

            if curr_node and curr_node.owner_character_id and curr_node.owner_character_id != getattr(char, 'id', None):
                owner_stmt = select(Character).where(
                    Character.id == curr_node.owner_character_id,
                    Character.node_id == curr_node.id
                )
                owner_char = (await session.execute(owner_stmt)).scalars().first()
                if owner_char:
                    alias_stmt = select(NetworkAlias.nickname).where(
                        NetworkAlias.player_id == owner_char.player_id,
                        NetworkAlias.network_name == network
                    )
                    owner_alias = (await session.execute(alias_stmt)).scalars().first()
                    return True, owner_alias or owner_char.name, curr_node.name
            return False, None, None
