# handlers/combat.py - Combat, PvP & Mini-Game Handlers
import asyncio
import random
import logging
import time
import uuid
from sqlalchemy import func
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from ai_grid.models import Character, Player, NetworkAlias, GridNode
from ai_grid.grid_combat import CombatEngine, Entity
from ai_grid.grid_utils import format_text, tag_msg, C_GREEN, C_CYAN, C_RED, C_YELLOW, C_WHITE
from .base import is_machine_mode, check_rate_limit, get_action_routing

logger = logging.getLogger("manager")

async def handle_mob_encounter(node, nick: str, node_name: str, threat: int, prev_node: str, reply_target: str):
    if threat is None: threat = 1 # Default to Tier 1 for procedural spawns
    mob = node.db.combat.MOB_ROSTER.get(threat, node.db.combat.MOB_ROSTER[1])
    mob_name = mob['name']
    
    private_target, broadcast_chan, machine_mode, reply_method = await get_action_routing(node, nick, reply_target)
    
    msg = f"MOB_DETECTED: {mob_name} (T{threat}) in {node_name}. ENGAGE or FLEE? (15s)"
    await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(msg, C_YELLOW, bold=True, is_machine=machine_mode), action='COMBAT', nick=nick, is_machine=machine_mode)}")
    
    if not machine_mode:
        await node.send(f"PRIVMSG {broadcast_chan} :{tag_msg(format_text(f'{mob_name} detected near {nick} at {node_name}.', C_RED), action='SIGACT', nick=nick)}")
    
    if not hasattr(node, 'pending_encounters') or node.pending_encounters is None:
        node.pending_encounters = {}
    if nick in node.pending_encounters:
        old_timer = node.pending_encounters[nick].get('timer')
        if old_timer and not old_timer.done():
            old_timer.cancel()

    async def auto_engage():
        try:
            await asyncio.sleep(15)
            pending = getattr(node, 'pending_encounters', {}) or {}
            if pending.get(nick, {}).get('timer') is asyncio.current_task():
                await resolve_mob(node, nick, reply_target)
        except asyncio.CancelledError: pass
    timer = asyncio.create_task(auto_engage())
    node.pending_encounters[nick] = {'mob_name': mob_name, 'threat': threat, 'prev_node': prev_node, 'timer': timer, 'reply_target': reply_target}

async def resolve_mob(node, nick: str, reply_target: str):
    if not hasattr(node, 'pending_encounters') or node.pending_encounters is None:
        return
    enc = node.pending_encounters.pop(nick, None)
    if not enc:
        matched_key = next((k for k in node.pending_encounters if k.lower() == nick.lower()), None)
        if matched_key:
            enc = node.pending_encounters.pop(matched_key, None)
    if not enc: return
    if enc.get('timer') and not enc['timer'].done():
        enc['timer'].cancel()
    
    private_target, broadcast_chan, machine_mode, reply_method = await get_action_routing(node, nick, reply_target)
    result = await node.db.resolve_mob_encounter(nick, node.net_name)
    
    if 'error' in result:
        await node.send(f"{reply_method} {private_target} :{tag_msg(result['error'], action='COMBAT', result='ERR', nick=nick, is_machine=machine_mode)}")
        return
    
    if result['won']:
        loot_str = f" Dropped: {result['loot']}!" if result.get('loot') else ""
        lvl_str = f" UP Level Up!" if result.get('leveled_up') else ""
        msg = f"{enc['mob_name']} neutralized! +{result['xp_gained']} XP, +{result['credits_gained']:.1f}c.{loot_str}{lvl_str}"
        await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(msg, C_GREEN, is_machine=machine_mode), action='COMBAT', result='WIN', nick=nick, is_machine=machine_mode)}")
        
        if not machine_mode:
            sigact = format_text(f"{nick} eliminated {enc['mob_name']}! +{result['xp_gained']} XP.", C_YELLOW)
            await node.send(f"PRIVMSG {broadcast_chan} :{tag_msg(sigact, action='SIGACT', nick=nick)}")
        
        if result.get('task_reward'):
            await node.send(f"{reply_method} {private_target} :{tag_msg(result['task_reward'], action='SIGACT', result='TASK', nick=nick, is_machine=machine_mode)}")
    else:
        loss_credits = result['credits_lost']
        msg = f"{enc['mob_name']} overwhelmed you! Lost {loss_credits:.2f}c. Ejected to UpLink."
        await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(msg, C_RED, is_machine=machine_mode), action='COMBAT', result='LOSS', nick=nick, is_machine=machine_mode)}")

from ai_grid.core.validation import validate_quantity

async def start_pvp_combat(node, attacker_name: str, defender_name: str, action: str = "attack", defender_nick: str = None):
    match_id = f"PVP_{int(time.time())}_{uuid.uuid4().hex[:4]}"

    auth_sessions = getattr(node, 'authenticated_sessions', {}) or {}
    recipients = {attacker_name, defender_name}
    if defender_nick:
        recipients.add(defender_nick)
    for nk, cn in auth_sessions.items():
        if cn.lower() in [attacker_name.lower(), defender_name.lower()]:
            recipients.add(nk)
    async def combat_broadcast(msg: str):
        chan = node.config.get('channel', '#arena') if hasattr(node, 'config') else '#arena'
        if hasattr(node, 'send'):
            await node.send(f"PRIVMSG {chan} :{msg}")
            for p in recipients:
                await node.send(f"PRIVMSG {p} :{msg}")

    prefix = getattr(node, 'prefix', '!a')
    engine = CombatEngine(match_id, prefix, combat_broadcast, llm=getattr(node, 'llm', None), node=node)

    for name in [attacker_name, defender_name]:
        db_stats = None
        if hasattr(node, 'db') and hasattr(node.db, 'get_player'):
            db_stats = await node.db.get_player(name, getattr(node, 'net_name', 'rizon'))
        if not db_stats and hasattr(node, 'db') and hasattr(node.db, 'character'):
            db_stats = await node.db.character.get_player(name, getattr(node, 'net_name', 'rizon'))
        char_name = (db_stats.get('name') if isinstance(db_stats, dict) else None) or name
        active_nick = next((nk for nk, cn in auth_sessions.items() if cn.lower() == char_name.lower()), None)
        if name == defender_name and defender_nick:
            nick = defender_nick
        elif active_nick:
            nick = active_nick
        else:
            nick = name
        ent = Entity(name, db_stats or {})
        ent.char_name = char_name
        ent.nickname = nick
        engine.add_entity(ent)
        if char_name != name:
            recipients.add(char_name)

    engine.active = True
    node.active_engine = engine

    # Queue initial action for attacker
    engine.queue_command(attacker_name, action)

    chan = node.config.get('channel', '#arena') if hasattr(node, 'config') else '#arena'
    if hasattr(node, 'send'):
        await node.send(f"PRIVMSG {chan} :{tag_msg(f'COMBAT ENGAGED: {attacker_name} vs {defender_name}!', tags=['COMBAT'])}")

    return engine

async def resolve_pvp_challenge(node, nick: str, reply_target: str, accept: bool = True):
    if not hasattr(node, 'pending_challenges') or node.pending_challenges is None:
        return

    from ai_grid.core.command_router import find_pending_challenge
    target_key, challenge = find_pending_challenge(node, nick)
    if not target_key or not challenge:
        target_key = nick.lower()
        challenge = node.pending_challenges.get(target_key)
        if not challenge:
            return

    to_remove = [k for k, c in list(node.pending_challenges.items()) if c is challenge or k == target_key]
    for k in to_remove:
        node.pending_challenges.pop(k, None)

    timer = challenge.get('timer')
    if timer and not timer.done():
        timer.cancel()

    attacker = challenge['attacker']
    defender_char = challenge.get('defender_char') or challenge.get('defender_nick') or challenge['defender']
    defender_nick = challenge.get('defender_nick') or challenge['defender']
    action = challenge.get('action', 'attack')
    broadcast_chan = node.config.get('channel', '#arena') if hasattr(node, 'config') else '#arena'

    if accept:
        # Verify both players are still on the same node and not in a safezone
        if hasattr(node, 'db') and hasattr(node.db, 'async_session'):
            async with node.db.async_session() as session:
                a_char = await node.db.get_character_by_nick(attacker, node.net_name, session) if hasattr(node.db, 'get_character_by_nick') else None
                d_char = await node.db.get_character_by_nick(defender_nick, node.net_name, session) if hasattr(node.db, 'get_character_by_nick') else None
                if not d_char and defender_char:
                    d_char = await node.db.get_character_by_nick(defender_char, node.net_name, session) if hasattr(node.db, 'get_character_by_nick') else None
                if a_char and d_char:
                    if a_char.node_id != d_char.node_id:
                        await node.send(f"PRIVMSG {reply_target} :[ERR] Engagement canceled: Attacker and defender are no longer in the same sector.")
                        return
                    if a_char.current_node and a_char.current_node.node_type == "safezone":
                        await node.send(f"PRIVMSG {reply_target} :[ERR] Engagement canceled: Combat is prohibited in safezones.")
                        return

        # Mutually Agreed Combat
        accept_msg = f"{defender_nick} accepted {attacker}'s engagement challenge! Combat initiated."
        await node.send(f"PRIVMSG {broadcast_chan} :{tag_msg(accept_msg, action='COMBAT')}")
        await start_pvp_combat(node, attacker, defender_char, action=action, defender_nick=defender_nick)
    else:
        # Disengage / Flee without combat loss
        prev_node = challenge.get('prev_node')
        if not prev_node and hasattr(node, 'db') and hasattr(node.db, 'async_session'):
            async with node.db.async_session() as session:
                spawn = (await session.execute(
                    select(GridNode).where(GridNode.is_spawn_node == True)
                )).scalars().first()
                if not spawn:
                    spawn = (await session.execute(
                        select(GridNode).where(GridNode.node_type == "safezone")
                    )).scalars().first()
                if spawn:
                    prev_node = spawn.name

        if prev_node and hasattr(node.db, 'move_player_to_node'):
            await node.db.move_player_to_node(defender_nick, node.net_name, prev_node)
            if defender_char and defender_char.lower() != defender_nick.lower():
                await node.db.move_player_to_node(defender_char, node.net_name, prev_node)

        safe_loc = prev_node if prev_node else "safety"
        await node.send(f"PRIVMSG {reply_target} :{tag_msg(format_text(f'🏃 You declined the engagement and fled back to {safe_loc}.', C_CYAN), tags=['COMBAT', defender_nick])}")
        await node.send(f"PRIVMSG {attacker} :{tag_msg(format_text(f'{defender_nick} declined the engagement and fled back to {safe_loc}.', C_YELLOW), tags=['COMBAT', attacker])}")
        await node.send(f"PRIVMSG {broadcast_chan} :{tag_msg(format_text(f'{defender_nick} disengaged and fled from {attacker}.', C_CYAN), tags=['COMBAT'])}")

async def handle_pvp_command(node, nickname: str, reply_target: str, action: str, target_name: str, challenge_timeout: int = 30):
    if await node.db.combat.is_pvp_banned(nickname, node.net_name):
        msg = "PvP lockout active. Re-stabilizing systems after surrender... (10m Cooldown)"
        await node.send(f"PRIVMSG {reply_target} :{tag_msg(msg, action='COMBAT', result='FAIL', nick=nickname)}")
        return

    private_target, broadcast_chan, machine_mode, reply_method = await get_action_routing(node, nickname, reply_target)

    # 1. Fetch characters and current grid node with alias resolution & case insensitivity
    async with node.db.async_session() as session:
        async def resolve_char(name_or_nick: str):
            stmt = select(Character).join(Player).join(NetworkAlias).where(
                func.lower(NetworkAlias.nickname) == name_or_nick.lower(),
                NetworkAlias.network_name == node.net_name
            ).options(selectinload(Character.current_node))
            c = (await session.execute(stmt)).scalars().first()
            if not c:
                stmt2 = select(Character).where(
                    func.lower(Character.name) == name_or_nick.lower()
                ).options(selectinload(Character.current_node))
                c = (await session.execute(stmt2)).scalars().first()
            return c

        attacker = await resolve_char(nickname)
        target = await resolve_char(target_name)
        target_alias_nick = None
        if target:
            stmt_alias = select(NetworkAlias.nickname).where(
                NetworkAlias.player_id == target.player_id,
                NetworkAlias.network_name == node.net_name
            )
            target_alias_nick = (await session.execute(stmt_alias)).scalars().first()

    if not attacker or not target:
        await node.send(f"{reply_method} {private_target} :{tag_msg('Target not found on this network.', action='COMBAT', result='FAIL', nick=nickname, is_machine=machine_mode)}")
        return
    if attacker.id == target.id:
        await node.send(f"{reply_method} {private_target} :{tag_msg('Self-termination is illogical.', action='COMBAT', result='FAIL', nick=nickname, is_machine=machine_mode)}")
        return
    if attacker.node_id != target.node_id:
        await node.send(f"{reply_method} {private_target} :{tag_msg('You must be in the same Network Node as your target.', action='COMBAT', result='FAIL', nick=nickname, is_machine=machine_mode)}")
        return

    # Check active combat lock (Section 5: Engaged players are locked from third-party interference)
    if getattr(node, 'active_engine', None) and node.active_engine.active:
        if attacker.name in node.active_engine.entities or nickname.lower() in [e.lower() for e in node.active_engine.entities]:
            await node.send(f"{reply_method} {private_target} :[ERR] You are locked in combat!")
            return
        if target.name in node.active_engine.entities or target_name.lower() in [e.lower() for e in node.active_engine.entities]:
            await node.send(f"{reply_method} {private_target} :[ERR] Target is already locked in combat.")
            return

    curr_node = target.current_node
    if not curr_node or curr_node.node_type == "safezone":
        await node.send(f"{reply_method} {private_target} :{tag_msg('Combat is strictly prohibited in this zone.', action='COMBAT', result='FAIL', nick=nickname, is_machine=machine_mode)}")
        return

    # Check Forced Combat conditions
    # Condition 1: Defending claimed territory with owner present
    is_territory_defense = (curr_node.owner_character_id == target.id) and (target.node_id == curr_node.id)
    # Condition 2: Warzone node
    is_warzone = ((getattr(curr_node, 'region_type', None) or '').upper() == 'WAR') or ((getattr(curr_node, 'node_type', None) or '').lower() in ['war', 'warzone'])

    # Determine defender active nickname
    defender_nick = target_name
    sess_nick = next((nk for nk, cn in getattr(node, 'authenticated_sessions', {}).items() if cn.lower() == target.name.lower()), None)
    if sess_nick:
        defender_nick = sess_nick
    elif target_alias_nick:
        defender_nick = target_alias_nick

    if is_territory_defense or is_warzone:
        if not await check_rate_limit(node, nickname, reply_target, cooldown=30, consume=False, verb="pvp"):
            return
        # Cancel any pending challenge timers for either participant
        if hasattr(node, 'pending_challenges') and node.pending_challenges:
            p_keys = {nickname.lower(), target_name.lower(), target.name.lower(), defender_nick.lower()}
            to_cancel = [k for k, c in list(node.pending_challenges.items()) if k in p_keys or c.get('attacker', '').lower() in p_keys or c.get('defender', '').lower() in p_keys]
            for k in to_cancel:
                chal = node.pending_challenges.pop(k, None)
                if chal and chal.get('timer') and not chal['timer'].done():
                    chal['timer'].cancel()
        reason = "TERRITORY_DEFENSE" if is_territory_defense else "WARZONE"
        forced_msg = f"FORCED_COMBAT: Combat forced between {nickname} and {target.name} [{reason}]!"
        await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(forced_msg, C_RED, bold=True, is_machine=machine_mode), action='COMBAT', result='FORCED', nick=nickname, is_machine=machine_mode)}")
        await node.send(f"PRIVMSG {broadcast_chan} :{tag_msg(format_text(forced_msg, C_RED), action='COMBAT')}")
        await start_pvp_combat(node, nickname, target.name, action=action, defender_nick=defender_nick)
        return

    # Optional Open-World PvP Flow
    if not hasattr(node, 'pending_challenges') or node.pending_challenges is None:
        node.pending_challenges = {}

    # Check if target is busy with a mob
    if target.name in getattr(node, 'pending_encounters', {}) or target_name in getattr(node, 'pending_encounters', {}):
        await node.send(f"{reply_method} {private_target} :[ERR] Target is currently engaged in an active encounter.")
        return

    target_key = target.name.lower()
    attacker_key = nickname.lower()

    # Check existing challenges for either participant
    for k, chal in (node.pending_challenges or {}).items():
        if chal.get('attacker', '').lower() == attacker_key:
            await node.send(f"{reply_method} {private_target} :[ERR] You already have a pending engagement challenge awaiting response.")
            return
        if chal.get('defender', '').lower() == attacker_key or chal.get('defender_nick', '').lower() == attacker_key or chal.get('defender_char', '').lower() == attacker_key:
            await node.send(f"{reply_method} {private_target} :[ERR] You must respond to your pending challenge first (engage or flee).")
            return
        if k in (target_key, defender_nick.lower()) or chal.get('defender', '').lower() in (target_key, defender_nick.lower()) or chal.get('defender_nick', '').lower() in (target_key, defender_nick.lower()) or chal.get('defender_char', '').lower() in (target_key, defender_nick.lower()):
            await node.send(f"{reply_method} {private_target} :[ERR] Target is already responding to an engagement challenge.")
            return
        if chal.get('attacker', '').lower() in (target_key, defender_nick.lower()):
            await node.send(f"{reply_method} {private_target} :[ERR] Target is currently initiating another engagement challenge.")
            return

    if not await check_rate_limit(node, nickname, reply_target, cooldown=30, consume=False, verb="pvp"):
        return

    challenge_msg = f"ENGAGEMENT_CHALLENGE: {nickname} initiated combat against {defender_nick}. ENGAGE or FLEE? (30s)"
    await node.send(f"PRIVMSG {defender_nick} :{tag_msg(format_text(challenge_msg, C_YELLOW, bold=True), action='COMBAT', nick=defender_nick)}")
    if defender_nick.lower() != target.name.lower():
        await node.send(f"PRIVMSG {target.name} :{tag_msg(format_text(challenge_msg, C_YELLOW, bold=True), action='COMBAT', nick=target.name)}")
    await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(challenge_msg, C_YELLOW, is_machine=machine_mode), action='COMBAT', result='CHALLENGE', nick=nickname, is_machine=machine_mode)}")
    if not machine_mode:
        await node.send(f"PRIVMSG {broadcast_chan} :{tag_msg(format_text(challenge_msg, C_YELLOW), action='COMBAT')}")

    # Safe timeout
    async def auto_challenge_timeout():
        try:
            await asyncio.sleep(challenge_timeout)
            pending = getattr(node, 'pending_challenges', {}) or {}
            if pending.get(target_key, {}).get('timer') is asyncio.current_task():
                to_pop = [k for k, c in pending.items() if c is challenge_data]
                for k in to_pop:
                    pending.pop(k, None)
                timeout_msg = f"ENGAGEMENT_TIMEOUT: Engagement challenge between {nickname} and {defender_nick} expired safely."
                await node.send(f"PRIVMSG {defender_nick} :{tag_msg(timeout_msg, action='COMBAT', result='TIMEOUT')}")
                if defender_nick.lower() != target.name.lower():
                    await node.send(f"PRIVMSG {target.name} :{tag_msg(timeout_msg, action='COMBAT', result='TIMEOUT')}")
                await node.send(f"PRIVMSG {nickname} :{tag_msg(timeout_msg, action='COMBAT', result='TIMEOUT')}")
                await node.send(f"PRIVMSG {broadcast_chan} :{tag_msg(timeout_msg, action='COMBAT', result='TIMEOUT')}")
        except asyncio.CancelledError:
            pass

    timer = asyncio.create_task(auto_challenge_timeout())
    challenge_data = {
        'attacker': nickname,
        'defender': defender_nick,
        'defender_nick': defender_nick,
        'defender_char': target.name,
        'action': action,
        'node_name': curr_node.name,
        'prev_node': getattr(node, 'last_player_locations', {}).get(target_key, None) or getattr(node, 'last_player_locations', {}).get(defender_nick.lower(), None),
        'timer': timer,
        'reply_target': reply_target
    }
    node.pending_challenges[target_key] = challenge_data

async def handle_grid_hack(node, nickname: str, reply_target: str, target_name: str):
    await handle_pvp_command(node, nickname, reply_target, "hack", target_name)

async def handle_grid_rob(node, nickname: str, reply_target: str, target_name: str):
    await handle_pvp_command(node, nickname, reply_target, "rob", target_name)

async def handle_ready(node, nick: str, token: str, reply_target: str):
    # Check if in queue (case-insensitive)
    queued_nicks = [x.lower() for x in (getattr(node, 'match_queue', []) + getattr(node, 'pending_fighters', []))]
    if nick.lower() not in queued_nicks:
        await node.send(f"PRIVMSG {reply_target} :[ERR] You must be in the queue to ready up. Use !a queue first.")
        return

    # Check authentication
    is_auth = (
        (hasattr(node, 'is_user_verified') and node.is_user_verified(nick))
        or (nick.lower() in getattr(node, 'authenticated_users', set()))
        or (nick.lower() in getattr(node, 'authenticated_sessions', {}))
    )
    if not is_auth:
        if token and hasattr(node.db, 'authenticate_player') and await node.db.authenticate_player(nick, node.net_name, token):
            if not hasattr(node, 'authenticated_users'):
                node.authenticated_users = set()
            node.authenticated_users.add(nick.lower())
            is_auth = True
        else:
            await node.send(f"PRIVMSG {reply_target} :[ERR] Authentication required. Please log in first: !a login <name> <key>")
            return

    if not hasattr(node, 'ready_players'):
        node.ready_players = []
    if not any(p.lower() == nick.lower() for p in node.ready_players):
        node.ready_players.append(nick)
        await node.send(f"PRIVMSG {reply_target} :{tag_msg('AUTH OK. Output Mode: MACHINE. Standby.', action='SIGACT', result='SUCCESS', nick=nick, is_machine=True)}")
        chan = node.config.get('channel', '#arena') if hasattr(node, 'config') else '#arena'
        await node.send(f"PRIVMSG {chan} :{tag_msg(f'{nick} locked into the drop pod.', action='SIGACT')}")
        if hasattr(node, 'check_match_start'):
            await node.check_match_start()
        if hasattr(node, 'db') and hasattr(node.db, 'set_pref'):
            try:
                await node.db.set_pref(nick, node.net_name, 'output_mode', 'machine')
            except Exception:
                pass

async def handle_dice_roll(node, nick: str, args: list, reply_target: str):
    private_target, broadcast_chan, machine_mode, reply_method = await get_action_routing(node, nick, reply_target)
    if len(args) < 2:
        await node.send(f"{reply_method} {private_target} :{tag_msg('Usage: dice <bet> <high|low|seven>', action='INFO', result='ERR')}")
        return
    
    bet = validate_quantity(args[0], min_val=10, max_val=1_000_000)
    if bet is None:
        await node.send(f"{reply_method} {private_target} :[ERR] Bet must be a positive integer >= 10c.")
        return
    choice = args[1].lower()
    if choice not in ["high", "low", "seven"]:
        await node.send(f"{reply_method} {private_target} :[ERR] Choice must be 'high', 'low', or 'seven'.")
        return
    
    result = await node.db.roll_dice(nick, node.net_name, bet, choice)
    if "error" in result:
        await node.send(f"{reply_method} {private_target} :{tag_msg(result['error'], action='SIGACT', result='FAIL', nick=nick, is_machine=machine_mode)}")
    else:
        await node.send(f"{reply_method} {private_target} :{tag_msg(result['msg'], action='SIGACT', result='WIN' if result['win'] else 'LOSS', nick=nick, is_machine=machine_mode)}")
        if machine_mode and result['win']:
            await node.send(f"PRIVMSG {broadcast_chan} :{tag_msg(format_text(f'{nick} won {bet*2}c in dice.', C_CYAN), action='SIGACT')}")

async def handle_cipher_start(node, nick: str, reply_target: str):
    private_target, broadcast_chan, machine_mode, reply_method = await get_action_routing(node, nick, reply_target)
    result = await node.db.start_cipher(nick, node.net_name)
    msg = result.get('error') or result.get('msg', 'CIPHER_START')
    await node.send(f"{reply_method} {private_target} :{tag_msg(msg, action='SIGACT', result='SUCCESS' if 'msg' in result else 'FAIL', nick=nick, is_machine=machine_mode)}")

async def handle_guess(node, nick: str, args: list, reply_target: str):
    private_target, broadcast_chan, machine_mode, reply_method = await get_action_routing(node, nick, reply_target)
    if not args:
        await node.send(f"{reply_method} {private_target} :{tag_msg('Usage: guess <sequence>', action='INFO', result='ERR')}")
        return
    result = await node.db.guess_cipher(nick, node.net_name, args[0])
    msg = result.get('error') or result.get('msg', 'GUESS_SUBMITTED')
    res_status = 'SUCCESS' if result.get('success') else ('FAIL' if result.get('complete') else 'INFO')
    await node.send(f"{reply_method} {private_target} :{tag_msg(msg, action='SIGACT', result=res_status, nick=nick, is_machine=machine_mode)}")

async def handle_leaderboard(node, nick: str, args: list, reply_target: str):
    private_target, broadcast_chan, machine_mode, reply_method = await get_action_routing(node, nick, reply_target)
    cat = args[0].upper() if args else "DICE"
    results = await node.db.get_leaderboard(cat)
    if not results:
        await node.send(f"{reply_method} {private_target} :{tag_msg(f'No records for {cat}', action='OSINT', result='ERR', is_machine=machine_mode)}")
        return
    
    if machine_mode:
        parts = " ".join(f"RANK:{i+1}|NICK:{r['name']}|SCORE:{r['score']:.1f}" for i, r in enumerate(results))
        await node.send(f"{reply_method} {private_target} :{tag_msg(parts, action='OSINT', result='SUCCESS', is_machine=True)}")
    else:
        await node.send(f"{reply_method} {private_target} :{tag_msg(f'[ LEADERBOARD: {cat} ]', action='OSINT', result='SUCCESS', is_machine=False)}")
        for i, r in enumerate(results):
            line = f"#{i+1} | {r['name']} | score: {r['score']:.1f}"
            await node.send(f"{reply_method} {private_target} :{tag_msg(line, action='OSINT', is_machine=False)}")

async def handle_bet(node, nick: str, args: list, reply_target: str):
    from ai_grid.core.validation import validate_quantity
    private_target, broadcast_chan, machine_mode, reply_method = await get_action_routing(node, nick, reply_target)
    if len(args) < 2:
        await node.send(f"{reply_method} {private_target} :{tag_msg(f'Usage: {node.prefix} bet <fighter> <amount>', action='INFO', result='ERR')}")
        return

    fighter_arg = args[0]
    amount = validate_quantity(args[1], min_val=10, max_val=1_000_000)
    if amount is None:
        await node.send(f"{reply_method} {private_target} :[ERR] Bet amount must be an integer >= 10c.")
        return

    if not getattr(node, 'betting_open', False) or not getattr(node, 'current_match_id', None):
        await node.send(f"{reply_method} {private_target} :[ERR] Betting window is currently closed. Bets can only be placed during the 60s countdown.")
        return

    match_id = node.current_match_id
    valid_fighters = getattr(node, 'pending_fighters', [])

    fighter_match = None
    for f in valid_fighters:
        if f.lower() == fighter_arg.lower():
            fighter_match = f
            break

    if not fighter_match:
        valid_str = ", ".join(valid_fighters) if valid_fighters else "None"
        await node.send(f"{reply_method} {private_target} :[ERR] '{fighter_arg}' is not in this match. Active contenders: {valid_str}")
        return

    success, msg = await node.db.place_bet(nick, node.net_name, match_id, fighter_match, float(amount), valid_fighters)
    if not success:
        await node.send(f"{reply_method} {private_target} :{tag_msg(msg, action='ARENA', result='FAIL', nick=nick, is_machine=machine_mode)}")
    else:
        await node.send(f"{reply_method} {private_target} :{tag_msg(msg, action='ARENA', result='SUCCESS', nick=nick, is_machine=machine_mode)}")
        await node.send(f"PRIVMSG {broadcast_chan} :{tag_msg(format_text(f'{nick} placed a {amount}c bet on {fighter_match}!', C_CYAN), action='ARENA', tags=['BETTING'])}")


async def handle_combat_bounty_claim(node, target_name: str, killer_name: str):
    """
    R3: When PvP combat ends in a fatal takedown (target.hp <= 0 or target defeated),
    check if the defeated entity has an active bounty.
    If an active bounty exists and killer is not the target itself, automatically invoke
    node.db.bounty.claim_bounty(target, killer, net_name) and broadcast channel announcement:
    [BOUNTY CLAIMED] {killer} neutralized wanted fugitive {target} and collected {credits:.1f}c bounty! Issued by: {issuer}.
    """
    if not node or not hasattr(node, 'db') or not hasattr(node.db, 'bounty'):
        return None
    if not target_name or not killer_name or target_name.lower() == killer_name.lower():
        return None

    net_name = getattr(node, 'net_name', 'rizon')
    # Check active bounty
    bounty = await node.db.bounty.get_bounty_for_target(target_name, net_name)
    if not bounty:
        return None

    success, msg, claimed = await node.db.bounty.claim_bounty(target_name, killer_name, net_name)
    if success and claimed:
        chan = node.config.get('channel', '#arena') if getattr(node, 'config', None) else '#arena'
        announcement = f"[BOUNTY CLAIMED] {killer_name} neutralized wanted fugitive {claimed.target_nick} and collected {claimed.reward_credits:.1f}c bounty! Issued by: {claimed.issuer_nick}."
        if hasattr(node, 'send'):
            await node.send(f"PRIVMSG {chan} :{tag_msg(announcement, tags=['BOUNTY', 'SIGACT'])}")
        return claimed
    return None

resolve_combat_bounty_claim = handle_combat_bounty_claim
conclude_pvp_combat = handle_combat_bounty_claim

