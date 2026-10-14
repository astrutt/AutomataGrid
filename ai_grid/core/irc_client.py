# irc_client.py - v1.5.0
import asyncio
import ssl
import logging
from ai_grid.core.validation import sanitize_irc_outbound

logger = logging.getLogger("manager")

class IRCClient:
    def __init__(self, net_name, config=None):
        self.net_name = net_name
        if isinstance(config, dict):
            self.config = config
            self.nickname = config.get('nickname', 'GridManager')
            self.channel = config.get('channel', '#automatagrid')
        else:
            self.config = {
                'server': str(net_name),
                'port': config if isinstance(config, int) else 6667,
                'nickname': 'GridManager',
                'channel': '#automatagrid',
                'ssl': False,
            }
            self.nickname = self.config['nickname']
            self.channel = self.config['channel']
        self.spectator_repo = self.config.get('spectator_repo') if isinstance(self.config, dict) else None
        self.db = self.config.get('db') if isinstance(self.config, dict) else None
        self.reader = None
        self.writer = None

    def sanitize_line(self, line: str) -> str:
        return sanitize_irc_outbound(line)

    async def connect(self):
        logger.info(f"Connecting to {self.net_name} ({self.config['server']}:{self.config['port']})...")
        ssl_ctx = ssl.create_default_context() if self.config['ssl'] else None
        self.reader, self.writer = await asyncio.open_connection(
            self.config['server'], self.config['port'], ssl=ssl_ctx
        )
        
        await self.send(f"NICK {self.nickname}")
        await self.send(f"USER {self.nickname} 0 * :AutomataGrid Master Node")
        # JOIN is usually handled after receiving the 001 welcome message in the listen loop
        # But we can put it here if we want to be simple, though it might fail if we aren't registered yet.

    async def send(self, message: str):
        if self.writer:
            sanitized = sanitize_irc_outbound(message)
            logger.debug(f"[{self.net_name}] > {sanitized}")
            self.writer.write(f"{sanitized}\r\n".encode('utf-8'))
            await self.writer.drain()
            await asyncio.sleep(0.3)

    async def privmsg(self, target: str, message: str):
        await self.send(f"PRIVMSG {target} :{message}")

    async def notice(self, target: str, message: str):
        await self.send(f"NOTICE {target} :{message}")

    async def join(self, channel: str):
        await self.send(f"JOIN {channel}")

    async def part(self, channel: str):
        await self.send(f"PART {channel}")

    def get_spectator_repo(self):
        if getattr(self, 'spectator_repo', None) is not None:
            return self.spectator_repo
        if getattr(self, 'db', None) is not None:
            if hasattr(self.db, 'spectator_repo'):
                self.spectator_repo = self.db.spectator_repo
                return self.spectator_repo
            from ai_grid.database.spectator_repo import SpectatorRepository
            self.spectator_repo = SpectatorRepository(self.db.async_session)
            return self.spectator_repo
        try:
            from ai_grid.grid_db import ArenaDB
            from ai_grid.database.spectator_repo import SpectatorRepository
            db = ArenaDB()
            self.spectator_repo = SpectatorRepository(db.async_session)
            return self.spectator_repo
        except Exception:
            return None

    def track_spectator_activity(self, line: str):
        if not line:
            return
        try:
            prefix = ""
            rest = line
            if rest.startswith(":"):
                prefix, _, rest = rest[1:].partition(" ")
            trailing = ""
            if " :" in rest:
                rest, _, trailing = rest.partition(" :")
            tokens = rest.split()
            if not tokens:
                return
            command = tokens[0].upper()
            params = tokens[1:]
            if trailing:
                params.append(trailing)

            source_nick = prefix.split("!")[0] if prefix else ""
            if not source_nick:
                return

            # Ignore bot nick
            if source_nick.lower() == (self.nickname or "").lower():
                return

            # Ignore administrator nicks (config['admins'])
            raw_admins = []
            if isinstance(self.config, dict):
                raw_admins = self.config.get('admins', [])
            if isinstance(raw_admins, str):
                admin_nicks = [a.strip().lower() for a in raw_admins.split(',') if a.strip()]
            elif isinstance(raw_admins, (list, set, tuple)):
                admin_nicks = [str(a).strip().lower() for a in raw_admins if str(a).strip()]
            else:
                admin_nicks = []

            if source_nick.lower() in admin_nicks:
                return

            game_channel = (self.channel or "").lower()

            if command == "PRIVMSG" and params:
                target = params[0].lstrip(":").lower()
                if target == game_channel:
                    asyncio.create_task(self._track_privmsg_spectator(source_nick))
            elif command == "JOIN" and params:
                target = params[0].lstrip(":").lower()
                if target == game_channel:
                    asyncio.create_task(self._track_join_spectator(source_nick))
        except Exception as e:
            logger.error(f"Error tracking spectator in IRCClient: {e}")

    async def _track_privmsg_spectator(self, nick: str):
        try:
            repo = self.get_spectator_repo()
            if repo:
                res1 = repo.upsert_spectator(nick, self.net_name)
                if asyncio.iscoroutine(res1) or hasattr(res1, '__await__'):
                    await res1
                res2 = repo.record_message(nick, self.net_name)
                if asyncio.iscoroutine(res2) or hasattr(res2, '__await__'):
                    await res2
        except Exception as e:
            logger.error(f"Spectator PRIVMSG tracking error for {nick}: {e}")

    async def _track_join_spectator(self, nick: str):
        try:
            repo = self.get_spectator_repo()
            if repo:
                res = repo.upsert_spectator(nick, self.net_name)
                if asyncio.iscoroutine(res) or hasattr(res, '__await__'):
                    await res
        except Exception as e:
            logger.error(f"Spectator JOIN tracking error for {nick}: {e}")

    def is_connected(self):
        return self.reader is not None and self.writer is not None

    async def readline(self):
        if not self.reader:
            return None
        try:
            line = await self.reader.readline()
            if not line:
                return None
            decoded = line.decode('utf-8', errors='ignore').strip()
            if decoded:
                self.track_spectator_activity(decoded)
            return decoded
        except Exception as e:
            logger.error(f"Error reading from {self.net_name}: {e}")
            return None

    async def close(self):
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
        self.reader = None
        self.writer = None
