#
# Copyright (C) 2007, 2008, 2009 Loic Dachary <loic@dachary.org>
#
# This software's license gives you freedom; you can copy, convey,
# propagate, redistribute and/or modify this program under the terms of
# the GNU Affero General Public License (AGPL) as published by the Free
# Software Foundation (FSF), either version 3 of the License, or (at your
# option) any later version of the AGPL published by the FSF.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU Affero
# General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program in a file in the toplevel directory called
# "AGPLv3".  If not, see <http://www.gnu.org/licenses/>.
#
# Authors:
#  Loic Dachary <loic@dachary.org>
#
import sys
sys.path.insert(0, "..")

from os import popen
from string import rstrip
from random import randint

from twisted.internet import reactor

from pokerengine.pokertournament import *
from pokernetwork.user import checkName
from pokerpackets.packets import *
from pokerpackets.networkpackets import *
from pokerpackets.clientpackets import *
from pokernetwork import log as network_log
log = network_log.get_child('pokerbotlogic')

LEVEL2ITERATIONS = {
    0: 10,
    1: 1000,
    2: 10000,
    3: 50000,
    4: 100000,
    5: 200000
    }

STATE_LOGIN = 0
STATE_RECONNECTING = 1
STATE_SEARCHING = 2
STATE_RUNNING = 3
STATE_BATCH = 4


#
# If name generation is slow use /dev/urandom instead of
# /dev/random. apg will switch to /dev/urandom if it cannot
# open it for reading. chmod go-rw /dev/random will do this
# trick if not running the bots as root.
#

game_id2tourney_id = {}

class Pool:

    def __init__(self, command):
        self.command = command
        self.pool = []
        self.max_tries = 5

    lines2pool = None
    
    def getLine(self):
        tries = 0
        while len(self.pool) == 0 and tries < self.max_tries:
            fd = popen(self.command)
            self.pool = self.lines2pool(fd.readlines())
            fd.close()
            tries += 1
        if tries >= self.max_tries:
            raise UserWarning, "pokerbotlogic:Pool too many failures running " + self.command
        return self.pool.pop()
    
class StringGenerator(Pool):

    def __init__(self, name_prefix):
        self.name_prefix = name_prefix
        Pool.__init__(self, "/usr/bin/apg -m 5 -x 10 -M ncl -q -n 500")

    def getName(self):
        return self.name_prefix + self.getLine()

    def lines2pool(self, lines):
        return filter(lambda string: checkName(string)[0], map(lambda string: string[:-1], lines))
    getPassword = Pool.getLine

class NoteGenerator(Pool):

    def lines2pool(self, lines):
        return map(lambda string: rstrip(string).split('\t'), lines)

    getNote = Pool.getLine
    
class PokerBot:

    note_generator = NoteGenerator("exit 1")
    log = log.get_child('PokerBot')
    
    def __init__(self, factory):
        self.factory = factory
        self.state = STATE_LOGIN
        self.batch_end_action = None
        self.seat = -1

    def lookForGame(self, protocol):
            join_info = self.factory.join_info
            if join_info['tournament']:
                protocol.sendPacket(PacketPokerTourneySelect(string = join_info["name"]))
            else:
                protocol.sendPacket(PacketPokerTableSelect(string = join_info["name"]))
            self.state = STATE_SEARCHING
            self.factory.can_disconnect = True

    def bootstrap(self, protocol):
        user = protocol.user
        protocol.sendPacket(PacketPokerSetRole(roles = PacketPokerRoles.PLAY))
        protocol.sendPacket(PacketLogin(name = user.name, password = user.password))
        protocol.sendPacket(PacketPokerTableSelect(string = "my"))
    
    def _handleLogin(self, protocol, packet):
        if packet.type == PACKET_BOOTSTRAP:
            reactor.callLater(self.factory.serial * 0.1, self.bootstrap, protocol)
        elif packet.type == PACKET_AUTH_OK:
            log.inform("Bot credentials are ok for user %s", protocol.user.name)
            self.state = STATE_RECONNECTING
        elif packet.type == PACKET_AUTH_REFUSED:
            log.error("Bot credentials are wrong for user %s", protocol.user.name)
            protocol.transport.loseConnection()

    def _handleSearch(self, protocol, packet):
        if packet.type == PACKET_POKER_TABLE_LIST:
            if self.state == STATE_SEARCHING:
                table_info = self.factory.join_info
                for table in packet.packets:
                    if table_info.get("skin", table.skin) == table.skin:
                        protocol.sendPacket(PacketPokerTableJoin(game_id = table.id, serial = protocol.getSerial()))
                        if not self.factory.watch:
                            protocol.sendPacket(PacketPokerSeat(game_id = table.id, serial = protocol.getSerial()))
                            protocol.sendPacket(PacketPokerBuyIn(game_id = table.id, serial = protocol.getSerial()))
                            protocol.sendPacket(PacketPokerAutoBlindAnte(game_id = table.id, serial = protocol.getSerial()))
                            protocol.sendPacket(PacketPokerSit(game_id = table.id, serial = protocol.getSerial()))
                        self.state = STATE_RUNNING
                        break
                # if we didn't break, we didn't find a table
                else:
                    self.log.warn("Unable to find table %s (%s)", table_info["name"], table_info.get("skin",""))
                    protocol.transport.loseConnection()

            elif self.state == STATE_RECONNECTING:
                tables = packet.packets
                if len(tables) == 0:
                    self.lookForGame(protocol)
                elif len(tables) == 1:
                    table = tables[0]
                    protocol.sendPacket(PacketPokerTableJoin(game_id = table.id, serial = protocol.getSerial()))
                    protocol.sendPacket(PacketPokerSit(game_id = table.id, serial = protocol.getSerial()))
                    self.state = STATE_RUNNING
                else:
                    self.log.inform("Unexpected number of tables %d", len(tables))
                    protocol.transport.loseConnection()

            else:
                self.log.warn("Unexpected state %d", self.state)
                protocol.transport.loseConnection()

        elif packet.type == PACKET_POKER_TOURNEY_LIST:
            tourney_info = self.factory.join_info
            
            for tourney in packet.packets:
                if tourney.state == TOURNAMENT_STATE_REGISTERING and tourney_info.get("skin", tourney.skin) == tourney.skin:
                    protocol.sendPacket(PacketPokerTourneyRegister(serial = protocol.getSerial(), tourney_serial = tourney.serial))
                    break
            else:
                self.log.inform("Unable to find tournament %s (%s) in state %s", tourney_info["name"], tourney_info.get("skin",""), TOURNAMENT_STATE_REGISTERING)
                self.factory.can_disconnect = False
                reactor.callLater(10, self.lookForGame, protocol)
                
        elif packet.type == PACKET_POKER_TOURNEY_START:
            game_id2tourney_id[packet.table_serial] = packet.tourney_serial
            protocol.sendPacket(PacketPokerTableJoin(game_id = packet.table_serial, serial = protocol.getSerial()))
            self.state = STATE_RUNNING
            
        elif packet.type == PACKET_POKER_ERROR or packet.type == PACKET_ERROR:
            giveup = True
            if packet.other_type == PACKET_POKER_TOURNEY_REGISTER:
                if packet.code == PacketPokerTourneyRegister.NOT_ENOUGH_MONEY:
                    self.factory.went_broke = True
                elif packet.code == PacketPokerTourneyRegister.ALREADY_REGISTERED:
                    giveup = False
                else:
                    name = self.factory.join_info['name']
                    self.log.inform("Registration refused for tournament %s, try later", name)
                    self.factory.can_disconnect = False
                    reactor.callLater(60, lambda: self.lookForGame(protocol))
                    giveup = False
            if giveup:
                self.log.error("%s", packet)
                protocol.transport.loseConnection()
        
    def _handlePlay(self, protocol, packet):
        if packet.type == PACKET_POKER_BATCH_MODE:
            self.state = STATE_BATCH
        elif packet.type == PACKET_POKER_STREAM_MODE:
            self.state = STATE_RUNNING
            if self.batch_end_action:
                self.batch_end_action()
                self.batch_end_action = None
        elif packet.type == PACKET_POKER_SEAT:
            if packet.seat == -1:
                self.log.inform("Not allowed to get a seat, give up")
                protocol.transport.loseConnection()
            else:
                self.seat = packet.seat
        
        elif packet.type == PACKET_POKER_BLIND_REQUEST:
            if packet.serial == protocol.getSerial():
                protocol.sendPacket(PacketPokerBlind(game_id = packet.game_id, serial = packet.serial))

        elif packet.type == PACKET_POKER_PLAYER_LEAVE:
            if packet.serial == protocol.getSerial():
                if self.factory.join_info['tournament']:
                    self.lookForGame(protocol)

        elif packet.type == PACKET_POKER_WIN:
            if self.factory.rebuy and self.state == STATE_RUNNING and not self.factory.watch:

                game = self.factory.packet2game(packet)
                serial = protocol.getSerial()

                if game and game.isBroke(serial):

                    if self.factory.join_info['tournament']:
                        self.log.crit("tourney rebuy %d", game_id2tourney_id[game.id])
                        protocol.sendPacket(PacketPokerTourneyRebuy(serial=serial, tourney_serial=game_id2tourney_id[game.id]))
                        protocol.sendPacket(PacketPokerSit(serial=serial, game_id=game.id))

                    else:
                        protocol.sendPacket(PacketPokerRebuy(serial=serial, game_id=game.id))
                        protocol.sendPacket(PacketPokerSit(serial=serial, game_id=game.id))
            
        elif packet.type == PACKET_POKER_SELF_IN_POSITION:
            game = self.factory.packet2game(packet)
            if self.state == STATE_RUNNING:
                self.inPosition(protocol, game)
            elif self.state == STATE_BATCH:
                self.batch_end_action = lambda: self.inPosition(protocol, game)

        elif packet.type == PACKET_POKER_SELF_LOST_POSITION:
            if self.state == STATE_BATCH:
                self.batch_end_action = None
                
        elif packet.type == PACKET_POKER_ERROR or packet.type == PACKET_ERROR:
            if packet.other_type == PACKET_POKER_REBUY or packet.other_type == PACKET_POKER_BUY_IN:
                self.factory.went_broke = True
            self.log.error("%s", packet)
            protocol.transport.loseConnection()
    
    def _handleConnection(self, protocol, packet):
        if packet.type == PACKET_SERIAL:
            if self.factory.cash_in:
                note = PokerBot.note_generator.getNote()
                if self.factory.currency_id:
                    note[0] += "?id=" + self.factory.currency_id
                protocol.sendPacket(PacketPokerCashIn(
                    serial=packet.serial,
                    **dict(zip(('url', 'bserial', 'name', 'value'), note))
                ))
        else:
            if self.state == STATE_LOGIN:
                self._handleLogin(protocol, packet)
            elif self.state in (STATE_RECONNECTING, STATE_SEARCHING):
                self._handleSearch(protocol, packet)
            elif self.state in (STATE_RUNNING, STATE_BATCH):
                self._handlePlay(protocol, packet)
                
    def inPosition(self, protocol, game):
        if not game.isBlindAnteRound():
            if self.factory.wait > 0:
                self.factory.can_disconnect = False
                reactor.callLater(self.factory.wait, self.play, protocol, game)
            else:
                self.play(protocol, game)

    def eval(self, game, serial):
        if self.factory.level == 0:
            actions = ("check", "call", "raise")
            return (actions[randint(0, 2)], -1)

        ev = game.handEV(serial, LEVEL2ITERATIONS[self.factory.level]) * 0.001
        actions = game.possibleActions(serial)
        player = game.serial2player[serial]
        
        # the contribution consists of the sum of all bets the player has made
        contribution = game.side_pots['contributions']['total'].get(serial,0)
        bets = game.potAndBetsAmount()
        
        # modify ev to be a bit more pessimistic
        ev -= 0.05
        
        # risk exponent for the additional bets. should be in (0,1]
        risk = 0.9
        
        actions_returns = {}
        
        if 'fold' in actions:
            actions_returns['fold'] = -contribution
            
        if 'check' in actions:
            actions_returns['check'] = bets*ev - (contribution)*(1-ev)
            
        if 'call' in actions:
            highest_amount = game.highestBetNotFold()
            additional_amounts = sum(highest_amount - p.bet for p in game.playersInGame())
            actions_returns['call'] = (bets+additional_amounts**risk)*ev - (contribution+highest_amount-player.bet)*(1-ev)
        
        if 'raise' in actions:
            min_bet, _max_bet, _to_call = game.betLimitsForSerial(serial)
            highest_amount = game.highestBetNotFold() + 2*min_bet
            additional_amounts = sum(highest_amount - p.bet for p in game.playersInGame())
            actions_returns['raise'] = (bets+additional_amounts**risk)*ev - (contribution+highest_amount-player.bet)*(1-ev)
            
        action = max(actions_returns.iteritems(), key=lambda i: i[1])[0]
        self.log.inform("%s actions (%s) (ev = %.2f)", serial, ', '.join('%s %.2f' % kv for kv in actions_returns.items()), ev)
        
        return (action, ev)
        
    def play(self, protocol, game):
        serial = protocol.getSerial()
        name = protocol.getName()
        if serial not in game.serialsNotFold():
            self.log.warn(
                "%s: the server must have decided to play on our behalf before we had a chance to decide "
                "(TIMEOUT happening at the exact same time we reconnected), most likely",
                name
            )
            return
        
        desired_action, ev = self.eval(game, serial)
        self.log.debug("%s serial = %d, hand = %s, board = %s", name, serial, game.getHandAsString(serial), game.getBoardAsString())
        self.log.debug("%s wants to %s (ev = %.2f)", name, desired_action, ev)
        self.log.inform("%s serial = %d, hand = %s, board = %s",
            name, serial, game.getHandAsString(serial), game.getBoardAsString()
        )
        self.log.inform("%s wants to %s (ev = %.2f)", name, desired_action, ev)
        
        if desired_action == "fold":
            protocol.sendPacket(PacketPokerFold(game_id = game.id, serial = serial))
        elif desired_action == "check":
            protocol.sendPacket(PacketPokerCheck(game_id = game.id, serial = serial))
        elif desired_action == "call":
            protocol.sendPacket(PacketPokerCall(game_id = game.id, serial = serial))
        elif desired_action == "raise":
            min_bet, _max_bet, _to_call = game.betLimitsForSerial(serial)
            protocol.sendPacket(PacketPokerRaise(game_id = game.id, serial = serial, amount = min_bet * 2))
        else:
            self.log.warn("=> unexpected actions = %s", actions)
        self.factory.can_disconnect = True

