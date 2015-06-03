#! /usr/bin/env python
# -*- coding: iso-8859-1 -*-

# chimera - observatory automation system
# Copyright (C) 2006-2007  P. Henrique Silva <henrique@astro.ufsc.br>

# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301, USA.

import os
import time
import threading

from chimera.util.coord import Coord

from chimera.interfaces.dome import DomeStatus
from chimera.instruments.dome import DomeBase
from chimera.interfaces.dome import Mode

from chimera.core.lock import lock
from chimera.core.exceptions import ObjectNotFoundException,ChimeraException
from chimera.core.constants import SYSTEM_CONFIG_DIRECTORY

class AstelcoException(ChimeraException):
    pass


class AstelcoDomeException(ChimeraException):
    pass


class AstelcoDome(DomeBase):
    '''
    AstelcoDome interfaces chimera with TSI system to control dome.
    '''

    __config__ = {"maxidletime": 90.,
                  "stabilization_time": 5.,
                  'tpl':'/TPL/0'}


    def __init__(self):
        DomeBase.__init__(self)

        self._position = 0
        self._slewing = False
        self._maxSlewTime = 300.

        self._syncmode = 0

        self._slitOpen = False
        self._slitMoving = False

        self._abort = threading.Event()

        self._errorNo = 0

        self._errorString = ""

        # debug log
        self._debugLog = None

        try:
            self._debugLog = open(os.path.join(SYSTEM_CONFIG_DIRECTORY,
                                               "astelcodome-debug.log"), "w")
        except IOError, e:
            self.log.warning("Could not create astelco debug file (%s)" % str(e))


    def __start__(self):

        self.setHz(1. / self["maxidletime"])

        self.open()

        tpl = self.getTPL()
        # Reading position
        self._position = tpl.getobject('POSITION.HORIZONTAL.DOME')
        self._slitOpen = tpl.getobject('AUXILIARY.DOME.REALPOS') > 0
        self._slitPos = tpl.getobject('AUXILIARY.DOME.REALPOS')
        self._syncmode = tpl.getobject('POINTING.SETUP.DOME.SYNCMODE')
        self._tel = self.getTelescope()

        if self._syncmode == 0:
            self._mode = Mode.Stand
        else:
            self._mode = Mode.Track

        return True

    def __stop__(self):  # converted to Astelco
        if self.isSlewing():
            self.abortSlew()

        return True

    @lock
    def slewToAz(self, az):
        # Astelco Dome will only enable slew if it is not tracking
        # If told to slew I will check if the dome is syncronized with
        # with the telescope. If it is not I will wait until it gets
        # in sync or timeout...

        if self._mode == Mode.Track:
            self.log.warning('Dome is in track mode... Slew is completely controled by AsTelOS...')
            self.slewBegin(az)

            start_time = time.time()
            self._abort.clear()
            self._slewing = True
            caz = self.getAz()

            while self.isSlewing():
                time.sleep(1.0)
                if time.time() > (start_time + self._maxSlewTime):
                    self.log.warning('Dome syncronization timed-out...')
                    self.slewComplete(self.getAz(), DomeStatus.TIMEOUT)
                    return 0
                elif self._abort.isSet():
                    self._slewing = False
                    self.slewComplete(self.getAz(), DomeStatus.ABORTED)
                    return 0
                elif abs(caz - self.getAz()) < 1e-6:
                    self._slewing = False
                    self.slewComplete(self.getAz(), DomeStatus.OK)
                    return 0
                else:
                    caz = self.getAz()

            self.slewComplete(self.getAz(), DomeStatus.OK)
        else:
            self.log.info('Slewing to %f...' % az)

            self.slewBegin(az)

            start_time = time.time()
            self._abort.clear()
            self._slewing = True
            caz = self.getAz()

            tpl = self.getTPL()

            tpl.set('POSITION.INSTRUMENTAL.DOME[0].TARGETPOS', '%f' % az)

            time.sleep(self['stabilization_time'])

            while self.isSlewing():

                if time.time() > (start_time + self._maxSlewTime):
                    self.log.warning('Dome syncronization timed-out...')
                    self.slewComplete(self.getAz(), DomeStatus.TIMEOUT)
                    return 0
                elif self._abort.isSet():
                    self._slewing = False
                    tpl.set('POSITION.INSTRUMENTAL.DOME[0].TARGETPOS', caz)
                    self.slewComplete(self.getAz(), DomeStatus.ABORTED)
                    return 0
                elif abs(caz - self.getAz()) < 1e-6:
                    self._slewing = False
                    self.slewComplete(self.getAz(), DomeStatus.OK)
                    return 0
                else:
                    caz = self.getAz()

            self.slewComplete(self.getAz(), DomeStatus.OK)


    @lock
    def stand(self):
        self.log.debug("[mode] standing...")
        tpl = self.getTPL()
        tpl.set('POINTING.SETUP.DOME.SYNCMODE', 0)
        self._syncmode = tpl.getobject('POINTING.SETUP.DOME.SYNCMODE')
        self._mode = Mode.Stand

    @lock
    def track(self):
        self.log.debug("[mode] tracking...")
        tpl = self.getTPL()
        tpl.set('POINTING.SETUP.DOME.SYNCMODE', 4)
        self._syncmode = tpl.getobject('POINTING.SETUP.DOME.SYNCMODE')
        self._mode = Mode.Track

    @lock
    def control(self):
        '''
        Just keep the connection alive. Everything else is done by astelco.

        :return: True
        '''

        tpl = self.getTPL()
        self.log.debug('[control] %s' % tpl.getobject('SERVER.UPTIME'))

        return True


    def isSlewing(self):

        tpl = self.getTPL()
        motionState = tpl.getobject('TELESCOPE.MOTION_STATE')
        return ( motionState != 11 )

    def abortSlew(self):
        self._abort.set()

    @lock
    def getAz(self):

        tpl = self.getTPL()
        ret = tpl.getobject('POSITION.INSTRUMENTAL.DOME[0].CURRPOS')
        if ret:
            self._position = ret
        elif not self._position:
            self._position = 0.

        return Coord.fromD(self._position)

    def getMode(self):

        tpl = self.getTPL()
        self._syncmode = tpl.getobject('POINTING.SETUP.DOME.SYNCMODE')

        if self._syncmode == 0:
            self._mode = Mode.Stand
        else:
            self._mode = Mode.Track
        return self._mode

    @lock
    def open(self):

        try:
            tpl = self.getTPL()
            tpl.get('SERVER.INFO.DEVICE')
            self.log.debug(tpl.getobject('SERVER.UPTIME'))
        except:
            raise AstelcoException("Error while opening %s." % self["device"])

        return True

    @lock
    def openSlit(self):

        # check slit condition

        if self._slitMoving:
            raise AstelcoException('Slit already opening...')
        elif self._slitOpen:
            self.log.info('Slit already opened...')
            return 0

        self._slitMoving = True
        self._abort.clear()
        tpl = self.getTPL()

        cmdid = tpl.set('AUXILIARY.DOME.TARGETPOS', 1, wait=True)

        time_start = time.time()

        cmdComplete = False
        while True:

            realpos = tpl.getobject('AUXILIARY.DOME.REALPOS')

            if tpl.commands_sent[cmdid].complete and ( (not realpos == 1) and (not cmdComplete) ):
                    self.log.warning('Slit opened! Opening Flap...')
                    cmdid = self._tpl.set('AUXILIARY.DOME.TARGETPOS', 1, wait=True)
                    cmdComplete = True
                    time_start = time.time()

            if realpos == 1:
                self._slitMoving = False
                self._slitOpen = True
                return DomeStatus.OK
            elif self._abort.isSet():
                self._slitMoving = False
                return DomeStatus.ABORTED
            elif time.time() > time_start + self._maxSlewTime:
                return DomeStatus.TIMEOUT

        return True

    @lock
    def closeSlit(self):
        if not self._slitOpen:
            self.log.info('Slit already closed')
            return 0

        self.log.info("Closing slit")

        tpl = self.getTPL()
        cmdid = tpl.set('AUXILIARY.DOME.TARGETPOS', 0)

        time_start = time.time()

        while True:

            for line in tpl.commands_sent[cmdid].received:
                self.log.debug(line)

            realpos = tpl.getobject('AUXILIARY.DOME.REALPOS')
            if realpos == 0:
                self._slitMoving = False
                self._slitOpen = False
                return DomeStatus.OK
            elif self._abort.isSet():
                self._slitMoving = False
                return DomeStatus.ABORTED
            elif time.time() > time_start + self._maxSlewTime:
                return DomeStatus.TIMEOUT


    def isSlitOpen(self):
        tpl = self.getTPL()
        self._slitPos = tpl.getobject('AUXILIARY.DOME.REALPOS')
        self._slitOpen = self._slitPos > 0
        return self._slitOpen

    # utilitaries
    def getTPL(self):
        try:
            p = self.getManager().getProxy(self['tpl'], lazy=True)
            if not p.ping():
                return False
            else:
                return p
        except ObjectNotFoundException:
            return False
