#
# -*- py-indent-offset: 4; coding: utf-8; mode: python -*-
#
# Copyright (C) 2006, 2007, 2008, 2009 Loic Dachary <loic@dachary.org>
# Copyright (C) 2008             Johan Euphrosine <proppy@aminche.com>
# Copyright (C) 2004, 2005, 2006 Mekensleep <licensing@mekensleep.com>
#                                24 rue vieille du temple, 75004 Paris
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
#  Johan Euphrosine <proppy@aminche.com>
#  Henry Precheur <henry@precheur.org> (2004)
#  Cedric Pinson <mornifle@plopbyte.net> (2004-2006)

from pokernetwork.user import User
from pokernetwork import log as network_log
log = network_log.get_child("pokerauthnopassword")
    
class PokerAuth:

    log = log.get_child('PokerAuth')

    def __init__(self, db, settings):
        self.db = db
        self.type2auth = {}
        self.auto_create_account = settings.headerGet("/server/@auto_create_account") == 'yes'

    def SetLevel(self, type, level):
        self.type2auth[type] = level

    def GetLevel(self, type):
        return type in self.type2auth and self.type2auth[type]

    def auth(self, name, password):
        cursor = self.db.cursor()
        cursor.execute("SELECT serial, password, privilege FROM users WHERE name = %s", name)
        numrows = int(cursor.rowcount)
        serial = 0
        privilege = User.REGULAR
        if numrows <= 0:
            self.log.debug("user %s does not exist", name)
            cursor.close()
            return ( False, "Invalid login or password" )
        elif numrows > 1:
            self.log.error("more than one row for %s", name)
            cursor.close()
            return ( False, "Invalid login or password" )
        else:
            (serial, password_sql, privilege) = cursor.fetchone()
            cursor.close()

        return ( (serial, name, privilege), None )

def get_auth_instance(db, settings):
    return PokerAuth(db, settings)
