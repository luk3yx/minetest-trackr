#!/usr/bin/env python3
#
# trackr 2.1.1
#
# © 2020 by luk3yx.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see https://www.gnu.org/licenses/.
#
# Usage:
#  • Make sure you are using the latest miniirc and miniirc_extras.
#    · sudo pip3 install --upgrade miniirc miniirc_extras
#  • Create a trackr.ini file similar to the below one.
#  • Run the script.
#
# Config file (trackr.ini) format:
#  [trackr]
#  ip = irc.edgy1.net
#  ssl_port = 6697
#  channels = #edgy1, #ls-servers
#  nick = bot-nickname
#  prefix = ,
#  secret = Random string to use when generating passwords
#  admins = Edgy1, luk3yx
#
#  # Optional and case-sensitive (defaults to +v list)
#  # server_list = MinetestServer1, MinetestServer2
#

import hashlib, miniirc, miniirc_extras, os, random, sys, time
assert miniirc.ver >= (1,4,0), 'Update miniirc.'
assert miniirc_extras.ver >= (0,2,5), 'Update miniirc_extras.'

from miniirc_extras import AbstractIRC, Hostmask
from miniirc_extras.features.chans import Channel, ModeList, ChannelTracker
from miniirc_extras.features.users import AbstractChannel, User, UserTracker

from typing import Dict, FrozenSet, List, Optional, Set, Tuple, Union

__version__ = '2.1.1'

# Errors
class BotError(Exception):
    pass

def err(msg: str, *args, **kwargs) -> None:
    if args or kwargs:
        msg = msg.format(*args, **kwargs)
    raise BotError(msg)

# Get a plural
def plural(n: int) -> str:
    return '' if n == 1 else 's'

# Get a valid Lua representation of a string
def _escape_string(x: bytes):
    yield '"'
    for char in x:
        if char == 0x22: # "
            yield r'\"'
        elif char == 0x5c:
            yield r'\\'
        elif 0x7f > char > 0x1f:
            yield chr(char)
        else:
            yield '\\' + str(char).zfill(3)

    yield '"'

def lua_repr(s: str) -> str:
    return ''.join(_escape_string(s.encode('utf-8')))

# A player action error
class ModerationError(Exception):
    pass

# A player class
class Player(str):
    total_warnings: int = 2
    warnings: int
    __slots__ = ('warnings', '_server')

    # Kick the player
    def kick(self, sender: str, reason: str) -> None:
        assert self._server
        self._server.msg(f'cmd kick {self} By {sender}: {reason}')

    # Mute the player
    def mute(self) -> None:
        assert self._server
        self._server.msg(f'cmd revoke {self} shout')

    # Unmute the player
    def unmute(self) -> None:
        assert self._server
        self._server.msg(f'cmd grant {self} shout')

    # Tempmute the player (mega hax)
    def tempmute(self, duration: Union[str, int, float]) -> None:
        assert self._server

        duration, multiplier = (duration or 5), 60
        if isinstance(duration, str):
            if duration.endswith('m'):
                duration = duration[:-1]
            elif duration.endswith('s'):
                duration, multiplier = duration[:-1], 1
            elif duration.endswith('h'):
                duration, multiplier = duration[:-1], 3600

        try:
            duration = float(duration)
            assert duration > 0
        except:
            raise ModerationError('Invalid duration!')
        duration = int(duration * multiplier)

        if duration > 7200:
            raise ModerationError('You cannot tempmute someone for over 2 '
                'hours!')

        # Create a hacky lua script
        script = (f'cmd /lua local m={lua_repr(self)};'
            'core.registered_chatcommands.revoke.func("trackr",m.." shout")'
            'local function r() '
                'if m then '
                    'core.registered_chatcommands.grant.func("trackr",'
                        'm.." shout") '
                'end '
            'end '
           f'core.after({duration},r);'
            'core.register_on_shutdown(r)')
        self._server.msg(script)

    # Warn the player
    def warn(self, sender: str, msg: str) -> str:
        assert self._server

        if self.warnings > 0:
            msg2 = '{} warning{} left until you get temp-muted.'.format(
                self.warnings, plural(self.warnings))
            self.warnings -= 1
        else:
            self.tempmute(30)
            msg2 = 'been temporarily muted for 30 minutes.'
            self.warnings = self.total_warnings

        msg = f'{msg}\n -- {sender}\n\nYou have {msg2}'

        self._server.msg(f'cmd /lua core.show_formspec({lua_repr(self)},'
            '"trackr:warning", "size[8,5;]image[0,0;1,1;bucket_lava.png]'
            'image[7,0;1,1;bucket_lava.png]'
            'label[1.25,0.25;WARNING - Please read carefully.]'
           f'label[0,1.25;" .. minetest.formspec_escape({lua_repr(msg)}) .. "]'
            'button_exit[0,4.5;8,0.5;quit;Continue]'
            '" .. (default.gui_bg or ""))')

        return self + ' has ' + msg2.replace('you', 'they')

    def __repr__(self) -> str:
        return f'{type(self).__name__}({super().__repr__()}, {self.warnings})'

    def __new__(cls, name: str, warnings: Optional[int] = None, *,
            server: Optional[User] = None):
        return super().__new__(cls, name) # type: ignore

    def __init__(self, name: str, warnings: Optional[int] = None, *,
            server: Optional[User] = None) -> None:
        assert name

        self.warnings: int
        if warnings is None:
            self.warnings = self.total_warnings
        else:
            self.warnings = warnings

        self._server: Optional[User] = server

# A player list
class PlayerList(dict):
    __slots__ = ('server',)

    # An easy way to create new players
    def Player_(self, name: str, warnings: Optional[int] = None) \
            -> Optional[Player]:
        if not name:
            return None

        if name in self:
            player = self[name]
        else:
            player = Player(name, warnings, server = self.server)
            self[name] = player
        return player

    def get(self, key: str, *args):
        return super().get(str(key).lower(), *args)

    def __getitem__(self, key: str):
        return super().__getitem__(str(key).lower())

    def __setitem__(self, key: str, value: Player):
        return super().__setitem__(str(key).lower(), value)

    def __delitem__(self, key: str):
        return super().__delitem__(str(key).lower())

    def __contains__(self, key) -> bool:
        return super().__contains__(str(key).lower())

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.server: Optional[User] = None

    Player = Player_
    del Player_

# The bot
class Trackr:
    cooldown: int = 15
    last_list: Union[int, float] = 0
    irc: AbstractIRC

    # Alias for self.irc.debug
    @property
    def debug(self):
        return self.irc.debug

    # Hacks to keep mypy happy
    @property
    def users(self) -> UserTracker:
        return self.irc.users # type: ignore

    @property
    def chans(self) -> ChannelTracker:
        return self.irc.chans # type: ignore

    # __init__
    def __init__(self, rawconfig: Dict[str, Dict[str, str]],
            debug: bool = False) -> None:
        if 'trackr' not in rawconfig:
            err('Invalid or non-existent config file!')
        config: Dict[str, str] = rawconfig['trackr']
        del rawconfig
        self.config: Dict[str, str] = config

        self._conf_assert('ip', ('ssl_port', int), 'nick', 'channels',
            'admins')

        self._secret: bytes = config.get('secret', 'Oops').encode('utf-8')
        self.admins: FrozenSet[str] = frozenset(map(
            lambda n : n.strip().lower(), config['admins'].split(',')))
        self.prefix = config.get('prefix', config['nick'] + ': ')

        self.server_list: Optional[FrozenSet[str]] = None
        serverlist = config.get('server_list', '').strip()
        if serverlist:
            self.server_list = frozenset(map(str.strip, serverlist.split(',')))

        kwargs = {}
        for i in 'ident', 'realname', 'ns_identity', 'connect_modes', \
                'quit_message':
            if i in config:
                kwargs[i] = config[i]

        # Create the IRC object
        self.irc = miniirc.IRC(config['ip'], # type: ignore
            int(config['ssl_port']), config['nick'],
            set(map(str.strip, config['channels'].split(','))),
            debug=debug, auto_connect=False, ssl=True, **kwargs) # type: ignore

        # Load irc.chans and irc.users
        self.irc.require('chans')
        self.irc.require('users')

        # Add handlers
        self.irc.Handler('PRIVMSG', colon=False)(self._handle_privmsg)
        self.irc.Handler('JOIN', colon=False)(self._handle_join)

        # Connect
        self.irc.connect()

    # Function copied from lurklite
    def _conf_assert(self, *keys: Union[str, Tuple[str, type]]) -> None:
        for key in keys:
            req: Optional[type] = None
            if isinstance(key, tuple):
                key, req = key

            if key not in self.config:
                err('Required config value {} missing!', repr(key))
            elif req:
                try:
                    req(self.config[key])
                except:
                    err('Config value {} contains an invalid {}.', repr(key),
                        req.__name__)

    # Check if a hostmask is a server
    def is_server(self, channel: Union[str, AbstractChannel],
            hostmask: Union[Hostmask, User]) -> bool:
        if not isinstance(channel, Channel):
            if isinstance(channel, AbstractChannel):
                channel = channel.name

            try:
                channel = self.chans[channel]
            except:
                # Oops, use a backup method
                if not isinstance(hostmask, User):
                    hostmask = self.users[hostmask]
                return 'players' in hostmask.keys()

        modes = self.server_list or channel.modes.getset('v')

        if isinstance(hostmask, User):
            hostmask = hostmask.hostmask
        elif not isinstance(hostmask, Hostmask):
            raise TypeError('is_server() expects User or hostmask.')

        return hostmask[0] in modes or hostmask[0].lower() in modes

    # Check if a user is an admin
    def is_admin(self, channel: Union[str, AbstractChannel],
            hostmask: Union[Hostmask, User, str]) -> bool:
        if not isinstance(channel, Channel):
            if isinstance(channel, AbstractChannel):
                channel = channel.name
            channel = self.chans[channel]

        nick: str
        if isinstance(hostmask, User):
            nick = hostmask.nick
        elif isinstance(hostmask, Hostmask):
            nick = hostmask[0]
        else:
            nick = hostmask

        lnick: str = nick.lower()

        for mode in 'oaq':
            users = channel.modes.getset(mode)
            if nick in users or lnick in users:
                return True

        return False

    # Derive a password from a hostmask
    def get_password(self, hostmask: Hostmask) -> str:
        host = '/'.join(hostmask[2].split('/', 3)[:3])
        host = '.'.join(host.split('.', 2)[:2])
        pw   = f'{hostmask[0]}@{host}'.encode('utf-8')
        pw  += b', secret: ' + self._secret

        # Hash it
        return hashlib.sha512(pw).hexdigest()

    # Get an iterable list with servers
    def servers(self, channel: Union[AbstractChannel, str]):
        if isinstance(channel, str):
            channel = self.chans[channel]

        for user in channel.users:
            if self.is_server(channel, user):
                yield user

    # Get a list of both servers and players
    def items(self, channel: Union[AbstractChannel, str]):
        for server in self.servers(channel):
            yield server, server.get('players')

    # The players command
    def _players_cmd(self, channel: str, nick: str) -> None:
        irc: AbstractIRC = self.irc

        t = time.time()
        if t <= self.last_list + self.cooldown:
            irc.msg(channel, nick + ': You can only run \2.players\2 once',
                f'every \2{self.cooldown} seconds\2.')
            return
        self.last_list = t

        # Get the player list
        total: int    = 0
        inactive: int = 0
        tplayers: int = 0
        slist: List[Tuple[User, PlayerList]] = list(self.items(channel))
        slist.sort(key = lambda s : s[0].nick.lower())

        # Iterate over every server in the channel
        for server, players in slist:
            if not players:
                inactive += 1
                continue
            total    += 1
            tplayers += len(players)
            players2: List[Player] = list(players.values())
            players2.sort()
            irc.msg(channel, 'Players on \2{}\2: {}'.format(server.nick,
                ', '.join(players2)))
            self.last_list += 0.5
            time.sleep(0.5)

        # Display the summary
        irc.msg(channel, ('Total: \2{} player{}\2 across \2{} active '
            'server{}\2 (and {} inactive server{}).').format(tplayers,
            plural(tplayers), total, plural(total), inactive,
            plural(inactive)))

    # The login command
    def _login_cmd(self, nick: str, param: str) -> None:
        irc: AbstractIRC = self.irc

        params = param.split(' ', 1)
        del param

        if len(params) != 2:
            irc.msg(nick, 'Invalid syntax! Syntax: login <server> <password>')
            return

        sid, pw = params
        del params

        if sid not in self.users:
            irc.msg(nick, f"What's a {repr(sid)}?")
            return
        server: User = self.users[sid]
        if 'players' not in server.keys():
            irc.msg(nick, f'{repr(sid)} is not a server!')

        server['logged_in'] = 0
        server.msg('login trackr', pw)

        irc.msg(nick, 'I will attempt to log in.')

    # Handle the moderation commands
    def _moderate(self, channel: str, hostmask: Hostmask, cmd: str,
            param: str) -> str:
        chan: AbstractChannel = self.chans[channel]
        if not isinstance(chan, Channel):
            return 'Error: This should never happen.'

        # Make sure the user is a channel operator
        is_op = False
        user: User = self.users[hostmask]
        if not self.is_admin(channel, hostmask):
            return 'Permission denied!'

        # Make sure the player exists
        n = param.split(' ', 1)
        victim: str = n[0].lower()
        server: Optional[User] = None
        if '@' in victim:
            victim, sid = victim.split('@', 1)
            try:
                server = self.users[sid]
                assert server in chan
            except:
                return f'The server {repr(sid)} does not exist!'

            if victim not in server.get('players', ()):
                return f'The player {repr(victim)} is not in {server.nick}.'
        else:
            for s, p in self.items(chan):
                if p and victim in p:
                    if server is not None:
                        return 'Error: That player is in multiple servers!'
                    server = s

        if not server:
            return 'Unknown player!'
        elif not server.get('logged_in'):
            return f'I am not logged into {server.nick}!'

        player: Player = server['players'][victim] # type: ignore
        res: Optional[str] = None

        try:
            if cmd in ('mute', 'unmute'):
                res = getattr(player, cmd)()
            elif cmd in ('warn', 'kick'):
                res = getattr(player, cmd)(hostmask[0], n[-1])
            elif cmd == 'tempmute':
                player.tempmute(n[-1])
            else:
                return 'Internal error!'
        except ModerationError as e:
            return 'Error: ' + str(e)

        if not res:
            res = f'Attempted to {cmd} {player}.'
        return res

    # Handle PRIVMSGs
    def _handle_privmsg(self, irc: AbstractIRC, hostmask: Hostmask,
            args: List[str]) -> None:
        nick:    str = hostmask[0]
        channel: str = args[0]
        msg:     str = args[-1]

        # Check for MT players
        if msg.startswith('<'):
            n: List[str] = msg.split(' ', 1)
            if len(n) > 1 and n[0].endswith('>') and (n[0][1].isalnum() or
                    n[0][1] == '\x03'):
                nick = f'{n[0][1:-1]}@{nick}'
                msg  = n[1].strip()
            del n

        if msg.startswith('.players'):
            msg = self.prefix + msg[1:]

        # Check for commands
        if msg.startswith(self.prefix):
            cmd_args = msg[len(self.prefix):].split(' ', 1)
            cmd = cmd_args[0].lower()

            if irc.nick.lower() == args[0].lower():
                if cmd != 'login':
                    irc.msg(hostmask[0],
                        'You may not execute commands in PMs.')
                elif hostmask[-1].split('/')[-1].lower() in self.admins:
                    self._login_cmd(hostmask[0], cmd_args[-1])
                else:
                    irc.msg(hostmask[0], 'Permission denied!')
                return

            if cmd == 'players':
                return self._players_cmd(channel, nick)
            elif cmd in ('kick', 'mute', 'unmute', 'tempmute', 'warn'):
                irc.msg(channel, nick + ': ' + self._moderate(channel,
                    hostmask, cmd, cmd_args[1] if len(cmd_args) > 1 else ''))
                return
            elif cmd == 'badservers':
                bad = []
                for s_ in self.servers(channel):
                    if not s_.get('logged_in'):
                        bad.append(s_.nick)
                if bad:
                    bad.sort(key = lambda n : n.lower())
                else:
                    bad.append('(none)')
                irc.msg(channel,
                    f'{nick}: Servers I am not logged into: {", ".join(bad)}')
                return
            elif cmd == 'die':
                if hostmask[-1].split('/')[-1].lower() in self.admins:
                    msg = f'{nick} ordered me to die- wait, why did I listen?'
                    irc.disconnect(msg)
                    print(msg)
                    os._exit(0)
                else:
                    msg = random.choice(("But I don't want to die.", 'No.',
                        'Resistance is futile.', 'Sorry, what was that?',
                        'You know I could ignore you all day.',
                        "I'm going to pretend you didn't say that.",
                        'die: Singular form of dice.'))
                    irc.msg(channel, f'{nick}: {msg}')

                return

        if nick != hostmask[0] or not self.is_server(channel, hostmask):
            return

        # Store Player objects inside the User object so they are moved with
        #   nick changes etc.
        server: User = self.users[hostmask]
        players: PlayerList
        if 'players' in server.keys():
            players = server['players'] # type: ignore
            assert isinstance(players, PlayerList)
        else:
            server['players'] = players = PlayerList()
            players.server = server

        if msg.startswith('*** '):
            a: List[str] = msg.split(' ', 3)
            if len(a) <= 2:
                return
            if a[2] == 'joined':
                players.Player(a[1])
            elif a[2] == 'left' and a[1] in players:
                del players[a[1]]
            del a
        elif msg.startswith('Connected players: '):
            new_players: List[str] = args[-1][19:].replace(' ', '').split(',')
            for player in new_players:
                players.Player(player)

            for player, pobj in tuple(players.items()):
                if str(pobj) not in new_players:
                    print('Deleting player', repr(pobj))
                    del players[player]

            # Log in
            if server.get('logged_in') is None:
                self.debug('[trackr] Logging into', hostmask[0])
                server.msg('login trackr', self.get_password(hostmask))
        elif msg.startswith('You are now logged in as'):
            self.debug('[trackr] Logged into', hostmask[0])
            logged_in = server.get('logged_in')
            if logged_in == 0 and logged_in is not False:
                server.msg('cmd setpassword trackr',
                    self.get_password(hostmask))
                server.msg('cmd /lua irc.say("[trackr] Logged in!")')
            self.debug('Logged into', hostmask[0])
            server['logged_in'] = True
        elif msg.startswith('Incorrect password'):
            print('[trackr] WARNING: Incorrect password for server',
                hostmask[0], file = sys.stderr)
            server['logged_in'] = False

    # Handle JOINs
    def _handle_join(self, irc: AbstractIRC, hostmask: Hostmask,
            args: List[str]) -> None:
        time.sleep(1)
        if hostmask[0].lower() == irc.nick.lower():
            for server in self.servers(args[0]):
                server['players'] = players = PlayerList()
                players.server = server
                server.msg('players', '-',
                    'If you are a human, report this to luk3yx.')
            return

        if not self.is_server(args[0], hostmask):
            return

        user = self.users[hostmask]
        players = PlayerList()
        players.server = user
        user['players'] = players
        irc.msg(hostmask[0],
            'players - If you are a human, report this to luk3yx.')

# The main script
def main() -> Trackr:
    import argparse, configparser
    parser = argparse.ArgumentParser()
    parser.add_argument('config_file',
        help='The config file to use with lurklite.')
    parser.add_argument('--verbose', '--debug', action='store_true',
        help='Enable verbose/debugging mode.')
    parser.add_argument('-v', '--version', action='version',
        version=f'trackr v{__version__} (powered by {miniirc.version})')
    args = parser.parse_args()

    # Load the config file
    config = configparser.ConfigParser()
    config.read(args.config_file)

    # Create the bot
    try:
        return Trackr(config, debug=args.verbose) # type: ignore
    except BotError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        raise SystemExit(1)

# Call main()
if __name__ == '__main__':
    main()
