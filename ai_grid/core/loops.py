# loops.py - v1.5.0
import asyncio
import logging
import time
import datetime
from datetime import timezone
import random
from ai_grid.grid_utils import format_text, tag_msg, C_GREEN, C_CYAN, C_RED, C_YELLOW

logger = logging.getLogger("manager")

async def hype_loop(node):
    await asyncio.sleep(60) 
    while True:
        try:
            await asyncio.sleep(2700) 
            if not node.active_engine:
                hype_msg = await node.llm.generate_hype()
                if not hype_msg.startswith("ERROR"):
                    await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(format_text(hype_msg, C_YELLOW, True), tags=['RUMINT'])}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Hype loop error on {node.net_name}: {e}")

async def ambient_event_loop(node):
    await asyncio.sleep(120) 
    while True:
        try:
            await asyncio.sleep(600)  # 10 minute interval
            
            # --- TASK 022: DYNAMIC CHANNEL FLOW (THE PULSE) ---
            # Expiry & Penalty Processing
            penalties = await node.db.pulse.expire_pulses(node.net_name)
            for alert in penalties:
                await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(format_text(alert, C_RED), tags=['PULSE'])}")

            # Pulse Spawning Logic
            # 10% base + 5% per hype unit (chat volume)
            base_chance = node.config.get('mechanics', {}).get('pulse_spawn_chance', 0.10)
            hype_bonus = node.hype_counter * 0.05
            spawn_chance = min(0.60, base_chance + hype_bonus)
            
            node.hype_counter = 0 # Reset activity tracking
            
            if random.random() < spawn_chance:
                pulse = await node.db.pulse.spawn_pulse(node.net_name)
                if pulse:
                    p_type = pulse['type']
                    n_name = pulse['node_name']
                    expiry = pulse['expiry'].strftime("%H:%M:%S UTC")
                    
                    if p_type == 'PACKET':
                        alert = f"HIGH-VALUE PACKET DETECTED at {n_name}! Signal expires at {expiry}. Use '!a collect {n_name}' to intercept."
                        color = C_GREEN
                    else: # GLITCH
                        alert = f"NODAL GLITCH DETECTED at {n_name}! Cascade failure imminent at {expiry}. Use '!a patch {n_name}' to stabilize."
                        color = C_YELLOW
                    
                    await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(format_text(alert, color, bold=True), tags=['PULSE'])}")
                    continue # Skip standard flavor text if pulse spawned

            # Standard Flavor Text (Fallback)
            if not node.active_engine or not node.active_engine.active:
                event = await node.llm.generate_ambient_event()
                cat = event.get('category', 'SYS').upper()
                msg = event.get('message', '')
                await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(format_text(msg, C_CYAN, True), tags=[cat])}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Ambient event error on {node.net_name}: {e}")

async def arena_call_loop(node):
    await asyncio.sleep(120) 
    while True:
        try:
            await asyncio.sleep(3600)  # 60 minute interval
            if not node.active_engine or not node.active_engine.active:
                alert = format_text("The Gladiator Gates are open. Travel to The Arena node to 'queue'!", C_YELLOW, True)
                await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(alert, tags=['ARENA'])}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Arena call error on {node.net_name}: {e}")

async def power_tick_loop(node):
    await asyncio.sleep(30)
    while True:
        try:
            await asyncio.sleep(600)  # 10 minute interval
            await node.db.tick_grid_power()
            # v1.8.0 Power Trickle
            await node.db.trickle_spectator_power(node.net_name)
            msg = format_text("Environmental Power levels restabilized based on organic loads.", C_CYAN)
            await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(msg, tags=['MAINT'])}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Power tick error on {node.net_name}: {e}")

async def idle_payout_loop(node):
    await asyncio.sleep(60) 
    while True:
        try:
            await asyncio.sleep(3600)  # 60 minute interval
            import datetime
            now_dt = datetime.datetime.now(datetime.timezone.utc)
            now_ts = time.time()
            payouts = {}
            
            rewarded_entities = set()
            for nick, data in list(node.channel_users.items()):
                join_time = data.get('join_time', now_ts)
                chat_lines = data.get('chat_lines', 0)
                idle_secs = now_ts - join_time
                
                # 1. Hourly/Passive Accrual
                earned = (idle_secs * 0.005) + (chat_lines * 0.5) 
                xp_earned = int((idle_secs * 0.1) + (chat_lines * 10))
                
                if earned > 0: 
                    payouts[nick] = round(earned, 3)
                    rewarded_entities.add(nick)
                if xp_earned > 0:
                    await node.db.player.add_experience(nick, node.net_name, xp_earned, llm_client=node.llm)
                    rewarded_entities.add(nick)
                
                # 2. Automated Daily Dividend (v1.8.0)
                char = await node.db.player.get_player(nick, node.net_name)
                if char and char['race'] == "Spectator":
                    last_bonus = char.get('last_daily_bonus_at')
                    should_payout = False
                    if not last_bonus:
                        should_payout = True
                    else:
                        # Compare UTC days
                        if last_bonus.date() < now_dt.date():
                            should_payout = True
                    
                    if should_payout:
                        success, log = await node.db.award_daily_dividend(nick, node.net_name)
                        if success:
                            rewarded_entities.add(nick)
                            logger.info(f"Automated Dividend for {nick}: {log}")
                            await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(format_text(f'🏆 Automated Dividend: {nick} received a daily participation bonus!', C_YELLOW), tags=['ECONOMY', nick])}")

                # 3. Persistence
                await node.db.update_activity_stats(nick, node.net_name, chat_lines, idle_secs)
                if nick in node.channel_users:
                    node.channel_users[nick]['join_time'] = now_ts
                    node.channel_users[nick]['chat_lines'] = 0
            
            if payouts:
                idlers = list(payouts.keys())
                await node.db.award_credits_bulk(payouts, node.net_name)
                await node.db.tick_player_maintenance(node.net_name, idlers)
            
            decayed, pruned = await node.db.tick_retention_policy(node.config)
            if decayed > 0 or pruned > 0:
                logger.info(f"Retention Policy Enforced: {decayed} decayed, {pruned} pruned.")

            try:
                decay_res = await node.db.apply_passive_decay()
                if decay_res.get('decayed_count', 0) > 0:
                    logger.info("Passive Reputation/Heat Decay applied to %d characters.", decay_res['decayed_count'])
            except Exception:
                logger.exception("Error applying passive reputation decay")

            # --- TASK 052: HUMANIZED HOURLY PAYOUT ---
            entity_count = len(rewarded_entities)
            if entity_count > 0:
                announcement = await node.llm.generate_hourly_payout(entity_count)
                if announcement.startswith("ERROR"):
                    # Fallback Logic: User-specified message for delayed connections
                    announcement = "...delayed neural connection, idle bonuses paid to spectators and players... ⚡💎"
                    color = C_CYAN
                else:
                    color = C_GREEN
                
                await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(format_text(announcement, color, bold=True), tags=['ECONOMY'])}")
            else:
                # Fallback for empty cycles
                await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(format_text('The Grid remains quiet. No active entities detected for this cycle.', C_CYAN), tags=['ECONOMY'])}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Payout loop error on {node.net_name}: {e}")

async def mainframe_loop(node):
    """Processes background Gibson tasks and MemoServ notifications."""
    await asyncio.sleep(45)
    while True:
        try:
            await asyncio.sleep(60) # Process every minute
            notifications = await node.db.tick_mainframe_tasks()
            
            for note in notifications:
                # We only process notes for the current network in this loop instance
                if note['network'].lower() == node.net_name.lower():
                    # Channel Alert
                    await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(format_text(note['msg'], C_CYAN), tags=['SIGINT', note['nickname']])}")
                    
                    # MemoServ Integration (Global)
                    if CONFIG.get('mechanics', {}).get('mainframe', {}).get('memoserv_enabled', True):
                        await node.hub.send_memo(note['network'], note['nickname'], note['msg'])
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Mainframe loop error on {node.net_name}: {e}")

async def auction_loop(node):
    """Checks for expired DarkNet auctions and processes fulfillment."""
    await asyncio.sleep(30)
    while True:
        try:
            await asyncio.sleep(60) # Tick every minute
            notifications = await node.db.tick_auctions()
            
            for note in notifications:
                # MemoServ Integration (Global)
                if CONFIG.get('mechanics', {}).get('mainframe', {}).get('memoserv_enabled', True):
                    await node.hub.send_memo(note['network'], note['nickname'], note['msg'])
                
                # If the recipient is currently on THIS network, give them a live alert too
                if note['network'].lower() == node.net_name.lower():
                    await node.send(f"PRIVMSG {note['nickname']} :{tag_msg(format_text(note['msg'], C_YELLOW), tags=['ECONOMY', note['nickname']])}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Auction loop error on {node.net_name}: {e}")

async def economic_ticker_loop(node):
    """LLM-driven market fluctuations every 60 minutes."""
    await asyncio.sleep(15) # Stagger start
    while True:
        try:
            # Generate Market News via LLM
            news_text = await node.hub.llm.generate_market_news()
            
            # Simple heuristic to extract multipliers from LLM output or just random-walk
            # For robustness, we'll use a random walk with LLM flavor text
            multipliers = {
                "junk": round(random.uniform(0.8, 1.5), 2),
                "hack": round(random.uniform(0.9, 1.3), 2),
                "weapon": round(random.uniform(0.9, 1.2), 2),
                "gear": round(random.uniform(0.9, 1.1), 2)
            }
            
            await node.db.update_market_rates(multipliers, news_text)
            
            # Broadcast news to the channel
            await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(format_text(news_text, C_CYAN), tags=['ECONOMY'])}")
            
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Economic ticker error: {e}")
            await asyncio.sleep(300)

async def hype_drop_loop(node):
    """Rewards active spectators during chat volume spikes."""
    await asyncio.sleep(60)
    while True:
        try:
            await asyncio.sleep(300) # Check every 5 minutes
            threshold = 15
            if node.hype_counter >= threshold:
                # Find active chatters (last 5 mins)
                # For simplicity, we filter from channel_users who have > 0 lines
                chatters = [nick for nick, data in node.channel_users.items() if data.get('chat_lines', 0) > 0]
                
                if chatters:
                    count = min(3, len(chatters))
                    lucky = random.sample(chatters, count)
                    
                    for nick in lucky:
                        # Award 100c or 5 Data
                        reward_type = random.choice(["credits", "data"])
                        if reward_type == "credits":
                            await node.db.economy.award_credits(nick, node.net_name, 100)
                        else:
                            await node.db.economy.award_data(nick, node.net_name, 5.0)
                        
                        node.channel_users[nick]['chat_lines'] = 0 # Reset activity for this user
                    
                    msg = format_text(f"The Grid resonates with your energy! Rewards dropped to: {', '.join(lucky)}", C_YELLOW, bold=True)
                    await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(msg, tags=['SIGACT'])}")
            
            node.hype_counter = 0 # Reset global counter
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Hype drop loop error: {e}")

async def topic_engine_loop(node):
    """Rotates the channel topic through different intelligence modes (Task 018)."""
    await asyncio.sleep(60) # Stagger start
    while True:
        try:
            await node.set_dynamic_topic()
            # Default to 15m if not set
            interval = getattr(node, 'topic_interval', 15)
            await asyncio.sleep(interval * 60)
            # Cycle through 4 modes
            node.topic_mode = (node.topic_mode + 1) % 4
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Topic engine error on {node.net_name}: {e}")
            await asyncio.sleep(60)

INCURSION_SCHEDULE = {
    1: {'label': 'Easy', 'interval': 1800, 'players': 1, 'chance': 0.30},
    2: {'label': 'Medium', 'interval': 3600, 'players': 2, 'chance': 0.25},
    3: {'label': 'Hard', 'interval': 14400, 'players': 4, 'chance': 0.20},
    4: {'label': 'Epic', 'interval': 43200, 'players': 8, 'chance': 0.15},
}

async def incursion_event_loop(node):
    """Spawns Cooperative World Events semi-randomly."""
    await asyncio.sleep(180) # Stagger start 3 mins
    
    # Internal schedule trackers
    schedule = {
        tier: {**data, 'last_check': 0}
        for tier, data in INCURSION_SCHEDULE.items()
    }
    
    while True:
        try:
            await asyncio.sleep(600) # Check every 10 minutes
            now_ts = time.time()
            
            # Expire active first
            notifications = await node.db.incursion.expire_incursions(node.net_name, getattr(node, 'llm', None))
            for alert in notifications:
                await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(format_text(alert, C_RED), tags=['INCURSION'])}")
                
            # Spawn new
            for tier, data in schedule.items():
                if now_ts - data['last_check'] >= data['interval']:
                    data['last_check'] = now_ts
                    if random.random() <= data['chance']:
                        target_node = None
                        if hasattr(node.db, 'incursion') and hasattr(node.db.incursion, 'get_random_incursion_node'):
                            try:
                                target_node = await node.db.incursion.get_random_incursion_node(node.net_name)
                            except Exception as e:
                                logger.error(f"Error fetching incursion node on {node.net_name}: {e}")
                        
                        # If get_random_incursion_node is available and returned None, no valid unoccupied nodes exist
                        if hasattr(node.db, 'incursion') and hasattr(node.db.incursion, 'get_random_incursion_node') and target_node is None:
                            logger.info(f"No available incursion nodes on {node.net_name}.")
                            continue
                        
                        target_name = getattr(target_node, 'name', '') or ''
                        target_region = getattr(target_node, 'region_type', '') or ''
                        target_id = getattr(target_node, 'id', None)
                        
                        mob_name = None
                        if getattr(node, 'llm', None) and hasattr(node.llm, 'generate_incursion_mob_name'):
                            try:
                                mob_name = await node.llm.generate_incursion_mob_name(tier, target_name, target_region)
                            except Exception as e:
                                logger.error(f"Error generating mob name on {node.net_name}: {e}")
                        if not mob_name:
                            from ai_grid.grid_llm import FALLBACK_ROSTER
                            mob_name = random.choice(FALLBACK_ROSTER.get(tier, FALLBACK_ROSTER[1]))
                        
                        try:
                            incursion = await node.db.incursion.spawn_incursion(
                                node.net_name, 
                                inc_type=mob_name, 
                                tier=tier,
                                reward=500.0 * tier,
                                duration_mins=5,
                                target_node_id=target_id
                            )
                        except TypeError:
                            incursion = await node.db.incursion.spawn_incursion(
                                node.net_name, 
                                inc_type=mob_name, 
                                tier=tier,
                                reward=500.0 * tier,
                                duration_mins=5
                            )
                        
                        if incursion:
                            node_name = incursion.get('node_name', target_name)
                            difficulty_label = data['label']
                            players = data['players']
                            msg = f"WORLD EVENT: A Tier {tier} ({difficulty_label}) anomaly [{mob_name}] has breached {node_name}! Requires {players} defenders. Use '!a defend {node_name}' within 5 minutes!"
                            await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(format_text(msg, C_RED, bold=True), tags=['INCURSION', 'URGENT'])}")
                            break # Only spawn one type per tick to prevent spam
                            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Incursion loop error on {node.net_name}: {e}")

async def distribute_spectator_payout(node):
    """
    Executes passive accrual payout to active spectators (seen within past 90 minutes).
    Base XP (10), Base Credits (5),
    Chat bonus (+2 credits per 10 messages since last payout, capped at +20),
    Idle bonus (+1 XP per hour of session time).
    Atomically commits rewards, increments idle hours by 1.0, resets message count,
    and broadcasts summary notice to the channel:
    '[GRID DIVIDEND] Hourly accrual distributed to N spectators.'
    """
    try:
        repo = getattr(node.db, 'spectator_repo', None)
        if repo is None:
            from ai_grid.database.spectator_repo import SpectatorRepository
            repo = SpectatorRepository(node.db.async_session)

        net_name = getattr(node, 'net_name', None)
        if net_name:
            active_res = repo.get_all_active(network=net_name, since_minutes=90)
        else:
            active_res = repo.get_all_active(since_minutes=90)

        if asyncio.iscoroutine(active_res) or hasattr(active_res, '__await__'):
            active_spectators = await active_res
        else:
            active_spectators = active_res

        if not active_spectators:
            return 0

        now_utc = datetime.datetime.now(timezone.utc)
        paid_count = 0
        for spec in active_spectators:
            try:
                # Base XP (10), Base Credits (5)
                base_xp = 10
                base_credits = 5.0

                # Chat bonus (+2 credits per full 10 messages, capped at +20)
                raw_msgs = spec.get('message_count', 0) if isinstance(spec, dict) else getattr(spec, 'message_count', 0)
                try:
                    msg_count = int(raw_msgs)
                except (TypeError, ValueError):
                    msg_count = 0
                chat_bonus = min(20.0, (msg_count // 10) * 2.0)

                # Idle bonus (+1 XP per hour of session time)
                session_hours = 0.0
                raw_joined = spec.get('joined_at') if isinstance(spec, dict) else getattr(spec, 'joined_at', None)
                if isinstance(raw_joined, datetime.datetime):
                    if raw_joined.tzinfo is None:
                        raw_joined = raw_joined.replace(tzinfo=timezone.utc)
                    session_hours = max(0.0, (now_utc - raw_joined).total_seconds() / 3600.0)

                raw_idle = spec.get('idle_hours', 0.0) if isinstance(spec, dict) else getattr(spec, 'idle_hours', 0.0)
                try:
                    idle_hrs = float(raw_idle)
                except (TypeError, ValueError):
                    idle_hrs = 0.0
                effective_hours = max(session_hours, idle_hrs)
                idle_bonus = int(effective_hours)

                total_xp = base_xp + idle_bonus
                total_credits = base_credits + chat_bonus

                nick = spec.get('nick') if isinstance(spec, dict) else getattr(spec, 'nick', '')
                net = spec.get('network') if isinstance(spec, dict) else getattr(spec, 'network', '')
                if not nick or not net:
                    continue

                # Commit payout
                apply_res = repo.apply_payout(nick, net, total_xp, total_credits)
                if asyncio.iscoroutine(apply_res) or hasattr(apply_res, '__await__'):
                    await apply_res

                # Increment idle hours
                idle_res = repo.record_idle_hours(nick, net, 1.0)
                if asyncio.iscoroutine(idle_res) or hasattr(idle_res, '__await__'):
                    await idle_res

                # Reset message count
                reset_res = repo.reset_message_count(nick, net)
                if asyncio.iscoroutine(reset_res) or hasattr(reset_res, '__await__'):
                    await reset_res

                paid_count += 1
            except Exception as spec_err:
                logger.error(f"Error processing individual spectator payout: {spec_err}")

        if paid_count == 0:
            return 0

        summary = f"[GRID DIVIDEND] Hourly accrual distributed to {paid_count} spectators."
        channel = "#automatagrid"
        if isinstance(getattr(node, 'config', None), dict) and 'channel' in node.config:
            channel = node.config['channel']
        elif hasattr(node, 'channel'):
            channel = node.channel
        send_res = node.send(f"PRIVMSG {channel} :{summary}")
        if asyncio.iscoroutine(send_res) or hasattr(send_res, '__await__'):
            await send_res
        return paid_count
    except Exception as e:
        logger.error(f"Spectator payout error on {getattr(node, 'net_name', 'node')}: {e}")
        return 0

async def spectator_payout_loop(node):
    while True:
        try:
            await asyncio.sleep(3600)
            await distribute_spectator_payout(node)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Spectator payout loop error on {getattr(node, 'net_name', 'node')}: {e}")

def start_loops(node):
    """Starts all background operational and maintenance loops for the node."""
    return [
        asyncio.create_task(hype_loop(node)),
        asyncio.create_task(ambient_event_loop(node)),
        asyncio.create_task(arena_call_loop(node)),
        asyncio.create_task(idle_payout_loop(node)),
        asyncio.create_task(power_tick_loop(node)),
        asyncio.create_task(mainframe_loop(node)),
        asyncio.create_task(auction_loop(node)),
        asyncio.create_task(economic_ticker_loop(node)),
        asyncio.create_task(hype_drop_loop(node)),
        asyncio.create_task(topic_engine_loop(node)),
        asyncio.create_task(incursion_event_loop(node)),
        asyncio.create_task(spectator_payout_loop(node)),
    ]

