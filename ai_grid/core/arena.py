# arena.py - v2.0.0
import asyncio
import logging
import time
import uuid
from ai_grid.grid_combat import CombatEngine, Entity
from ai_grid.grid_utils import (
    format_text, tag_msg, ICONS, C_GREEN, C_CYAN, C_RED, C_YELLOW, C_BLUE, C_PURPLE,
    C_ORANGE, C_L_GREEN, C_L_BLUE, C_PINK, generate_gradient, generate_meter,
    TOPIC_START, TOPIC_END, TOPIC_SEP
)

logger = logging.getLogger("manager")

async def set_dynamic_topic(node):
    """Generates and sets a high-aesthetic, multi-mode channel topic."""
    mode = getattr(node, 'topic_mode', 0)
    
    # 1. Gather Core Telemetry
    players = await node.db.list_players(node.net_name)
    node.registered_bots = len(players)
    
    # 2. Mode-Specific Construction
    try:
        if mode == 0: # STATUS
            title = "[GRID][SIGACT]"
            gradient_title = generate_gradient(title, [C_CYAN, C_L_BLUE, C_BLUE])
            
            match_status = format_text("ACTIVE", C_GREEN) if node.active_engine else "STANDBY"
            # Use 50 as a reference capacity for the meter
            bot_meter = generate_meter(node.registered_bots, 50)
            content = f"ARENA: {match_status} | LOAD: [{bot_meter}] {node.registered_bots} UNITS"
            
        elif mode == 1: # INTEL
            title = "[GRID][GEOINT]"
            gradient_title = generate_gradient(title, [C_YELLOW, C_ORANGE, C_RED])
            
            grid = await node.db.get_grid_telemetry()
            mesh_meter = generate_meter(grid['claimed_nodes'], grid['total_nodes'])
            content = f"MESH: [{mesh_meter}] {grid['claimed_percent']:.1f}% | PWR: {grid['total_power']:.0f}uP"
            
        elif mode == 2: # NEWS
            title = "[GRID][SIGINT]"
            gradient_title = generate_gradient(title, [C_L_GREEN, C_GREEN, C_CYAN])
            # news = await node.llm.generate_topic(node.registered_bots, node.net_name)
            # Actually, use generate_hype for a better ticker feel if no match is active
            news = await node.llm.generate_topic(node.registered_bots, node.net_name)
            content = news[:90] + "..." if len(news) > 90 else news
            
        else: # EVENTS
            title = "[GRID][ARENA]"
            gradient_title = generate_gradient(title, [C_PINK, C_PURPLE, C_BLUE])
            hype_meter = generate_meter(node.hype_counter, 15)
            queue_len = len(node.match_queue)
            content = f"QUEUE: {queue_len} READY | HYPE: [{hype_meter}] Resonance Burst"

        # 3. Final Assembly
        fmt_topic = f"{TOPIC_START}{gradient_title}{TOPIC_SEP}{content}{TOPIC_END}"
        await node.send(f"TOPIC {node.config['channel']} :{fmt_topic}")
        
    except Exception as e:
        logger.error(f"Failed to set dynamic topic: {e}")
        # Fallback to simple topic
        await node.send(f"TOPIC {node.config['channel']} :[GRID] Hub active. Version ARCHIVE 1.7.x")

async def trigger_arena_call(node):
    if not node.active_engine or not node.active_engine.active:
        alert = format_text("The Gladiator Gates are open. Travel to The Arena node to 'queue'!", C_YELLOW, True)
        await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(alert, tags=['ARENA'])}")

async def check_match_start(node):
    if len(node.ready_players) > 0 and not node.active_engine and not node.pve_task:
        match_id = f"ARENA_{int(time.time())}_{uuid.uuid4().hex[:4]}"
        node.current_match_id = match_id
        node.betting_open = True
        if len(node.ready_players) >= 2:
            node.pending_fighters = [node.ready_players[0], node.ready_players[1]]
        else:
            node.pending_fighters = [node.ready_players[0], "Trojan.Exe"]

        contenders = " vs ".join(node.pending_fighters)
        # A 1-minute grace period begins as soon as the first player readies
        await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(f'Match initialization sequence started! 60 seconds until combat drop: [{contenders}]. Betting is OPEN: {node.prefix} bet <fighter> <amount>', tags=['ARENA', 'SIGACT'])}")
        node.pve_task = asyncio.create_task(pve_countdown(node))

async def pve_countdown(node):
    try:
        await asyncio.sleep(60) # Standard 1-minute grace period
        node.betting_open = False
        match_id = getattr(node, 'current_match_id', None) or f"ARENA_{int(time.time())}_{uuid.uuid4().hex[:4]}"
        if len(node.ready_players) >= 2:
            participants = node.ready_players[:2]
            node.ready_players = node.ready_players[2:]
            logger.info(f"Starting PVP Match after 1m wait: {participants}")
            asyncio.create_task(start_match(node, match_id, participants, pve=False))
        elif len(node.ready_players) == 1:
            player = node.ready_players.pop(0)
            await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg('No human challengers manifested. Dropping PvE obstacle.', tags=['ARENA', 'SIGACT'])}")
            logger.info(f"Starting PVE Match for: {player}")
            asyncio.create_task(start_match(node, match_id, [player], pve=True))
        else:
            # Aborted with 0 fighters
            if getattr(node, 'current_match_id', None):
                await node.db.refund_bets(node.current_match_id)
            node.current_match_id = None
            node.pending_fighters = []
            node.betting_open = False
        
        node.pve_task = None
    except asyncio.CancelledError:
        node.betting_open = False
        if getattr(node, 'current_match_id', None):
            await node.db.refund_bets(node.current_match_id)
        node.current_match_id = None
        node.pending_fighters = []
        node.pve_task = None

async def generate_and_queue_npc(node, npc: Entity, state_msg: str):
    action = await node.llm.generate_npc_action(npc.name, npc.bio, state_msg, node.prefix)
    if node.active_engine and node.active_engine.active:
        node.active_engine.queue_command(npc.name, action)

async def start_match(node, match_id: str, participants: list, pve=False):
    node.betting_open = False
    # Detect participant preferences for mirroring
    machine_participants = []
    for p in participants:
        prefs = await node.db.get_prefs(p, node.net_name)
        if prefs.get('output_mode') == 'machine':
            machine_participants.append({
                'nick': p,
                'cmd': prefs.get('msg_type', 'PRIVMSG').upper()
            })

    async def combat_broadcast(msg: str):
        # 1. Always send to Public Channel
        await node.send(f"PRIVMSG {node.config['channel']} :{msg}")
        
        # 2. Mirror to Machine-Mode Participants
        for mp in machine_participants:
            # We strip formatting/tags for the machine version if necessary, 
            # but usually the callback msg already has tags.
            await node.send(f"{mp['cmd']} {mp['nick']} :{msg}")

    for p in participants:
        if p in node.match_queue:
            node.match_queue.remove(p)

    node.active_engine = CombatEngine(match_id, node.prefix, combat_broadcast, llm=getattr(node, 'llm', None), node=node)
    for name in participants:
        db_stats = await node.db.get_player(name, node.net_name)
        node.active_engine.add_entity(Entity(name, db_stats))

    if pve:
        # Normalizing NPC stats for better starter game (RAM 8->6, SEC 6->5)
        npc_db = {'cpu': 6, 'ram': 6, 'bnd': 4, 'sec': 5, 'alg': 2, 'inventory': '["Malware_Blade"]', 'alignment': -100, 'bio': 'A feral, rogue malware process.'}
        node.active_engine.add_entity(Entity("Trojan.Exe", npc_db, is_npc=True))

    node.active_engine.active = True
    await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg('THE ARENA IS LOCKED. COMBAT SEQUENCE INITIALIZED!', tags=['ARENA', 'COMBAT'])}")
    await asyncio.sleep(2)

    while node.active_engine and node.active_engine.active:
        raw_state = await node.active_engine.broadcast_state()
        await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(raw_state + '| Awaiting public commands (30s)...', tags=['ARENA', 'COMBAT'])}")

        npc_tasks = [generate_and_queue_npc(node, ent, raw_state) for ent in node.active_engine.entities.values() if ent.is_npc and ent.is_alive]
        if npc_tasks: asyncio.gather(*npc_tasks) 

        await asyncio.sleep(30) 
        
        if node.active_engine and node.active_engine.active:
            node.active_engine.active = await node.active_engine.resolve_turn()
            if node.active_engine.active: await asyncio.sleep(2)

    if node.active_engine: 
        winners = [e.name for e in node.active_engine.entities.values() if e.is_alive and not e.is_npc]
        losers = [e.name for e in node.active_engine.entities.values() if not e.is_alive and not e.is_npc]
        npc_winners = [e.name for e in node.active_engine.entities.values() if e.is_alive and e.is_npc]
        
        if winners and losers:
            # v1.8.0: Pass surrender flag and final uP to repo
            winner_name = winners[0]
            loser_name = losers[0]
            winner_ent = node.active_engine.entities.get(winner_name)
            loser_ent = node.active_engine.entities.get(loser_name)
            
            was_surrender = loser_ent.status == "Surrendered" if loser_ent else False
            winner_up = winner_ent.up if winner_ent else None
            loser_up = loser_ent.up if loser_ent else None
            
            await node.db.record_match_result(
                winner_name, loser_name, node.net_name, 
                was_surrender=was_surrender,
                winner_up=winner_up,
                loser_up=loser_up
            )

            try:
                from ai_grid.core.handlers.combat import handle_combat_bounty_claim
                await handle_combat_bounty_claim(node, loser_name, winner_name)
            except Exception as e:
                logger.warning(f"Error checking arena bounty claim: {e}")

        # Betting Resolution
        match_id_res = getattr(node.active_engine, 'match_id', match_id)
        if winners:
            winner_name = winners[0]
            payouts = await node.db.resolve_bets(match_id_res, winner_name)
            if payouts:
                won_count = sum(1 for p in payouts if p['status'] == 'WON')
                total_paid = sum(p['payout'] for p in payouts if p['status'] == 'WON')
                await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(f'BETTING RESOLUTION: {winner_name} wins! {won_count} bets paid out ({total_paid:.0f}c total).', tags=['ARENA', 'SIGACT'])}")
        elif npc_winners:
            winner_name = npc_winners[0]
            payouts = await node.db.resolve_bets(match_id_res, winner_name)
            if payouts:
                won_count = sum(1 for p in payouts if p['status'] == 'WON')
                total_paid = sum(p['payout'] for p in payouts if p['status'] == 'WON')
                await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(f'BETTING RESOLUTION: {winner_name} wins! {won_count} bets paid out ({total_paid:.0f}c total).', tags=['ARENA', 'SIGACT'])}")
        else:
            refunds = await node.db.refund_bets(match_id_res)
            if refunds:
                await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(f'BETTING REFUND: Match concluded without a victor. {len(refunds)} bets refunded.', tags=['ARENA', 'SIGACT'])}")

        node.current_match_id = None
        node.pending_fighters = []
        node.betting_open = False
        await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg('MATCH CONCLUDED.', tags=['ARENA'])}")
        node.active_engine = None
    
    await check_match_start(node)
