# command_router.py - v1.5.0
import asyncio
import logging
import time
import datetime
from datetime import timezone
import ai_grid.core.handlers as handlers
import ai_grid.core.handlers.spectator as _spectator_handler
handlers.handle_spectator_rename = _spectator_handler.handle_spectator_rename
from ai_grid.grid_utils import format_text, tag_msg, C_CYAN, C_YELLOW, C_GREEN, C_RED
from ai_grid.core.validation import (
    validate_nickname, validate_node_name, validate_direction,
    validate_item_name, validate_quantity, validate_token
)
from sqlalchemy import func
from sqlalchemy.future import select
from ai_grid.models import Character, Player, NetworkAlias, GridNode

logger = logging.getLogger("manager")

def find_pending_challenge(node, nick: str):
    """Finds pending challenge matching nickname (via direct key, session, or defender name)."""
    pending = getattr(node, 'pending_challenges', {}) or {}
    nick_lower = nick.lower()
    if nick_lower in pending:
        return nick_lower, pending[nick_lower]
    sess_char = getattr(node, 'authenticated_sessions', {}).get(nick_lower)
    if sess_char and sess_char.lower() in pending:
        return sess_char.lower(), pending[sess_char.lower()]
    for k, chal in pending.items():
        defender_names = [
            chal.get('defender', '').lower(),
            chal.get('defender_nick', '').lower(),
            chal.get('defender_char', '').lower()
        ]
        if nick_lower in defender_names or (sess_char and sess_char.lower() in defender_names):
            return k, chal
    return None, None

class CommandRouter:
    def __init__(self, node):
        self.node = node

    async def dispatch(self, source_nick, command, target, msg, is_admin):
        # 1. Nickname Format Guard
        if not validate_nickname(source_nick):
            logger.warning(f"SECURITY: Dropping command from invalid nickname: {source_nick!r}")
            return

        # 2. Inbound Message Sanitization & Bounding
        msg = msg.replace("\r", "").replace("\n", "").strip()
        if len(msg) > 510:
            msg = msg[:510]

        prefix = self.node.prefix
        bot_nick = (self.node.config.get('nickname') or '').lower()
        reply_target = source_nick if target.lower() == bot_nick else target

        # 3. Active Combat Routing (prefixed or non-prefixed)
        active_engine = getattr(self.node, "active_engine", None)
        combat_actor = None
        if active_engine and getattr(active_engine, "active", False):
            combat_actor = active_engine.get_entity(source_nick) if hasattr(active_engine, 'get_entity') else (
                active_engine.entities.get(source_nick) or next((e for k, e in active_engine.entities.items() if k.lower() == source_nick.lower()), None)
            )
            if not combat_actor:
                sess_char = getattr(self.node, 'authenticated_sessions', {}).get(source_nick.lower())
                if sess_char:
                    combat_actor = active_engine.get_entity(sess_char) if hasattr(active_engine, 'get_entity') else (
                        active_engine.entities.get(sess_char) or next((e for k, e in active_engine.entities.items() if k.lower() == sess_char.lower()), None)
                    )

        in_active_combat = combat_actor is not None and combat_actor.is_alive

        if in_active_combat:
            candidate = msg.strip()
            if candidate.lower().startswith(f"{prefix} "):
                candidate = candidate[len(prefix):].strip()
            parts = candidate.split()
            if parts:
                verb_candidate = parts[0].lower()
                if hasattr(active_engine, "is_combat_verb") and active_engine.is_combat_verb(verb_candidate):
                    active_engine.queue_command(combat_actor.name, msg)
                    return

        # Handle Game Commands (prefixed with configured prefix or !a)
        matched_prefix = None
        for p in [prefix, "!a"]:
            if msg.lower().startswith(f"{p} "):
                matched_prefix = p
                break

        if matched_prefix:
            parts = msg.split()
            if len(parts) < 2: return
            verb = parts[1].lower()
            args = parts[2:]

            # --- CENTRAL ANTI-FLOOD ENFORCEMENT ---
            # All game commands consume tokens immediately. Admin commands are exempt.
            admin_verbs = ["admin", "topic", "broadcast", "shutdown", "status", "restart"]
            if not is_admin and verb not in admin_verbs:
                if not await handlers.base.check_rate_limit(self.node, source_nick, reply_target, consume=True):
                    return

            # 1. Registration & Authentication
            if verb == "register":
                asyncio.create_task(handlers.handle_registration(self.node, source_nick, args, reply_target))
            elif verb == "login":
                asyncio.create_task(handlers.handle_login(self.node, source_nick, args, reply_target))

            # 2. Grid Navigation & Exploration
            elif verb == "grid":
                if args and args[0].lower() == "map":
                    asyncio.create_task(handlers.handle_grid_map(self.node, source_nick, reply_target, args[1:]))
                elif args and args[0].lower() == "claimed":
                    asyncio.create_task(handlers.handle_grid_claimed(self.node, source_nick, args, reply_target))
                elif args and args[0].lower() == "net":
                    # Aliased via x grid net <network> ...
                    net_subargs = args[1:]
                    if not net_subargs:
                        await self.node.send(f"PRIVMSG {reply_target} :[ERR] Syntax: {prefix} grid net <network> <explore|probe|hack|siphon|exploit|raid|claim|upgrade|repair|status|stash|msg> OR {prefix} grid net <affinity>")
                    elif net_subargs[0].lower() in ["state", "mode", "open", "closed", "stealth"]:
                        asyncio.create_task(handlers.handle_remote_net_command(self.node, source_nick, reply_target, net_subargs[0], net_subargs[1:]))
                    elif len(net_subargs) == 1:
                        asyncio.create_task(handlers.handle_grid_command(self.node, source_nick, reply_target, "net", net_subargs))
                    else:
                        asyncio.create_task(handlers.handle_remote_net_command(self.node, source_nick, reply_target, net_subargs[0], net_subargs[1:]))
                elif args and args[0].lower() in ["mode", "state"]:
                    if len(args) > 1:
                        asyncio.create_task(handlers.handle_remote_net_command(self.node, source_nick, reply_target, "state", args[1:]))
                    else:
                        await self.node.send(f"PRIVMSG {reply_target} :[ERR] Syntax: {prefix} grid state <OPEN|CLOSED|STEALTH>")
                elif args and args[0].lower() in ["probe", "install", "bolster", "link", "siphon", "hardware", "hw", "hack", "exploit", "rename"]:
                    # !a grid <action> <args>
                    if args[0].lower() in ["hardware", "hw"]:
                        asyncio.create_task(handlers.handle_grid_hardware(self.node, source_nick, reply_target, args[1].lower() if len(args) > 1 else None, args[2:]))
                    elif args[0].lower() == "exploit":
                        asyncio.create_task(handlers.handle_node_exploit(self.node, source_nick, reply_target, args[1:]))
                    else:
                        asyncio.create_task(handlers.handle_grid_command(self.node, source_nick, reply_target, args[0].lower(), args[1:]))
                else:
                    asyncio.create_task(handlers.handle_grid_view(self.node, source_nick, reply_target))
            elif verb == "move":
                if not self.node.active_engine or not self.node.active_engine.active:
                    if not args:
                        await self.node.send(f"PRIVMSG {reply_target} :[ERR] Provide a direction.")
                        return
                    direction = validate_direction(args[0])
                    # Check bridge affinity if direction is not standard cardinal
                    if not direction:
                        loc = await self.node.db.get_location(source_nick, self.node.net_name)
                        if loc and loc.get('net_affinity') and args[0].lower() == loc['net_affinity'].lower():
                            direction = args[0]
                    if direction:
                        asyncio.create_task(handlers.handle_grid_movement(self.node, source_nick, direction, reply_target))
                    else:
                        await self.node.send(f"PRIVMSG {reply_target} :[ERR] Invalid direction '{args[0]}'. Allowed: north, south, east, west, up, down.")
                else:
                    await self.node.send(f"PRIVMSG {reply_target} :[ERR] You are locked in combat!")
            elif verb == "explore":
                asyncio.create_task(handlers.handle_node_explore(self.node, source_nick, reply_target))

            # 3. Economy & Items
            elif verb == "shop":
                asyncio.create_task(handlers.handle_shop_view(self.node, source_nick, reply_target))
            elif verb in ["buy", "sell"]:
                if not self.node.active_engine or not self.node.active_engine.active:
                    item_arg = " ".join(args).strip()
                    if item_arg and validate_item_name(item_arg):
                        asyncio.create_task(handlers.handle_merchant_tx(self.node, source_nick, verb, item_arg, reply_target))
                    else:
                        await self.node.send(f"PRIVMSG {reply_target} :[ERR] Syntax: {prefix} {verb} <valid_item_name>")
                else:
                    await self.node.send(f"PRIVMSG {reply_target} :[ERR] Locked in combat!")
            
            # --- PHASE 2 RECOUP ---
            elif verb == "powergen":
                asyncio.create_task(handlers.handle_powergen(self.node, source_nick, reply_target))
            elif verb == "train":
                asyncio.create_task(handlers.handle_training(self.node, source_nick, reply_target))
            elif verb == "rep":
                asyncio.create_task(handlers.handle_rep_view(self.node, source_nick, reply_target))
            elif verb in ["bounty", "bounties"]:
                asyncio.create_task(handlers.handle_bounty(self.node, source_nick, args, reply_target))
            elif verb in ["skill", "skills"]:
                asyncio.create_task(handlers.handle_skill(self.node, source_nick, args, reply_target))
            elif verb == "craft":
                asyncio.create_task(handlers.handle_craft(self.node, source_nick, args, reply_target))
            elif verb == "stash":
                asyncio.create_task(handlers.handle_grid_command(self.node, source_nick, reply_target, "stash", args))

            elif verb == "net":
                if not args:
                    await self.node.send(f"PRIVMSG {reply_target} :[ERR] Syntax: {prefix} net <network> <explore|probe|hack|siphon|exploit|raid|claim|upgrade|repair|status|stash|msg> OR {prefix} net state <OPEN|CLOSED|STEALTH>")
                elif args[0].lower() in ["state", "mode", "open", "closed", "stealth"]:
                    asyncio.create_task(handlers.handle_remote_net_command(self.node, source_nick, reply_target, args[0], args[1:]))
                elif len(args) == 1:
                    asyncio.create_task(handlers.handle_grid_command(self.node, source_nick, reply_target, "net", args))
                else:
                    asyncio.create_task(handlers.handle_remote_net_command(self.node, source_nick, reply_target, args[0], args[1:]))

            # 4. Grid Interaction (Claim, Upgrade, etc.)
            elif verb in ["claim", "upgrade", "repair", "recharge", "raid", "breach", "hack", "probe", "siphon", "install", "bolster", "link", "rename", "stash"]:
                if verb in ["raid", "breach"]:
                    asyncio.create_task(handlers.handle_grid_loot(self.node, source_nick, reply_target, args))
                elif verb == "siphon" and args and args[0].lower() == "grid":
                    # Backward compatibility for !a siphon grid
                    asyncio.create_task(handlers.handle_grid_command(self.node, source_nick, reply_target, "siphon", args[1:]))
                elif verb == "hack" and args and args[0].lower() != "grid":
                    # Disambiguation: !a hack <nick> routes to PvP hack
                    if validate_nickname(args[0]):
                        asyncio.create_task(handlers.handle_pvp_command(self.node, source_nick, reply_target, "hack", args[0]))
                    else:
                        await self.node.send(f"PRIVMSG {reply_target} :[ERR] Invalid target nickname '{args[0]}'.")
                else:
                    asyncio.create_task(handlers.handle_grid_command(self.node, source_nick, reply_target, verb, args))
            
            # --- TASK 022: INTERACTIVE PULSES & WORLD EVENTS ---
            elif verb in ["collect", "patch"]:
                asyncio.create_task(handlers.handle_pulse_resolve(self.node, source_nick, reply_target, verb, args))
                
            elif verb == "defend":
                asyncio.create_task(handlers.handle_incursion_defend(self.node, source_nick, args, reply_target))
            
            elif verb == "memos":
                asyncio.create_task(handlers.handle_memos(self.node, source_nick, args, reply_target))

            elif verb == "grid" and len(args) >= 3 and args[0].lower() == "network" and args[1].lower() == "msg":
                # !a grid network msg <nick> <msg>
                asyncio.create_task(handlers.handle_grid_network_msg(self.node, source_nick, args, reply_target))

            # --- PHASE 4: THE GIBSON ---
            elif verb in ["gibson", "mainframe"]:
                asyncio.create_task(handlers.handle_gibson_status(self.node, source_nick, reply_target))
            elif verb == "compile":
                asyncio.create_task(handlers.handle_gibson_compile(self.node, source_nick, args, reply_target))
            elif verb == "assemble":
                asyncio.create_task(handlers.handle_gibson_assemble(self.node, source_nick, reply_target))
            elif verb == "exploit" and (not args or args[0].lower() != "grid"):
                # Handle !a exploit <nick> (Combat)
                if args: asyncio.create_task(handlers.handle_pvp_command(self.node, source_nick, reply_target, "exploit", args[0]))
                else: await self.node.send(f"PRIVMSG {reply_target} :[ERR] Syntax: {prefix} exploit <nick> OR {prefix} grid exploit")


            # --- PHASE 5: GLOBAL ECONOMY & MINI-GAMES ---
            elif verb == "auction":
                asyncio.create_task(handlers.handle_auction(self.node, source_nick, args, reply_target))
            elif verb == "market":
                asyncio.create_task(handlers.handle_market_view(self.node, source_nick, reply_target))
            elif verb == "dice":
                asyncio.create_task(handlers.handle_dice_roll(self.node, source_nick, args, reply_target))
            elif verb == "cipher":
                asyncio.create_task(handlers.handle_cipher_start(self.node, source_nick, reply_target))
            elif verb == "guess" or (verb == "a" and len(args) > 0 and args[0] == "guess"):
                # Handle both !a guess and potentially nested calls
                asyncio.create_task(handlers.handle_guess(self.node, source_nick, args, reply_target))
            elif verb in ["leaderboard", "highrollers", "top"]:
                asyncio.create_task(handlers.handle_leaderboard(self.node, source_nick, args, reply_target))

            # --- PHASE 6: MAP & SPECTATOR ---
            elif verb == "spectator" or (verb == "a" and len(args) > 0 and args[0].lower() == "spectator"):
                spec_args = args[1:] if verb == "a" else args
                if not spec_args:
                    asyncio.create_task(self.handle_spectator_session_command(source_nick))
                else:
                    sub = spec_args[0].lower()
                    if sub == "stats":
                        asyncio.create_task(self.handle_spectator_stats_command(source_nick))
                    elif sub == "drop":
                        asyncio.create_task(handlers.handle_spectator_drop(self.node, source_nick, spec_args[1:], reply_target))
                    elif sub == "inventory":
                        asyncio.create_task(handlers.handle_spectator_inventory(self.node, source_nick, reply_target))
                    elif sub == "rename":
                        asyncio.create_task(handlers.handle_spectator_rename(self.node, source_nick, spec_args[1:], reply_target))
                    else:
                        asyncio.create_task(self.handle_spectator_session_command(source_nick))
            elif verb == "drop":
                asyncio.create_task(handlers.handle_spectator_drop(self.node, source_nick, args, reply_target))
            elif verb == "help":
                if args and args[0] == "grid": await handlers.handle_help(self.node, source_nick, ["grid"], reply_target)
                elif args and args[0] == "spectator": await handlers.handle_spectator_help(self.node, source_nick, reply_target)
                else: await handlers.handle_help(self.node, source_nick, args, reply_target)
            elif verb == "map":
                asyncio.create_task(handlers.handle_grid_map(self.node, source_nick, reply_target, args))

            # 5. Combat & Mob Encounters
            elif verb in ["attack", "hack", "rob", "exploit"] and len(args) > 0 and args[0].lower() != "grid":
                target_nick = args[0]
                if validate_nickname(target_nick):
                    asyncio.create_task(handlers.handle_pvp_command(self.node, source_nick, reply_target, verb, target_nick))
                else:
                    await self.node.send(f"PRIVMSG {reply_target} :[ERR] Invalid target nickname '{target_nick}'.")
            elif verb == "engage":
                chal_key, chal = find_pending_challenge(self.node, source_nick)
                pending_enc = getattr(self.node, 'pending_encounters', {}) or {}
                if chal_key:
                    asyncio.create_task(handlers.resolve_pvp_challenge(self.node, chal_key, reply_target, accept=True))
                elif source_nick in pending_enc or source_nick.lower() in [k.lower() for k in pending_enc]:
                    enc_key = source_nick if source_nick in pending_enc else next((k for k in pending_enc if k.lower() == source_nick.lower()), source_nick)
                    asyncio.create_task(handlers.resolve_mob(self.node, enc_key, reply_target))
                else:
                    await self.node.send(f"PRIVMSG {reply_target} :{tag_msg(format_text('No enemy to engage.', C_RED), tags=['INFO', source_nick], nick=source_nick)}")
            elif verb == "flee":
                chal_key, chal = find_pending_challenge(self.node, source_nick)
                pending_enc = getattr(self.node, 'pending_encounters', {}) or {}
                if chal_key:
                    asyncio.create_task(handlers.resolve_pvp_challenge(self.node, chal_key, reply_target, accept=False))
                elif source_nick in pending_enc or source_nick.lower() in [k.lower() for k in pending_enc]:
                    enc_key = source_nick if source_nick in pending_enc else next((k for k in pending_enc if k.lower() == source_nick.lower()), source_nick)
                    enc = pending_enc.pop(enc_key, None)
                    if enc:
                        if enc.get('timer') and not enc['timer'].done():
                            enc['timer'].cancel()
                        prev = enc.get('prev_node')
                        if not prev and hasattr(self.node, 'db') and hasattr(self.node.db, 'async_session'):
                            async with self.node.db.async_session() as session:
                                spawn = (await session.execute(
                                    select(GridNode).where(GridNode.is_spawn_node == True)
                                )).scalars().first()
                                if not spawn:
                                    spawn = (await session.execute(
                                        select(GridNode).where(GridNode.node_type == "safezone")
                                    )).scalars().first()
                                if spawn:
                                    prev = spawn.name
                        if prev: await self.node.db.move_player_to_node(source_nick, self.node.net_name, prev)
                        machine = await handlers.is_machine_mode(self.node, source_nick)
                        if machine: await self.node.send(f"PRIVMSG {source_nick} :[MOB] RESULT:FLED NODE:{prev or 'unknown'}")
                        else:
                            safe_loc = prev if prev else "safety"
                            await self.node.send(f"PRIVMSG {reply_target} :{tag_msg(format_text(f'🏃 You fled back to {safe_loc}.', C_CYAN), tags=['COMBAT', source_nick])}")
                else:
                    await self.node.send(f"PRIVMSG {reply_target} :{tag_msg(format_text('No active encounter to flee from.', C_RED), tags=['INFO', source_nick], nick=source_nick)}")

            # 6. Arena
            elif verb == "queue":
                # 1. Check account registration
                async with self.node.db.async_session() as session:
                    char = await self.node.db.get_character_by_nick(source_nick, self.node.net_name, session) if hasattr(self.node.db, 'get_character_by_nick') else None
                    if not char:
                        stmt2 = select(Character).where(func.lower(Character.name) == source_nick.lower())
                        char = (await session.execute(stmt2)).scalars().first()

                if not char:
                    await self.node.send(f"PRIVMSG {reply_target} :[ERR] Unregistered user. Please create an account first: !a register <name> <race> <class> <traits>")
                    return

                # 2. Check authentication status
                is_auth = (
                    (hasattr(self.node, 'is_user_verified') and self.node.is_user_verified(source_nick))
                    or (source_nick.lower() in getattr(self.node, 'authenticated_users', set()))
                    or (source_nick.lower() in getattr(self.node, 'authenticated_sessions', {}))
                )
                if not is_auth:
                    await self.node.send(f"PRIVMSG {reply_target} :[ERR] Authentication required. Please log in first: !a login <name> <key>")
                    return

                # 3. Check eligible grid node location ([ARN], claimed, friendly)
                loc = await self.node.db.get_location(source_nick, self.node.net_name)
                if not loc and char:
                    loc = await self.node.db.get_location(char.name, self.node.net_name)
                if not loc:
                    await self.node.send(f"PRIVMSG {reply_target} :[ERR] Queue only available at Arena [ARN], claimed nodes, or friendly nodes.")
                    return

                is_arn = ((loc.get('type') or '').lower() == 'arena') or ((loc.get('region_type') or '').upper() == 'ARN')
                is_claimed = bool(loc.get('owner') and loc.get('owner').lower() not in ['unclaimed', 'none', ''])
                is_friendly = ((loc.get('type') or '').lower() == 'safezone') or ((loc.get('region_type') or '').upper() in ['CIV', 'EDU', 'MED', 'ORG'])

                if not (is_arn or is_claimed or is_friendly):
                    await self.node.send(f"PRIVMSG {reply_target} :[ERR] Queue only available at Arena [ARN], claimed nodes, or friendly nodes.")
                    return

                if source_nick not in self.node.match_queue:
                    self.node.match_queue.append(source_nick)
                    sig = format_text(f"{source_nick} stepped into the Gladiator Queue!", C_YELLOW)
                    reward = await self.node.db.player.complete_task(source_nick, self.node.net_name, "Queue in Arena")
                    if reward: sig += f"\n{reward}"
                    await self.node.send(f"PRIVMSG {self.node.config['channel']} :{tag_msg(sig, tags=['ARENA', 'SIGACT'])}")
                await self.node.send(f"PRIVMSG {reply_target} :{tag_msg(f'{source_nick} queued. Use {prefix} ready to confirm.', tags=['ARENA', 'SIGACT', source_nick])}")

            elif verb == "ready":
                token = args[0] if (args and validate_token(args[0])) else None
                asyncio.create_task(handlers.handle_ready(self.node, source_nick, token, reply_target))
            elif verb in ["bet", "gamble"] or (verb == "arena" and args and args[0].lower() == "bet"):
                bet_args = args[1:] if verb == "arena" else args
                asyncio.create_task(handlers.handle_bet(self.node, source_nick, bet_args, reply_target))

            # 7. Information & Meta
            elif verb in ["info", "help", "?"]:
                if verb == "info":
                    asyncio.create_task(handlers.handle_info_nick(self.node, source_nick, args, reply_target))
                else:
                    asyncio.create_task(handlers.handle_help(self.node, source_nick, args, reply_target))
            elif verb == "tasks":
                asyncio.create_task(handlers.handle_tasks_view(self.node, source_nick, reply_target))
            elif verb == "options":
                asyncio.create_task(handlers.handle_options(self.node, source_nick, args, reply_target))
            elif verb == "stats":
                asyncio.create_task(handlers.handle_stats(self.node, source_nick, args, reply_target))
            elif verb == "news":
                asyncio.create_task(handlers.handle_news_view(self.node, source_nick, reply_target))
            elif verb == "version":
                v = "[MODULES] manager: v1.5.0 | grid_db: v3.0.0 | core.handlers: v1.0.0"
                await self.node.send(f"PRIVMSG {reply_target} :{tag_msg(v, tags=['SIGINT'])}")
            elif verb == "ping":
                ts = str(time.time())
                self.node.pending_pings[ts] = {'source': source_nick, 'reply_target': reply_target, 'start': float(ts), 'client_latency': None, 'server_latency': None}
                await self.node.send(f"PRIVMSG {source_nick} :\x01PING {ts}\x01")
                await self.node.send(f"PING {ts}")

            # --- PHASE 8: OSINT & ANALYTICS (Task 019) ---
            elif verb == "economy":
                asyncio.create_task(handlers.handle_economy_osint(self.node, source_nick, reply_target))
            elif verb == "gridpower":
                asyncio.create_task(handlers.handle_gridpower_osint(self.node, source_nick, reply_target))
            elif verb == "gridstability":
                asyncio.create_task(handlers.handle_gridstability_osint(self.node, source_nick, reply_target))
            elif verb == "networks":
                asyncio.create_task(handlers.handle_networks_osint(self.node, source_nick, reply_target))
            elif verb == "about":
                asyncio.create_task(handlers.handle_about_osint(self.node, source_nick, reply_target))

            # 8. Admin Commands
            elif verb in ["admin", "topic", "broadcast", "shutdown", "status", "restart"]:
                if verb == "admin" and args and args[0].lower() in ["auth", "deauth"]:
                    asyncio.create_task(handlers.handle_admin_command(self.node, source_nick, verb, args, reply_target))
                else:
                    authorized, reason = self.node.check_admin_privilege(source_nick)
                    if authorized:
                        asyncio.create_task(handlers.handle_admin_command(self.node, source_nick, verb, args, reply_target))
                    else:
                        if reason == "NOT_VERIFIED":
                            from ai_grid.core.security import request_nickserv_check
                            asyncio.create_task(request_nickserv_check(self.node, source_nick))
                            logger.warning(f"UNAUTHORIZED ADMIN ATTEMPT: '{source_nick}' attempted '{verb}' but is not NickServ (+r) verified.")
                            await self.node.send(f"PRIVMSG {reply_target} :[ERR] Access Denied: NickServ authentication (+r) required. Verification check initiated.")
                        elif reason == "SESSION_REQUIRED":
                            logger.warning(f"UNAUTHORIZED ADMIN ATTEMPT: NickServ-verified admin '{source_nick}' attempted '{verb}' without active token session.")
                            await self.node.send(f"PRIVMSG {reply_target} :[ERR] Access Denied: Admin session token required. Send '{prefix} admin auth <token>' via PM.")
                        else:
                            logger.warning(f"UNAUTHORIZED ADMIN ATTEMPT: Non-admin '{source_nick}' attempted '{verb} {' '.join(args)}' from {reply_target}.")
                            await self.node.send(f"PRIVMSG {reply_target} :[ERR] Access Denied.")

        # Handle Active Combat Commands (non-prefixed fallback)
        elif in_active_combat:
            active_engine.queue_command(combat_actor.name, msg)

        # Handle Non-Prefixed Special Verbs (engage, flee, login, ready)
        else:
            clean_msg = msg.strip()
            parts = clean_msg.split()
            if parts:
                verb_candidate = parts[0].lower()
                if verb_candidate == "engage":
                    chal_key, chal = find_pending_challenge(self.node, source_nick)
                    pending_enc = getattr(self.node, 'pending_encounters', {}) or {}
                    if chal_key:
                        asyncio.create_task(handlers.resolve_pvp_challenge(self.node, chal_key, reply_target, accept=True))
                    elif source_nick in pending_enc or source_nick.lower() in [k.lower() for k in pending_enc]:
                        enc_key = source_nick if source_nick in pending_enc else next((k for k in pending_enc if k.lower() == source_nick.lower()), source_nick)
                        asyncio.create_task(handlers.resolve_mob(self.node, enc_key, reply_target))
                    else:
                        await self.node.send(f"PRIVMSG {reply_target} :{tag_msg(format_text('No enemy to engage.', C_RED), tags=['INFO', source_nick], nick=source_nick)}")
                elif verb_candidate == "flee":
                    chal_key, chal = find_pending_challenge(self.node, source_nick)
                    pending_enc = getattr(self.node, 'pending_encounters', {}) or {}
                    if chal_key:
                        asyncio.create_task(handlers.resolve_pvp_challenge(self.node, chal_key, reply_target, accept=False))
                    elif source_nick in pending_enc or source_nick.lower() in [k.lower() for k in pending_enc]:
                        enc_key = source_nick if source_nick in pending_enc else next((k for k in pending_enc if k.lower() == source_nick.lower()), source_nick)
                        enc = pending_enc.pop(enc_key, None)
                        if enc:
                            if enc.get('timer') and not enc['timer'].done():
                                enc['timer'].cancel()
                            prev = enc.get('prev_node')
                            if not prev and hasattr(self.node, 'db') and hasattr(self.node.db, 'async_session'):
                                async with self.node.db.async_session() as session:
                                    spawn = (await session.execute(
                                        select(GridNode).where(GridNode.is_spawn_node == True)
                                    )).scalars().first()
                                    if not spawn:
                                        spawn = (await session.execute(
                                            select(GridNode).where(GridNode.node_type == "safezone")
                                        )).scalars().first()
                                    if spawn:
                                        prev = spawn.name
                            if prev: await self.node.db.move_player_to_node(source_nick, self.node.net_name, prev)
                            machine = await handlers.is_machine_mode(self.node, source_nick)
                            if machine: await self.node.send(f"PRIVMSG {source_nick} :[MOB] RESULT:FLED NODE:{prev or 'unknown'}")
                            else:
                                safe_loc = prev if prev else "safety"
                                await self.node.send(f"PRIVMSG {reply_target} :{tag_msg(format_text(f'🏃 You fled back to {safe_loc}.', C_CYAN), tags=['COMBAT', source_nick])}")
                    else:
                        await self.node.send(f"PRIVMSG {reply_target} :{tag_msg(format_text('No active encounter to flee from.', C_RED), tags=['INFO', source_nick], nick=source_nick)}")
                elif verb_candidate == "login":
                    asyncio.create_task(handlers.handle_login(self.node, source_nick, parts[1:], reply_target))
                elif verb_candidate == "ready":
                    token = parts[1] if (len(parts) > 1 and validate_token(parts[1])) else None
                    asyncio.create_task(handlers.handle_ready(self.node, source_nick, token, reply_target))

    async def handle_spectator_session_command(self, source_nick: str):
        try:
            repo = getattr(self.node.db, 'spectator_repo', None)
            if repo is None:
                from ai_grid.database.spectator_repo import SpectatorRepository
                repo = SpectatorRepository(self.node.db.async_session)

            spec_res = repo.upsert_spectator(source_nick, self.node.net_name)
            if asyncio.iscoroutine(spec_res) or hasattr(spec_res, '__await__'):
                spec = await spec_res
            else:
                spec = spec_res

            now_utc = datetime.datetime.now(timezone.utc)
            elapsed_seconds = 0.0
            raw_joined = spec.get('joined_at') if isinstance(spec, dict) else getattr(spec, 'joined_at', None)
            if isinstance(raw_joined, datetime.datetime):
                if raw_joined.tzinfo is None:
                    raw_joined = raw_joined.replace(tzinfo=datetime.timezone.utc)
                elapsed_seconds = max(0.0, (now_utc - raw_joined).total_seconds())

            nick_lower = source_nick.lower()
            if hasattr(self.node, 'channel_users') and isinstance(self.node.channel_users, dict) and nick_lower in self.node.channel_users:
                c_data = self.node.channel_users[nick_lower]
                if isinstance(c_data, dict) and 'join_time' in c_data:
                    try:
                        c_elapsed = max(0.0, time.time() - float(c_data['join_time']))
                        elapsed_seconds = max(elapsed_seconds, c_elapsed)
                    except (TypeError, ValueError):
                        pass

            elapsed_time = str(datetime.timedelta(seconds=int(elapsed_seconds)))
            raw_count = spec.get('message_count', 0) if isinstance(spec, dict) else getattr(spec, 'message_count', 0)
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                count = 0

            hours = elapsed_seconds / 3600.0
            rate = (count / hours) if hours > 0 else 0.0
            msg = f"[SPECTATOR] {source_nick} | Session: {elapsed_time} | Messages: {count} | Rate: {rate:.1f}"
            send_res = self.node.send(f"PRIVMSG {source_nick} :{msg}")
            if asyncio.iscoroutine(send_res) or hasattr(send_res, '__await__'):
                await send_res
        except Exception as e:
            logger.error(f"Error handling spectator command for {source_nick}: {e}")

    async def handle_spectator_stats_command(self, source_nick: str):
        try:
            repo = getattr(self.node.db, 'spectator_repo', None)
            if repo is None:
                from ai_grid.database.spectator_repo import SpectatorRepository
                repo = SpectatorRepository(self.node.db.async_session)

            spec_res = repo.upsert_spectator(source_nick, self.node.net_name)
            if asyncio.iscoroutine(spec_res) or hasattr(spec_res, '__await__'):
                spec = await spec_res
            else:
                spec = spec_res

            raw_xp = spec.get('xp', 0) if isinstance(spec, dict) else getattr(spec, 'xp', 0)
            try:
                xp = int(raw_xp)
            except (TypeError, ValueError):
                xp = 0

            raw_creds = spec.get('credits', 0.0) if isinstance(spec, dict) else getattr(spec, 'credits', 0.0)
            try:
                creds_f = float(raw_creds)
                creds = int(creds_f) if creds_f.is_integer() else creds_f
            except (TypeError, ValueError):
                creds = 0

            raw_idle = spec.get('idle_hours', 0.0) if isinstance(spec, dict) else getattr(spec, 'idle_hours', 0.0)
            try:
                idle_hours = float(raw_idle)
            except (TypeError, ValueError):
                idle_hours = 0.0

            raw_lifetime = None
            if isinstance(spec, dict):
                raw_lifetime = spec.get('lifetime_messages', spec.get('message_count', 0))
            else:
                raw_lifetime = getattr(spec, 'lifetime_messages', getattr(spec, 'message_count', 0))
            try:
                lifetime_msgs = int(raw_lifetime)
            except (TypeError, ValueError):
                lifetime_msgs = 0

            msg = f"[SPECTATOR STATS] {source_nick} | XP: {xp} | Credits: {creds}c | Idle Hours: {idle_hours:.1f}h | Messages (lifetime): {lifetime_msgs}"
            send_res = self.node.send(f"PRIVMSG {source_nick} :{msg}")
            if asyncio.iscoroutine(send_res) or hasattr(send_res, '__await__'):
                await send_res
        except Exception as e:
            logger.error(f"Error handling spectator stats command for {source_nick}: {e}")

