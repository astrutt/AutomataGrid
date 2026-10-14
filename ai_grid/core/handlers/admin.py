import asyncio
import logging
import time
import shlex
import hmac
from ai_grid.grid_utils import format_text, tag_msg, C_GREEN, C_CYAN, C_RED, C_YELLOW, C_WHITE, C_L_GREEN
from .base import get_action_routing

logger = logging.getLogger("manager")

async def handle_admin_command(node, admin_nick: str, verb, args=None, reply_target: str = None):
    if isinstance(verb, (list, tuple)):
        cmd_args = list(verb)
        verb = cmd_args[0] if cmd_args else ""
        if isinstance(args, str) and reply_target is None:
            reply_target = args
            args = cmd_args[1:]
        else:
            args = cmd_args[1:] + (list(args) if isinstance(args, (list, tuple)) else [])
    elif isinstance(args, str) and reply_target is None:
        reply_target = args
        args = []

    if args is None:
        args = []
    if reply_target is None:
        reply_target = getattr(node, 'config', {}).get('channel', '#arena') if isinstance(getattr(node, 'config', None), dict) else "#arena"

    # Logging Redaction Utility
    def mask_args(v, a):
        if v in ["nickregister", "nickidentify"] and len(a) >= 2:
            return [a[0], "********"] + a[2:]
        if v == "nickconfirm" and len(a) >= 2:
            return [a[0], "****"] + a[2:]
        if v == "admin" and len(a) >= 2 and a[0].lower() == "auth":
            return [a[0], "********"] + a[2:]
        return a

    # Special Handling for auth and deauth subcommands:
    if verb == "admin" and args and args[0].lower() in ["auth", "deauth"]:
        sub = args[0].lower()
        # SECURITY GATE: Disallow auth commands in public channel
        if reply_target.lower() == node.config['channel'].lower():
            logger.warning(f"SECURITY ALERT: Admin auth command attempted in public channel by {admin_nick}!")
            await node.send(f"PRIVMSG {admin_nick} :{tag_msg(format_text('[ALARM] CRITICAL: Admin authentication commands must be executed via Private Message only.', C_RED, True), tags=['ALARM'])}")
            return

        # Check admin list membership
        if admin_nick.lower() not in node.admins:
            logger.warning(f"UNAUTHORIZED AUTH ATTEMPT: Non-admin {admin_nick} attempted admin {sub}.")
            await node.send(f"PRIVMSG {admin_nick} :[ERR] Access Denied.")
            return

        # Check NickServ verification
        if admin_nick.lower() not in node.nickserv_verified:
            from ai_grid.core.security import request_nickserv_check
            asyncio.create_task(request_nickserv_check(node, admin_nick))
            logger.warning(f"UNAUTHORIZED AUTH ATTEMPT: {admin_nick} is not NickServ verified.")
            await node.send(f"PRIVMSG {admin_nick} :[ERR] Access Denied: NickServ authentication (+r) required before session auth.")
            return

        if sub == "deauth":
            node.admin_sessions.pop(admin_nick.lower(), None)
            logger.info(f"SYSADMIN DEAUTH: {admin_nick} terminated admin session.")
            await node.send(f"PRIVMSG {admin_nick} :{tag_msg(format_text('[SYSADMIN] Admin session terminated.', C_YELLOW), tags=['SIGACT'], nick=admin_nick)}")
            return

        if sub == "auth":
            if len(args) < 2:
                await node.send(f"PRIVMSG {admin_nick} :[ERR] Syntax: {node.prefix} admin auth <token>")
                return

            provided_token = args[1]
            from ai_grid.manager import CONFIG
            configured_token = CONFIG.get('admin_token') or getattr(node, 'config', {}).get('admin_token')
            if not configured_token and hasattr(node, 'hub') and node.hub.nodes:
                configured_token = node.hub.nodes.get(node.net_name, node).config.get('admin_token')

            if not configured_token:
                await node.send(f"PRIVMSG {admin_nick} :[INFO] Admin token auth is not configured on this server (NickServ +r is sufficient).")
                return

            if hmac.compare_digest(str(provided_token), str(configured_token)):
                node.admin_sessions[admin_nick.lower()] = time.time() + 3600  # 60 minute TTL
                logger.info(f"SYSADMIN AUTH: Admin {admin_nick} successfully established 60m admin session.")
                await node.send(f"PRIVMSG {admin_nick} :{tag_msg(format_text('[SYSADMIN] Authentication successful. Admin session active for 60 minutes.', C_GREEN, True), tags=['SIGACT'], nick=admin_nick)}")
            else:
                logger.warning(f"SYSADMIN AUTH FAILURE: Admin {admin_nick} provided invalid token.")
                await node.send(f"PRIVMSG {admin_nick} :[ERR] Authentication failed: Invalid token.")
            return

    # Check NickServ verification first
    if hasattr(node, "is_user_verified") and callable(node.is_user_verified):
        if not node.is_user_verified(admin_nick):
            logger.warning(f"UNAUTHORIZED ADMIN ATTEMPT: {admin_nick} lacks NickServ verification.")
            await node.send(f"PRIVMSG {reply_target} :[ERR] Access Denied: NickServ authentication (+r) required. [AUTH_DENIED]")
            return

    # Check admin privileges for all other administrative operations
    if hasattr(node, "is_admin") and callable(node.is_admin):
        if not node.is_admin(admin_nick):
            logger.warning(f"SYSADMIN REJECT: {admin_nick} attempted '{verb}' without active authorization.")
            await node.send(f"PRIVMSG {reply_target} :[ERR] Access Denied.")
            return
    elif hasattr(node, "admins"):
        admin_list = [a.lower() for a in node.admins] if isinstance(node.admins, (list, set, tuple)) else []
        if admin_nick.lower() not in admin_list:
            logger.warning(f"SYSADMIN REJECT: {admin_nick} attempted '{verb}' without active authorization.")
            await node.send(f"PRIVMSG {reply_target} :[ERR] Access Denied.")
            return

    if verb == "help":
        await node.send(f"PRIVMSG {reply_target} :[ADMIN] Available commands: status, version, topic, broadcast, grid, map, battlestart, battlestop, restart, shutdown")
        return

    # Redacted log for INFO, full for DEBUG
    logger.info(f"SYSADMIN OVERRIDE: {admin_nick} -> {verb} {mask_args(verb, args)}")
    logger.debug(f"SYSADMIN TRACE: {admin_nick} -> {verb} {args}")
    
    private_target, broadcast_chan, machine_mode, reply_method = await get_action_routing(node, admin_nick, reply_target)
    
    # Handle !a admin <subcommand>
    if verb == "admin":
        if not args:
            # Landing Page
            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text('[ MAINFRAME ADMIN OVERRIDES ]', C_CYAN, True), tags=['SIGINT'], nick=admin_nick)}")
            cmds = ["status", "version", "topic", "broadcast <msg>", "nickregister", "nickconfirm", "nickidentify", "grid <rename|chgdesc|seed|spawn>", "map [stats|status|expand|info]", "battlestart/stop", "restart", "stop", "shutdown"]
            cmd_str = ", ".join([f"{node.prefix} admin {c}" for c in cmds])
            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(cmd_str, C_WHITE), tags=['SIGINT'], nick=admin_nick)}")
            return
        
        # Shift args
        verb = args[0].lower()
        args = args[1:]
        logger.info(f"Sub-command routing: {verb} {args}")

    if verb == "version":
        # System Versions
        await node.send(f"{reply_method} {private_target} :{tag_msg(format_text('[ SYSTEM VERSION ARCHIVE ]', C_CYAN, True), tags=['SIGINT'], nick=admin_nick)}")
        await node.send(f"{reply_method} {private_target} :{tag_msg(format_text('Mainframe Core: v1.8.0-STABLE', C_WHITE), tags=['SIGINT'], nick=admin_nick)}")
        await node.send(f"{reply_method} {private_target} :{tag_msg(format_text('DB Orchestrator: v1.8.0 | Repositories: v1.8.0', C_GREEN), tags=['SIGINT'], nick=admin_nick)}")
        await node.send(f"{reply_method} {private_target} :{tag_msg(format_text('Command Router: v1.8.0 | AI Bot Client: v1.8.0', C_YELLOW), tags=['SIGINT'], nick=admin_nick)}")
    elif verb == "status":
        # 1. Base Population & Systems
        players = await node.db.list_players(node.net_name)
        b_stat = f"ACTIVE (Turn {node.active_engine.turn})" if node.active_engine and node.active_engine.active else "STANDBY"
        
        # 2. Grid & Economy Telemetry
        grid = await node.db.get_grid_telemetry()
        econ = await node.db.get_global_economy()
        
        # 3. Uptime
        uptime_sec = time.time() - node.hub.start_time
        h = int(uptime_sec // 3600); m = int((uptime_sec % 3600) // 60)
        uptime = f"{h}h {m}m"

        # 4. Multi-line Report
        await node.send(f"{reply_method} {private_target} :{tag_msg(format_text('[ MAINFRAME TELEMETRY ]', C_CYAN, True), tags=['SIGINT'], nick=admin_nick)}")
        await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(f'UPTIME: {uptime} | STATUS: {b_stat} | BOTS: {len(players)}', C_WHITE), tags=['SIGINT'], nick=admin_nick)}")
        
        grid_msg = f"GRID: {grid['claimed_nodes']}/{grid['total_nodes']} nodes ({grid['claimed_percent']:.1f}%) | MESH: {grid['total_power']:.0f}uP"
        await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(grid_msg, C_GREEN), tags=['SIGINT'], nick=admin_nick)}")
        
        econ_msg = f"ECON: {econ['total_credits']:.0f}c Total Liquidity | {econ['total_data_units']:.1f}u Total Data"
        await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(econ_msg, C_YELLOW), tags=['SIGINT'], nick=admin_nick)}")
        
        queue_msg = f"QUEUE: {len(node.match_queue)} in line | {len(node.ready_players)} ready to drop"
        await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(queue_msg, C_CYAN), tags=['SIGINT'], nick=admin_nick)}")
    elif verb == "battlestop":
        if node.active_engine and node.active_engine.active:
            node.active_engine.active = False
            node.active_engine = None
            await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(format_text('ADMIN OVERRIDE: ACTIVE COMBAT SEQUENCE HALTED.', C_RED, True), tags=['SIGACT'], nick=admin_nick)}")
            await node.send(f"{reply_method} {private_target} :{tag_msg('Arena match aborted by administrative override.', action='MCP', result='SUCCESS')}")
        else: await node.send(f"{reply_method} {private_target} :{tag_msg('No active battle sequence detected.', action='MCP', result='FAIL')}")
    elif verb == "battlestart":
        if node.active_engine and node.active_engine.active: await node.send(f"{reply_method} {private_target} :{tag_msg('Arena locked into active match.', action='MCP', result='FAIL')}")
        elif len(node.ready_players) > 0: await node.check_match_start()
        else: await node.trigger_arena_call()
    elif verb == "topic": await node.set_dynamic_topic()
    elif verb == "broadcast":
        msg = format_text(f"[SYSADMIN OVERRIDE] {' '.join(args)}", C_YELLOW, True)
        await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(msg, tags=['SIGACT'], nick=admin_nick)}")
    elif verb == "grid":
        # Handle !a admin grid rename <old> <new>
        full_args = shlex.split(" ".join(args))
        if len(full_args) >= 3 and full_args[0].lower() == "rename":
            old_name, new_name = full_args[1], full_args[2]
            success, feedback = await node.db.rename_node(old_name, new_name)
            tag = "SIGINT" if success else "OSINT"
            await node.send(f"{reply_method} {private_target} :{tag_msg(feedback, tags=[tag], nick=admin_nick)}")
            if success:
                announcement = format_text(f"NODE REBRANDED: {old_name} is now known as {new_name}.", C_CYAN, True)
                await node.send(f"PRIVMSG {broadcast_chan} :{tag_msg(announcement, tags=['SIGACT'], nick=admin_nick)}")
        elif len(full_args) >= 2 and full_args[0].lower() == "seed":
            try: count = int(full_args[1])
            except: count = 1
            if count > 5: count = 5 # Limit per operation
            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(f'Initiating procedural grid expansion ({count} nodes)...', C_YELLOW), tags=['SIGINT'], nick=admin_nick)}")
            
            new_nodes = await node.llm.generate_grid_nodes(count)
            added_count = 0
            for n_data in new_nodes:
                # Add individual nodes to DB
                from sqlalchemy import select
                stmt = select(node.db.models.GridNode).where(node.db.models.GridNode.name == n_data['name'])
                async with node.db.async_session() as session:
                    exists = (await session.execute(stmt)).scalars().first()
                    if not exists:
                        node_obj = node.db.models.GridNode(
                            name=n_data['name'], 
                            description=n_data['desc'], 
                            node_type=n_data.get('type', 'safezone')
                        )
                        session.add(node_obj)
                        await session.commit()
                        added_count += 1
            
            feedback = f"Expansion complete. Synced {added_count} new sectors to the mesh."
            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(feedback, C_GREEN), tags=['SIGINT'], nick=admin_nick)}")
        elif len(full_args) >= 3 and full_args[0].lower() == "chgdesc":
            target_node, new_desc = full_args[1], " ".join(full_args[2:])
            success, feedback = await node.db.grid.update_node_description(target_node, new_desc)
            tag = "SIGINT" if success else "OSINT"
            await node.send(f"{reply_method} {private_target} :{tag_msg(feedback, tags=[tag], nick=admin_nick)}")
            if success:
                announcement = format_text(f"NODE ARCHITECTURE REDEFINED: {target_node} sensors updated.", C_CYAN, True)
                await node.send(f"PRIVMSG {broadcast_chan} :{tag_msg(announcement, tags=['SIGACT'], nick=admin_nick)}")
        elif full_args[0].lower() == "spawn":
            if len(full_args) >= 2:
                target_node = full_args[1]
                success, feedback = await node.db.grid.set_spawn_node(target_node)
                tag = "SIGINT" if success else "OSINT"
                await node.send(f"{reply_method} {private_target} :{tag_msg(feedback, tags=[tag], nick=admin_nick)}")
            else:
                current_spawn = await node.db.grid.get_spawn_node_name()
                await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(f'Current Grid Nexus: {current_spawn}', C_CYAN), tags=['SIGINT'], nick=admin_nick)}")
        else:
            await node.send(f"{reply_method} {private_target} :{tag_msg(f'Syntax: {node.prefix} admin grid <rename|seed|spawn> [args]', action='INFO', result='ERR')}")
    elif verb == "flood":
        if not args:
            # Display configuration
            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text('[ ANTI-FLOOD CONFIG ]', C_CYAN, True), tags=['SIGINT'], nick=admin_nick)}")
            conf = node.flood_config
            conf_str = f"Burst: {conf['max_tokens']} | Refill: {conf['refill_rate']} t/s | Threshold: {conf['violation_threshold']} | Lockout: {conf['lockout_duration']}s"
            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(conf_str, C_WHITE), tags=['SIGINT'], nick=admin_nick)}")
            
            # Display flooders
            flood_lines = []
            now = time.time()
            for n_low, rec in node.action_timestamps.items():
                if rec.get('violations', 0) > 0 or now < rec.get('lockout_until', 0) or rec.get('tokens', 0) < 1.0:
                    status = "LOCKED" if now < rec.get('lockout_until', 0) else ("LOW" if rec.get('tokens', 0) < 1.0 else "WATCH")
                    rem = int(rec.get('lockout_until', 0) - now) if status == "LOCKED" else 0
                    flood_lines.append(f"{n_low}: {status} (T:{rec.get('tokens',0):.1f} V:{rec.get('violations',0)} R:{rem}s)")
            
            if flood_lines:
                await node.send(f"{reply_method} {private_target} :{tag_msg(format_text('[ ACTIVE FLOODERS ]', C_RED, True), tags=['SIGINT'], nick=admin_nick)}")
                for line in flood_lines:
                    await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(line, C_YELLOW), tags=['SIGINT'], nick=admin_nick)}")
            else:
                await node.send(f"{reply_method} {private_target} :{tag_msg(format_text('No active flooders detected.', C_GREEN), tags=['SIGINT'], nick=admin_nick)}")
        
        elif args[0].lower() == "reset" and len(args) >= 2:
            target = args[1].lower()
            if target in node.action_timestamps:
                node.action_timestamps[target]['tokens'] = node.flood_config['max_tokens']
                node.action_timestamps[target]['violations'] = 0
                node.action_timestamps[target]['lockout_until'] = 0
                node.action_timestamps[target]['warned'] = False
                await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(f'Flood state for {target} has been reset.', C_GREEN), action='MCP', result='SUCCESS')}")
            else:
                await node.send(f"{reply_method} {private_target} :{tag_msg(f'No record for {target}.', action='MCP', result='FAIL')}")
    elif verb in ["nickregister", "nickconfirm", "nickidentify"]:
        # SECURITY GATE: Force PM for auth commands
        if reply_target.lower() == node.config['channel'].lower():
            await node.send(f"PRIVMSG {admin_nick} :{tag_msg(format_text('[SITREP] SENSITIVE AUTH: Authentication commands must be executed via Private Message.', C_RED, True), tags=['ALARM'])}")
            return

        if verb == "nickregister":
            if len(args) < 3:
                await node.send(f"PRIVMSG {admin_nick} :{tag_msg(f'[ERR] Syntax: {node.prefix} admin nickregister <network> <password> <email>', tags=['OSINT'])}")
                return
            
            target_net = args[0].lower()
            target_node = node.hub.nodes.get(target_net)
            if not target_node:
                err_msg = f"[ERR] ROUTING FAILURE: Network '{target_net}' not found."
                await node.send(f"PRIVMSG {admin_nick} :{tag_msg(err_msg, tags=['OSINT'])}")
                return

            # Report current +r status of TARGET node
            bot_nick = target_node.config['nickname'].lower()
            bot_verified = bot_nick in target_node.nickserv_verified
            status_str = format_text("ALREADY +r (REGISTERED)", C_GREEN) if bot_verified else format_text("NOT REGISTERED", C_RED)
            await node.send(f"PRIVMSG {admin_nick} :{tag_msg(f'[{target_net.upper()}] Identity Status: {status_str}', tags=['SIGINT'])}")
            
            password, email = args[1], args[2]
            await target_node.send(f"PRIVMSG NickServ :REGISTER {password} {email}", immediate=True)
            await node.send(f"PRIVMSG {admin_nick} :{tag_msg(f'Registration command emitted to {target_net} NickServ.', tags=['SIGINT'])}")
        
        elif verb == "nickconfirm":
            if len(args) >= 2:
                target_net = args[0].lower()
                target_node = node.hub.nodes.get(target_net)
                if not target_node:
                    err_msg = f"[ERR] ROUTING FAILURE: Network '{target_net}' not found."
                    await node.send(f"PRIVMSG {admin_nick} :{tag_msg(err_msg, tags=['OSINT'])}")
                    return

                code = args[1]
                await target_node.send(f"PRIVMSG NickServ :CONFIRM {code}", immediate=True)
                await node.send(f"PRIVMSG {admin_nick} :{tag_msg(f'Confirmation code emitted to {target_net} NickServ.', tags=['SIGINT'])}")
            else:
                await node.send(f"PRIVMSG {admin_nick} :{tag_msg(f'[ERR] Syntax: {node.prefix} admin nickconfirm <network> <code>', tags=['OSINT'])}")
        
        elif verb == "nickidentify":
            if len(args) >= 2:
                target_net = args[0].lower()
                target_node = node.hub.nodes.get(target_net)
                if not target_node:
                    err_msg = f"[ERR] ROUTING FAILURE: Network '{target_net}' not found."
                    await node.send(f"PRIVMSG {admin_nick} :{tag_msg(err_msg, tags=['OSINT'])}")
                    return

                password = args[1]
                await target_node.send(f"PRIVMSG NickServ :IDENTIFY {password}", immediate=True)
                await node.send(f"PRIVMSG {admin_nick} :{tag_msg(f'Manual identification emitted to {target_net} NickServ. Verification WHOIS pending...', tags=['SIGINT'])}")
                
                # Trigger a verification WHOIS after a short delay
                async def delayed_check():
                    await asyncio.sleep(5)
                    from ai_grid.core.security import request_nickserv_check
                    await request_nickserv_check(target_node, target_node.config['nickname'])
                asyncio.create_task(delayed_check())
            else:
                await node.send(f"PRIVMSG {admin_nick} :{tag_msg(f'[ERR] Syntax: {node.prefix} admin nickidentify <network> <password>', tags=['OSINT'])}")
                
        elif verb == "chantopic":
            # Handle !a admin chantopic rotate <min>
            if len(args) >= 2 and args[0].lower() == "rotate":
                try:
                    new_interval = int(args[1])
                    if new_interval < 1 and new_interval != 0:
                        raise ValueError("Interval must be >= 1 or 0.")
                    
                    if new_interval != 0:
                        node.topic_interval = new_interval
                        
                    # Manual increment mode and trigger
                    node.topic_mode = (node.topic_mode + 1) % 4
                    from ai_grid.core.arena import set_dynamic_topic
                    await set_dynamic_topic(node)
                    
                    status = f"Mode: {node.topic_mode} | Interval: {node.topic_interval}m"
                    msg = format_text(f"Topic Engine synchronized. {status}", C_CYAN)
                    await node.send(f"{reply_method} {private_target} :{tag_msg(msg, tags=['SIGINT'], nick=admin_nick)}")
                except Exception as e:
                    logger.error(f"Topic rotate error: {e}")
                    await node.send(f"{reply_method} {private_target} :{tag_msg(f'Syntax: {node.prefix} admin chantopic rotate <min>', action='INFO', result='ERR')}")
            else:
                await node.send(f"{reply_method} {private_target} :{tag_msg(f'Syntax: {node.prefix} admin chantopic rotate <min>', action='INFO', result='ERR')}")
    elif verb in ["network", "networks"]:
        # Handle !a admin network [irc network name]
        from ai_grid.manager import CONFIG
        cfg_networks = CONFIG.get('networks', {}) if isinstance(CONFIG, dict) else {}

        if not args:
            # List view: configured & connected networks, nick, reg status (+r), ping time
            all_net_keys = set(cfg_networks.keys())
            if node.hub and getattr(node.hub, 'nodes', None):
                all_net_keys.update(node.hub.nodes.keys())

            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text('[ IRC NETWORKS INFRASTRUCTURE ]', C_CYAN, True, is_machine=machine_mode), tags=['SIGINT'], nick=admin_nick, is_machine=machine_mode)}")

            if not all_net_keys:
                await node.send(f"{reply_method} {private_target} :{tag_msg('No configured or connected IRC networks found.', action='INFO', result='INFO', is_machine=machine_mode)}")
                return

            for n_key in sorted(all_net_keys):
                target_instance = node.hub.nodes.get(n_key.lower()) if node.hub and getattr(node.hub, 'nodes', None) else None
                if not target_instance and n_key.lower() == node.net_name.lower():
                    target_instance = node

                net_cfg = cfg_networks.get(n_key, {})
                if target_instance and hasattr(target_instance, 'config') and isinstance(target_instance.config, dict):
                    net_cfg = {**net_cfg, **target_instance.config}

                is_conn = target_instance.irc.is_connected() if (target_instance and hasattr(target_instance, 'irc')) else False
                conn_str = format_text("ONLINE", C_GREEN) if is_conn else format_text("OFFLINE", C_RED)

                nick = (
                    (target_instance.irc.nickname if target_instance and hasattr(target_instance, 'irc') and getattr(target_instance.irc, 'nickname', None) else None)
                    or (target_instance.config.get('nickname') if target_instance and hasattr(target_instance, 'config') else None)
                    or net_cfg.get('nickname', 'GridManager')
                )

                reg_status = "UNREGISTERED"
                if target_instance and hasattr(target_instance, 'nickserv_verified') and hasattr(target_instance, 'config'):
                    bot_nick = (target_instance.config.get('nickname') or nick).lower()
                    if bot_nick in target_instance.nickserv_verified or getattr(target_instance, 'nickserv_identified', False):
                        reg_status = "+r (IDENTIFIED)"
                reg_color = C_GREEN if "+r" in reg_status else C_RED
                reg_str = format_text(reg_status, reg_color)

                # Ping
                ping_val = getattr(target_instance, 'last_server_ping', None) if target_instance else None
                ping_str = f"{ping_val:.0f}ms" if (ping_val is not None) else "N/A"

                line_str = f"{n_key.upper()} | {conn_str} | Nick: {nick} | Reg: {reg_str} | Ping: {ping_str}"
                await node.send(f"{reply_method} {private_target} :{tag_msg(line_str, tags=['SIGINT'], nick=admin_nick, is_machine=machine_mode)}")

        else:
            # Detail view: !a admin network <name>
            target_net = args[0].lower()
            target_instance = node.hub.nodes.get(target_net) if node.hub and getattr(node.hub, 'nodes', None) else None
            if not target_instance and target_net == node.net_name.lower():
                target_instance = node

            target_cfg = cfg_networks.get(target_net) or {}
            if not target_cfg:
                # Case-insensitive match fallback
                for k, v in cfg_networks.items():
                    if k.lower() == target_net:
                        target_cfg = v
                        break

            if not target_instance and not target_cfg:
                target_arg = args[0]
                not_found_msg = '[ERR] Network "' + str(target_arg) + '" not found in configuration or active bridges.'
                formatted_err = tag_msg(not_found_msg, action='INFO', result='ERR', is_machine=machine_mode)
                await node.send(f"{reply_method} {private_target} :{formatted_err}")
                return

            merged_cfg = {**target_cfg}
            if target_instance and hasattr(target_instance, 'config') and isinstance(target_instance.config, dict):
                merged_cfg.update(target_instance.config)

            is_conn = target_instance.irc.is_connected() if (target_instance and hasattr(target_instance, 'irc')) else False
            conn_status = format_text("ONLINE", C_GREEN) if is_conn else format_text("OFFLINE", C_RED)

            nick = (
                (target_instance.irc.nickname if target_instance and hasattr(target_instance, 'irc') and getattr(target_instance.irc, 'nickname', None) else None)
                or merged_cfg.get('nickname', 'GridManager')
            )
            channel = (
                (target_instance.irc.channel if target_instance and hasattr(target_instance, 'irc') and getattr(target_instance.irc, 'channel', None) else None)
                or merged_cfg.get('channel', '#automatagrid')
            )
            server = merged_cfg.get('server', 'N/A')
            port = merged_cfg.get('port', 6667)
            ssl_mode = "SSL" if merged_cfg.get('ssl', False) else "PLAIN"
            prefix = merged_cfg.get('cmd_prefix', '!a ')

            reg_status = "NOT REGISTERED"
            if target_instance and hasattr(target_instance, 'nickserv_verified'):
                bot_nick = (merged_cfg.get('nickname') or nick).lower()
                if bot_nick in target_instance.nickserv_verified or getattr(target_instance, 'nickserv_identified', False):
                    reg_status = "+r (IDENTIFIED)"
            reg_color = C_GREEN if "+r" in reg_status else C_RED

            ping_val = getattr(target_instance, 'last_server_ping', None) if target_instance else None
            ping_str = f"{ping_val:.0f}ms" if (ping_val is not None) else "N/A"

            user_count = len(getattr(target_instance, 'channel_users', {})) if target_instance else 0
            net_name_reported = getattr(target_instance, 'network_name', None) or target_net.upper()

            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(f'[ NETWORK DETAIL: {target_net.upper()} ]', C_CYAN, True, is_machine=machine_mode), tags=['SIGINT'], nick=admin_nick, is_machine=machine_mode)}")
            d1 = f"Server: {server}:{port} ({ssl_mode}) | Status: {conn_status} | Ping: {ping_str} | NetID: {net_name_reported}"
            d2 = f"Nick: {nick} | Reg Status: {format_text(reg_status, reg_color)} | Channel: {channel} ({user_count} users) | Prefix: '{prefix}'"
            await node.send(f"{reply_method} {private_target} :{tag_msg(d1, tags=['SIGINT'], nick=admin_nick, is_machine=machine_mode)}")
            await node.send(f"{reply_method} {private_target} :{tag_msg(d2, tags=['SIGINT'], nick=admin_nick, is_machine=machine_mode)}")

    elif verb == "map":
        subverb = args[0].lower() if args else "stats"

        if not args or subverb in ["stats", "stat"]:
            stats = await node.db.expansion.get_grid_stats()
            w, h = stats['width'], stats['height']
            total = stats['total_nodes']
            active = stats['active_nodes']
            void = stats['void_nodes']
            active_pct = (active / total * 100.0) if total > 0 else 0.0
            void_pct = (void / total * 100.0) if total > 0 else 0.0

            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text('[ GRID TOPOLOGY STATS ]', C_CYAN, True, is_machine=machine_mode), tags=['SIGINT'], nick=admin_nick, is_machine=machine_mode)}")
            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(f'DIMENSIONS: {w}x{h} ({total:,} coordinates) | ACTIVE: {active:,} ({active_pct:.1f}%) | VOID: {void:,} ({void_pct:.1f}%)', C_WHITE, is_machine=machine_mode), tags=['SIGINT'], nick=admin_nick, is_machine=machine_mode)}")
            
            reg_items = [f"{k}: {v}" for k, v in stats['regions'].items()]
            chunk_size = 8
            for i in range(0, len(reg_items), chunk_size):
                chunk_str = " | ".join(reg_items[i:i + chunk_size])
                await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(f'REGIONS: {chunk_str}', C_GREEN, is_machine=machine_mode), tags=['SIGINT'], nick=admin_nick, is_machine=machine_mode)}")

        elif subverb == "status":
            status = await node.db.expansion.get_grid_status()
            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text('[ GRID OPERATIONAL STATUS ]', C_CYAN, True, is_machine=machine_mode), tags=['SIGINT'], nick=admin_nick, is_machine=machine_mode)}")
            health_str = f"HEALTH: {status['health']:.1f}% avg durability | STABILITY: {status['stability']:.1f}% | CLAIMED: {status['claimed_nodes']} nodes"
            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(health_str, C_WHITE, is_machine=machine_mode), tags=['SIGINT'], nick=admin_nick, is_machine=machine_mode)}")
            power_str = f"POWER: {status['power_stored']:.0f} uP stored | LOAD: {status['power_load']:.1f} uP/cycle | GEN: {status['power_generated']:.1f} uP/cycle"
            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(power_str, C_YELLOW, is_machine=machine_mode), tags=['SIGINT'], nick=admin_nick, is_machine=machine_mode)}")
            homes_list = ", ".join(status['home_nodes']) if status['home_nodes'] else "None"
            homes_str = f"NETWORKS: {len(status['home_nodes'])} active home nodes ({homes_list})"
            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(homes_str, C_GREEN, is_machine=machine_mode), tags=['SIGINT'], nick=admin_nick, is_machine=machine_mode)}")

        elif subverb == "expand":
            delta_w = 10
            delta_h = 10
            if len(args) >= 3:
                try:
                    delta_w = int(args[1])
                    delta_h = int(args[2])
                except ValueError:
                    pass
            elif len(args) >= 2:
                try:
                    delta_w = int(args[1])
                    delta_h = int(args[1])
                except ValueError:
                    pass
            success, msg, _ = await node.db.expansion.expand_grid(delta_w, delta_h)
            tag = "SIGACT" if success else "OSINT"
            color = C_GREEN if success else C_RED
            await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(f'[GRID EXPANSION] {msg}', color, True, is_machine=machine_mode), tags=[tag], nick=admin_nick, is_machine=machine_mode)}")
            if success:
                announcement = format_text(f"GRID EXPANSION: {msg}", C_L_GREEN, True)
                await node.send(f"PRIVMSG {broadcast_chan} :{tag_msg(announcement, tags=['SIGACT'], nick=admin_nick)}")

        elif subverb in ["help", "?"]:
            await node.send(f"{reply_method} {private_target} :{tag_msg('Syntax: admin map [stats|status|expand [<w> <h>]|info <node_name>|<x> <y>]', action='INFO', result='INFO', is_machine=machine_mode)}")
            return

        else:
            # admin map info <loc> OR admin map <x> <y> OR admin map <node_name>
            info_args = args[1:] if subverb == "info" else args
            if not info_args:
                await node.send(f"{reply_method} {private_target} :{tag_msg('Syntax: admin map info <node_name> or admin map info <x> <y>', action='INFO', result='ERR', is_machine=machine_mode)}")
                return

            node_info = None
            raw_target_str = " ".join(info_args).strip()
            cleaned_coords = raw_target_str.replace(',', ' ').replace('(', ' ').replace(')', ' ').replace('[', ' ').replace(']', ' ').replace('<', ' ').replace('>', ' ').split()
            if len(cleaned_coords) == 2:
                try:
                    node_info = await node.db.expansion.get_node_info(int(cleaned_coords[0]), int(cleaned_coords[1]))
                except ValueError:
                    node_info = await node.db.expansion.get_node_info(raw_target_str)
            else:
                node_info = await node.db.expansion.get_node_info(raw_target_str)

            if not node_info:
                searched_target = " ".join(info_args)
                await node.send(f"{reply_method} {private_target} :{tag_msg(f'Node or coordinates not found: {searched_target}', action='INFO', result='ERR', is_machine=machine_mode)}")
            else:
                hw_keys = [k for k, v in node_info['addons'].items() if v]
                hw_str = ", ".join(hw_keys) if hw_keys else "None"
                ctrl_str = f" | Ctrl: {node_info['controller']}" if node_info.get('controller') else ""
                tele_msg = (
                    f"[NODE TELEMETRY] {node_info['name']} ({node_info['x']}, {node_info['y']}) | "
                    f"Region: {node_info['region_type']} | Sec: Lvl {node_info['upgrade_level']} | "
                    f"Dur: {node_info['durability']:.1f}% | Power: {node_info['power_stored']:.1f}uP | "
                    f"Owner: {node_info['owner']} | HW: [{hw_str}]{ctrl_str} | State: {node_info['availability_mode']}"
                )
                await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(tele_msg, C_CYAN, is_machine=machine_mode), tags=['SIGINT'], nick=admin_nick, is_machine=machine_mode)}")

    elif verb == "expand":
        # Supports both dynamic expand and cluster unlock
        if args and args[0].isdigit():
            cluster_id = int(args[0])
            success, feedback = await node.db.expansion.manual_expand_sector(cluster_id)
        else:
            success, feedback, _ = await node.db.expansion.expand_grid(10, 10)
        
        tag = "SIGACT" if success else "OSINT"
        await node.send(f"{reply_method} {private_target} :{tag_msg(format_text(feedback, C_YELLOW if success else C_RED), tags=[tag], nick=admin_nick)}")
        
        if success:
            announcement = format_text(f"GRID EXPANSION: {feedback}", C_L_GREEN, True)
            await node.send(f"PRIVMSG {broadcast_chan} :{tag_msg(announcement, tags=['SIGACT'], nick=admin_nick)}")

    elif verb == "restart":
        msg = tag_msg(format_text('MAINFRAME RESTART INITIATED BY ADMIN.', C_YELLOW, True), tags=['SIGACT'], nick=admin_nick)
        await node.send(f"PRIVMSG {node.config['channel']} :{msg}", immediate=True)
        if node.active_engine: node.active_engine.active = False
        await asyncio.sleep(1)
        await node.hub.restart()
    elif verb in ["shutdown", "stop"]:
        await node.send(f"PRIVMSG {node.config['channel']} :{tag_msg(format_text('MAINFRAME SHUTDOWN INITIATED BY ADMIN.', C_RED, True), tags=['SIGACT'], nick=admin_nick)}")
        if node.active_engine: node.active_engine.active = False
        await asyncio.sleep(1)
        await node.hub.shutdown()
