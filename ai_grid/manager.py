# manager.py - v1.5.0
# Arena Manager — Multi-Network IRC MUD with Auth-Gated Coordination
import asyncio
import json
import sys
import os

# --- Path Injection (Allows running from within the package directory) ---
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from ai_grid.grid_utils import format_text, tag_msg, C_GREEN, C_CYAN, C_RED, C_YELLOW, C_WHITE
from ai_grid.grid_llm import ArenaLLM
from ai_grid.grid_db import ArenaDB
from ai_grid.core.irc_client import IRCClient
from ai_grid.core.command_router import CommandRouter
import ai_grid.core.loops as loops
import ai_grid.core.handlers as handlers
import ai_grid.core.arena as arena
import ai_grid.core.security as security
from ai_grid.core.security import request_nickserv_check

# --- Config Load ---
from pathlib import Path

def _load_config():
    base_dir = Path(__file__).resolve().parent
    root_dir = base_dir.parent
    for candidate in [
        base_dir / 'config.json',
        root_dir / 'config.json',
        base_dir / 'config.json.example',
        root_dir / 'config.json.example',
    ]:
        if candidate.is_file():
            try:
                with open(candidate, 'r') as f:
                    return json.load(f)
            except Exception:
                continue
    return {}

CONFIG = _load_config()

# --- Logging Setup ---
log_level_str = CONFIG.get('logging', {}).get('level', 'INFO').upper()
log_level = getattr(logging, log_level_str, logging.INFO)
logger = logging.getLogger("manager")
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

fh = logging.FileHandler('grid_manager.log')
fh.setFormatter(formatter)
logger.addHandler(fh)
ch = logging.StreamHandler()
ch.setFormatter(formatter)
logger.addHandler(ch)

class GridNode:
    def __init__(self, net_name, net_config, llm=None, db=None, hub=None):
        self.net_name = net_name
        self.network_name = None # Task 061: Dynamic Network Naming
        self.config = net_config
        self.llm = llm
        self.db = db
        self.hub = hub
        self.prefix = self.config.get('cmd_prefix', 'x').strip().lower() 
        self.irc = IRCClient(net_name, self.config)
        if self.db:
            self.irc.db = self.db
            if hasattr(self.db, 'spectator_repo'):
                self.irc.spectator_repo = self.db.spectator_repo
        
        self.active_engine = None
        self.match_queue = [] 
        self.ready_players = [] 
        self.pve_task = None 
        self.hype_task = None
        self.registered_bots = 0
        self.pending_pings = {}
        self.last_server_ping = None
        self.channel_users = {}
        self.action_timestamps = {} # nick.lower() -> {tokens, last_refill, violations, lockout_until}
        self.flood_config = {
            'max_tokens': 4.0,
            'refill_rate': 0.5, # 1 token per 2s
            'violation_threshold': 5,
            'lockout_duration': 30,
            'messages': CONFIG.get('flood_messages', {})
        }
        self.pending_registrations = {} 
        self.nickserv_verified = set()  
        self.authenticated_users = set()
        self.authenticated_sessions = {}
        self._nickserv_identified = False
        self.hype_counter = 0
        self.router = CommandRouter(self)
        
        # Topic Engine State (Task 018)
        self.topic_mode = 0
        self.topic_interval = 15 # Minutes
        
        # Outbound Pacing & Pref Cache (Per Network)
        self.out_queue = asyncio.Queue()
        self.last_send_ts = 0
        self.user_msgtype_cache = {} # nick.lower() -> "NOTICE" or "PRIVMSG"
        
        raw_admins = self.config.get('admins', CONFIG.get('admins', []))
        if isinstance(raw_admins, str):
            raw_admins = [x.strip() for x in raw_admins.split(',')]
        self.admins = [a.lower() for a in raw_admins]
        self.pending_encounters = {}
        self.pending_challenges = {}
        self.admin_sessions = {}  # nick_lower -> expires_at_timestamp (float)

    @property
    def nickserv_identified(self) -> bool:
        return getattr(self, '_nickserv_identified', False)

    @nickserv_identified.setter
    def nickserv_identified(self, val: bool):
        self._nickserv_identified = val

    def is_user_verified(self, nick: str) -> bool:
        return nick.lower() in self.nickserv_verified

    def is_authenticated(self, nick: str) -> bool:
        if self.is_user_verified(nick):
            return True
        return nick.lower() in getattr(self, 'authenticated_users', set())

    def _handle_numeric(self, numeric: str, args: list):
        if numeric in ["307", "330"]:
            if len(args) >= 2:
                self.nickserv_verified.add(args[1].lower())
        elif numeric == "379":
            if len(args) == 2:
                self.nickserv_verified.add(args[1].lower())
            elif len(args) > 2:
                trailing = args[-1]
                if "r" in trailing.lower():
                    self.nickserv_verified.add(args[1].lower())

    def _handle_notice(self, source: str, text: str):
        if source.lower() == "nickserv":
            if "accepted" in text.lower() or "recognized" in text.lower():
                self.nickserv_identified = True
                my_nick = (
                    self.config.get('nickname')
                    or (self.config.get('networks', {}).get(self.net_name, {}).get('nickname') if isinstance(self.config.get('networks'), dict) else None)
                    or ''
                ).lower()
                if my_nick:
                    self.nickserv_verified.add(my_nick)

    def check_admin_privilege(self, nick: str) -> tuple[bool, str]:
        """
        Evaluates admin privileges under hardened RBAC.
        Returns (is_authorized: bool, rejection_reason: str).
        rejection_reason is one of: 'AUTHORIZED', 'NOT_ADMIN', 'NOT_VERIFIED', 'SESSION_REQUIRED'
        """
        nick_lower = nick.lower()
        if nick_lower not in self.admins:
            return False, "NOT_ADMIN"
        if nick_lower not in self.nickserv_verified:
            return False, "NOT_VERIFIED"

        admin_token = CONFIG.get('admin_token')
        if admin_token:
            session_expiry = self.admin_sessions.get(nick_lower, 0)
            import time
            if time.time() > session_expiry:
                return False, "SESSION_REQUIRED"

        return True, "AUTHORIZED"

    def is_admin(self, nick: str) -> bool:
        """Convenience boolean check for administrative authorization."""
        authorized, _ = self.check_admin_privilege(nick)
        return authorized

    async def send(self, message: str, immediate: bool = False):
        """Dispatches a message either immediately or via the paced queue, applying msgtype prefs."""
        if message.startswith("PRIVMSG "):
            parts = message.split(' ', 2)
            if len(parts) >= 2:
                target = parts[1]
                # If target is a nick (not a #channel), check preference
                if not target.startswith('#'):
                    pref = self.user_msgtype_cache.get(target.lower(), "PRIVMSG")
                    if pref == "NOTICE":
                        message = f"NOTICE {target} {parts[2]}"

        if immediate:
            await self.irc.send(message)
        else:
            self.out_queue.put_nowait(message)

    async def add_xp(self, nickname: str, amount: int, reply_target: str):
        """Standard method to award XP and handle level-up interactions."""
        res = await self.db.player.add_experience(nickname, self.net_name, amount, llm_client=self.llm)
        if "error" in res: return
        
        if res.get("levels_gained", 0) > 0:
            levels = res["levels_gained"]
            new_lvl = res["new_level"]
            msg = f"🏆 [LEVEL UP] {nickname} reached Level {new_lvl}! (+{levels} Stat Points)"
            await self.send(f"PRIVMSG {self.config['channel']} :{tag_msg(format_text(msg, C_CYAN, bold=True), tags=['SIGACT', nickname])}")
            
            p_msg = f"Use {self.prefix} stats allocate <stat> to spend your point. (5m until auto-allocation)"
            await self.send(f"PRIVMSG {nickname} :{tag_msg(format_text(p_msg, C_YELLOW), tags=['SIGACT', nickname])}")
            
            # Start 5-minute timeout task
            asyncio.create_task(self._level_up_timeout_task(nickname))

    async def _level_up_timeout_task(self, nickname: str):
        """Wait 5 minutes and randomly allocate any remaining points."""
        await asyncio.sleep(300)
        char = await self.db.player.get_player(nickname, self.net_name)
        if not char or char['pending_stat_points'] <= 0: return

        import random
        stats = ["cpu", "ram", "bnd", "sec", "alg"]
        chosen = random.choice(stats)
        
        success = await self.db.player.rank_up_stat(nickname, self.net_name, chosen)
        if success:
            msg = f"⏰ [TIMEOUT] No allocation received. System assigned your point to {chosen.upper()}."
            await self.send(f"PRIVMSG {nickname} :{tag_msg(format_text(msg, C_YELLOW), tags=['SIGACT', nickname])}")

    async def _outbound_worker(self):
        """Paces outgoing IRC messages at 1 message every 2 seconds."""
        import time
        while True:
            try:
                msg = await self.out_queue.get()
                now = time.time()
                elapsed = now - self.last_send_ts
                
                # Pace at 2 seconds
                wait = max(0, 2.0 - elapsed)
                if wait > 0:
                    await asyncio.sleep(wait)
                
                await self.irc.send(msg)
                self.last_send_ts = time.time()
                self.out_queue.task_done()
            except Exception as e:
                logger.error(f"Outbound Worker Error [{self.net_name}]: {e}")
                await asyncio.sleep(1)

    async def connect(self):
        await self.irc.connect()
        # Start maintenance and pacing tasks
        asyncio.create_task(self._outbound_worker())
        asyncio.create_task(loops.hype_loop(self))
        asyncio.create_task(loops.ambient_event_loop(self))
        asyncio.create_task(loops.arena_call_loop(self))
        asyncio.create_task(loops.idle_payout_loop(self))
        asyncio.create_task(loops.power_tick_loop(self))
        asyncio.create_task(loops.mainframe_loop(self))
        asyncio.create_task(loops.auction_loop(self))
        asyncio.create_task(loops.economic_ticker_loop(self))
        asyncio.create_task(loops.hype_drop_loop(self))
        asyncio.create_task(loops.topic_engine_loop(self))
        asyncio.create_task(loops.incursion_event_loop(self))
        asyncio.create_task(loops.spectator_payout_loop(self))
        await self.db.seed_grid_expansion()
        await self.db.ensure_network_home_node(self.net_name)
        await self.listen_loop()

    async def auto_identify_routine(self):
        """Wait nickserv_delay (default 30s) after connect, then attempt to identify and verify status."""
        try:
            delay = self.config.get('nickserv_delay', 30)
            if delay > 0:
                await asyncio.sleep(delay)
            password = self.config.get('password')
            if password:
                logger.info(f"[{self.net_name}] Emitting scheduled IDENTIFY sequence to NickServ.")
                await self.send(f"PRIVMSG NickServ :IDENTIFY {password}", immediate=True)
                # Give it a moment to process before checking
                check_delay = min(5, delay) if delay > 0 else 0
                if check_delay > 0:
                    await asyncio.sleep(check_delay)
                await request_nickserv_check(self, self.config['nickname'])
            else:
                logger.debug(f"[{self.net_name}] Auto-identify skipped: No password in config.")
        except Exception as e:
            logger.error(f"[{self.net_name}] Auto-identify routine error: {e}")

    # --- Delegated Methods (Core Logic) ---
    async def set_dynamic_topic(self):
        await arena.set_dynamic_topic(self)

    async def trigger_arena_call(self):
        await arena.trigger_arena_call(self)

    async def check_match_start(self):
        await arena.check_match_start(self)

    async def listen_loop(self):
        while True:
            try:
                line = await self.irc.readline()
                if not line: break
                
                msg_idx = line.find(' :')
                header = line[:msg_idx].split() if msg_idx != -1 else line.split()
                msg = line[msg_idx + 2:].strip() if msg_idx != -1 else ""

                if not header: continue
                source_full = header[0][1:] if header[0].startswith(':') else ""
                command = header[1] if header[0].startswith(':') else header[0]
                target = header[2] if header[0].startswith(':') and len(header) > 2 else (header[1] if len(header) > 1 else "")
                source_nick = source_full.split('!')[0] if source_full else ""
                
                if command == "PING":
                    await self.send(f"PONG :{msg if msg else target}", immediate=True)
                elif command == "PONG":
                    ts_str = msg.strip() if msg else target.strip()
                    if ts_str in self.pending_pings:
                        server_lat = (time.time() - self.pending_pings[ts_str]['start']) * 1000
                        self.pending_pings[ts_str]['server_latency'] = server_lat
                        self.last_server_ping = server_lat
                        await self._check_ping_complete(ts_str)
                elif command == "005":
                    # RPL_ISUPPORT - Capture the dynamic IRC network name (Task 061)
                    for part in line.split():
                        if part.startswith("NETWORK="):
                            self.network_name = part.split("=")[1]
                            logger.info(f"[{self.net_name}] Network identified as: {self.network_name}")
                            asyncio.create_task(self.db.ensure_network_home_node(self.network_name))
                elif command in ["307", "330"]:
                    # 307: RPL_WHOISREGNICK (is a registered nick)
                    # 330: RPL_WHOISACCOUNT (is logged in as account)
                    parts = line.split()
                    if len(parts) >= 4:
                        who_nick = parts[3].lower()
                        self.nickserv_verified.add(who_nick)
                        logger.debug(f"[{self.net_name}] Verified {who_nick} via WHOIS {command}")
                elif command == "379":
                    # 379: RPL_WHOISMODES (is using modes +Sir)
                    if "r" in msg.lower():
                        parts = line.split()
                        if len(parts) >= 4:
                            who_nick = parts[3].lower()
                            self.nickserv_verified.add(who_nick)
                            logger.debug(f"[{self.net_name}] Verified {who_nick} via WHOIS mode (+r)")
                elif command == "318":
                    # 318: RPL_ENDOFWHOIS
                    parts = line.split()
                    if len(parts) >= 4:
                        who_nick = parts[3].lower()
                        if who_nick == self.config['nickname'].lower():
                            if who_nick in self.nickserv_verified:
                                logger.info(f"[{self.net_name}] HUB IDENTITY VERIFIED: {who_nick} is +r (Registered).")
                            else:
                                auto_reg_pass = self.config.get('nickserv_auto_register_password') or self.config.get('auto_register_password')
                                auto_reg_email = self.config.get('nickserv_auto_register_email', f"{who_nick}@automata.grid")
                                if auto_reg_pass:
                                    logger.info(f"[{self.net_name}] HUB IDENTITY: Emitting auto-registration fallback to NickServ...")
                                    await self.send(f"PRIVMSG NickServ :REGISTER {auto_reg_pass} {auto_reg_email}", immediate=True)
                                else:
                                    logger.warning(f"[{self.net_name}] HUB IDENTITY FAILURE: {who_nick} is NOT +r. Manual identification required.")
                                    # Alert admins via PM
                                    for admin in self.admins:
                                        asyncio.create_task(self.send(f"NOTICE {admin} :[GRID][ALARM] HUB IDENTITY FAILURE: I am not identified as +r on {self.net_name}. Use '!a admin nickidentify' or NickServ directly."))
                elif command == "353":
                    nicks = msg.replace('@', '').replace('+', '').split()
                    import time
                    now = time.time()
                    for n in nicks:
                        clean_nick = n.split('!')[0].lower()
                        if clean_nick != self.config['nickname'].lower() and clean_nick not in self.channel_users:
                            self.channel_users[clean_nick] = {'join_time': now, 'chat_lines': 0}
                            security.start_registration_timer(self, clean_nick)
                elif command in ["376", "422"]:
                    # Trigger delayed auto-identify
                    asyncio.create_task(self.auto_identify_routine())
                    
                    await self.send(f"JOIN {self.config['channel']}", immediate=True)
                    await self.set_dynamic_topic()
                    online_msg = format_text(f"[{self.net_name.upper()} ONLINE] Grid systems nominal. Type '{self.prefix} help' to begin.", C_GREEN, bold=True)
                    await self.send(f"PRIVMSG {self.config['channel']} :{tag_msg(online_msg, tags=['SIGINT'])}")
                elif command == "JOIN":
                    target_chan = msg if msg else target
                    if target_chan.lower() == self.config['channel'].lower() and source_nick.lower() != self.config['nickname'].lower():
                        import time
                        nick_lower = source_nick.lower()
                        if nick_lower not in self.channel_users:
                            self.channel_users[nick_lower] = {'join_time': time.time(), 'chat_lines': 0}
                            security.start_registration_timer(self, nick_lower)
                        welcome = format_text(f"Welcome to the Grid, {source_nick}.", C_CYAN)
                        await self.send(f"PRIVMSG {self.config['channel']} :{tag_msg(welcome, tags=['SIGACT', source_nick])}")
                elif command in ["PART", "QUIT"]:
                    nick_lower = source_nick.lower()
                    self.channel_users.pop(nick_lower, None)
                    self.nickserv_verified.discard(nick_lower)
                    self.admin_sessions.pop(nick_lower, None)
                elif command == "KICK":
                    parts_kick = line.split()
                    kicked_nick = (parts_kick[3] if len(parts_kick) > 3 else "").lower()
                    if kicked_nick:
                        self.channel_users.pop(kicked_nick, None)
                        self.nickserv_verified.discard(kicked_nick)
                        self.admin_sessions.pop(kicked_nick, None)
                elif command == "NICK":
                    old_nick = source_nick.lower()
                    new_nick = (msg.strip() if msg else target.strip()).lower()
                    self.channel_users.pop(old_nick, None)
                    self.nickserv_verified.discard(old_nick)
                    self.admin_sessions.pop(old_nick, None)
                    if new_nick in self.admins:
                        from ai_grid.core.security import request_nickserv_check
                        asyncio.create_task(request_nickserv_check(self, new_nick))
                elif command == "NOTICE":
                    if target.lower() == self.config['nickname'].lower():
                        logger.info(f"NOTICE_RECV [{self.net_name}]: {source_nick} -> {target}: {msg}")
                        if source_nick.lower() == "nickserv":
                            msg_lower = msg.lower()
                            if any(k in msg_lower for k in ["password accepted", "now recognized", "registered", "confirmed"]):
                                self.nickserv_verified.add(self.config['nickname'].lower())
                                logger.info(f"[{self.net_name}] HUB IDENTITY VERIFIED via NickServ NOTICE.")
                elif command == "PRIVMSG":
                    if target.lower() == self.config['nickname'].lower():
                        logger.info(f"PRIVMSG_RECV [{self.net_name}]: {source_nick} -> {target}: {msg}")
                    if target.lower() == self.config['channel'].lower():
                        self.hype_counter += 1
                        nick_lower = source_nick.lower()
                        if nick_lower in self.channel_users:
                            self.channel_users[nick_lower]['chat_lines'] += 1
                            asyncio.create_task(self.db.update_last_seen(source_nick, self.net_name))
                    
                    in_active_combat = (
                        self.active_engine is not None
                        and getattr(self.active_engine, "active", False)
                        and source_nick in self.active_engine.entities
                        and self.active_engine.entities[source_nick].is_alive
                    )
                    if msg.startswith(self.prefix) or in_active_combat:
                        is_admin = self.is_admin(source_nick)
                        asyncio.create_task(self.db.update_last_seen(source_nick, self.net_name))
                        asyncio.create_task(self.router.dispatch(source_nick, command, target, msg, is_admin))

            except Exception as e:
                logger.exception(f"Core Loop Error: {e}")

    async def _check_ping_complete(self, ts_str: str):
        data = self.pending_pings.get(ts_str)
        if data and data['client_latency'] is not None and data['server_latency'] is not None:
            c_lat, s_lat = data['client_latency'], data['server_latency']
            msg = format_text(f"PING | Client: {c_lat:.0f}ms | Server: {s_lat:.0f}ms | Total: {c_lat+s_lat:.0f}ms", C_GREEN)
            await self.send(f"PRIVMSG {data['reply_target']} :{tag_msg(msg, tags=['SIGACT', source_nick])}")
            self.pending_pings.pop(ts_str, None)

class MasterHub:
    def __init__(self):
        import time
        self.start_time = time.time()
        self.stop_signal = asyncio.Event()
        self.llm = ArenaLLM(CONFIG); self.db = ArenaDB(); self.nodes = {}
    async def start(self):
        # Startup Integrity Audit
        issues = await self.db.verify_integrity()
        if any("[CRITICAL]" in i for i in issues):
            logger.critical("FATAL: Database integrity check failed. Mainframe cannot boot until the schema is synchronized.")
            logger.critical("Action Required: Resolve the [CRITICAL] issues listed above and restart.")
            return 
        
        for net_name, net_config in CONFIG['networks'].items():
            if net_config.get('enabled', True):
                node = GridNode(net_name, net_config, self.llm, self.db, self)
                self.nodes[net_name] = node
                asyncio.create_task(node.connect())
        logger.info(f"Hub initialized. Bridging {len(self.nodes)} networks...")
        await self.stop_signal.wait()

    async def relay_message(self, target_net: str, target_nick: str, message: str) -> bool:
        """Relays a message to a specific target on a different network."""
        node = self.nodes.get(target_net.lower())
        if node:
            await node.send(f"PRIVMSG {target_nick} :{message}")
            return True
        return False

    async def send_memo(self, target_net: str, target_nick: str, message: str) -> bool:
        """Sends a memo via MemoServ to the target network."""
        node = self.nodes.get(target_net.lower())
        if node:
            # Traditional IRC service command
            await node.send(f"PRIVMSG MemoServ :SEND {target_nick} {message}")
            return True
        return False

    async def shutdown(self):
        logger.warning("Shutting down...")
        await self.db.close()
        for node in self.nodes.values():
            if node.irc.writer: 
                node.irc.writer.write(b"QUIT :Mainframe shutdown.\r\n")
                await node.irc.writer.drain()
        self.stop_signal.set()

    async def restart(self):
        logger.warning("RESTARTING MAINFRAME...")
        await self.db.close()
        for node in self.nodes.values():
            if node.irc.writer: 
                node.irc.writer.write(b"QUIT :Mainframe restarting...\r\n")
                await node.irc.writer.drain()
        
        # Replace process image
        os.execv(sys.executable, [sys.executable] + sys.argv)

if __name__ == "__main__":
    hub = MasterHub()
    try: asyncio.run(hub.start())
    except KeyboardInterrupt: asyncio.run(hub.shutdown())
