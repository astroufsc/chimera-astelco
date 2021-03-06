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
import copy
import numpy as np

from chimera.util.coord import Coord
from chimera.util.position import Position
from chimera.interfaces.dome import DomeStatus
from chimera.instruments.dome import DomeBase
from chimera.interfaces.dome import Mode, InvalidDomePositionException

from chimera.core.lock import lock
from chimera.core.exceptions import ObjectNotFoundException
from chimera.core.constants import SYSTEM_CONFIG_DIRECTORY

from astelcoexceptions import AstelcoException, AstelcoDomeException


class AstelcoDome(DomeBase):
    '''
    AstelcoDome interfaces chimera with TSI system to control dome.
    '''

    __config__ = {"maxidletime": 90.,
                  "stabilization_time": 5.,
                  'tpl': '/TPL/0'}

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
        # with the telescope. If it is not it� will wait until it gets
        # in sync or timeout...

        if self.getMode() == Mode.Track:
            raise AstelcoDomeException('Dome is in track mode... Slew is completely controled by AsTelOS...')
            # self.log.warning('Dome is in track mode... Slew is completely controled by AsTelOS...')
            # self.slewBegin(az)
            #
            # start_time = time.time()
            # self._abort.clear()
            # self._slewing = True
            # caz = self.getAz()
            #
            # while self.isSlewing():
            #     # time.sleep(1.0)
            #     if time.time() > (start_time + self._maxSlewTime):
            #         self.log.warning('Dome syncronization timed-out...')
            #         self.slewComplete(self.getAz(), DomeStatus.TIMEOUT)
            #         return 0
            #     elif self._abort.isSet():
            #         self._slewing = False
            #         self.slewComplete(self.getAz(), DomeStatus.ABORTED)
            #         return 0
            #     elif abs(caz - self.getAz()) < 1e-6:
            #         self._slewing = False
            #         self.slewComplete(self.getAz(), DomeStatus.OK)
            #         return 0
            #     else:
            #         caz = self.getAz()
            #
            # self.slewComplete(self.getAz(), DomeStatus.OK)
        else:
            self.log.info('Slewing to %f...' % az)

            start_time = time.time()
            self._abort.clear()
            self._slewing = True
            current_az = self.getAz()

            tpl = self.getTPL()

            self.slewBegin(az)
            cmdid = tpl.set('POSITION.INSTRUMENTAL.DOME[0].TARGETPOS', '%f' % az)
            reference_alt = Coord.fromD(0.)
            desired_position = Position.fromAltAz(reference_alt,
                                                  az)
            # Wait for command to be completed
            cmd = tpl.getCmd(cmdid)
            while not cmd.complete:
                if time.time() > (start_time + self._maxSlewTime):
                    self.log.warning('Dome syncronization timed-out...')
                    self.slewComplete(self.getAz(), DomeStatus.ABORTED)
                    return 0
                cmd = tpl.getCmd(cmdid)

            # time.sleep(self['stabilization_time'])
            # Wait dome arrive on desired position

            while True:

                current_position = Position.fromAltAz(reference_alt,
                                                      current_az)
                if time.time() > (start_time + self._maxSlewTime):
                    self.slewComplete(self.getAz(), DomeStatus.ABORTED)
                    raise AstelcoDomeException('Dome syncronization timed-out...')
                elif self._abort.isSet():
                    self._slewing = False
                    tpl.set('POSITION.INSTRUMENTAL.DOME[0].TARGETPOS', current_az)
                    self.slewComplete(self.getAz(), DomeStatus.ABORTED)
                    return 0
                elif abs(current_position.angsep(desired_position)) < tpl.getobject(
                        'POINTING.SETUP.DOME.MAX_DEVIATION') * 2.0:
                    self._slewing = False
                    self.slewComplete(self.getAz(), DomeStatus.OK)
                    return 0
                else:
                    current_az = self.getAz()

            self.slewComplete(self.getAz(), DomeStatus.OK)

    @lock
    def syncWithTel(self):
        self.syncBegin()

        self.log.debug('[sync] Check if dome is in sync with telescope')

        if self.getMode() == Mode.Track:
            self.log.warning('Dome is in track mode... Slew is completely controled by AsTelOS...'
                             'Waiting for dome to reach expected position')

            start_time = time.time()

            tpl = self.getTPL()
            ref_altitude = Coord.fromD(0.)
            target_az = Coord.fromD(tpl.getobject('POSITION.INSTRUMENTAL.DOME[0].TARGETPOS'))
            target_position = Position.fromAltAz(ref_altitude,
                                                 target_az)
            while True:
                current_az = self.getAz()
                current_position = Position.fromAltAz(ref_altitude,
                                                      current_az)
                self.log.debug('Current az: %s | Target az: %s' % (current_az.toDMS(), target_az.toDMS()))
                if time.time() > (start_time + self._maxSlewTime):
                    if abs(target_position.angsep(current_position).D) < tpl.getobject(
                            'POINTING.SETUP.DOME.MAX_DEVIATION') * 4.0:
                        self.log.warning("[sync] Dome too far from target position!")
                        break
                    else:
                        self.syncComplete()
                        raise AstelcoDomeException("Dome synchronization timed-out")
                elif abs(target_position.angsep(current_position).D) < tpl.getobject(
                        'POINTING.SETUP.DOME.MAX_DEVIATION') * 2.0:
                    break

        self.syncComplete()
        self.log.debug('[sync] Dome in sync')

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


    def isSlewing(self):

        tpl = self.getTPL()
        motionState = tpl.getobject('TELESCOPE.MOTION_STATE')
        return (motionState != 11)

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

    @lock
    def getAzOffset(self):

        tpl = self.getTPL()
        ret = tpl.getobject('POSITION.INSTRUMENTAL.DOME[0].OFFSET')

        return Coord.fromD(ret)

    def getMode(self):

        tpl = self.getTPL()
        syncmode = tpl.getobject('POINTING.SETUP.DOME.SYNCMODE')

        return Mode.Stand if syncmode == 0 else Mode.Track

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

        if self.slitMoving():
            raise AstelcoException('Slit already opening...')
        elif self.isSlitOpen():
            self.log.info('Slit already opened...')
            return 0

        self._abort.clear()
        tpl = self.getTPL()

        cmdid = tpl.set('AUXILIARY.DOME.TARGETPOS', 2, wait=False)

        time_start = time.time()

        cmd = tpl.getCmd(cmdid)
        cmdComplete = False
        while not cmd.complete:

            if self._abort.isSet():
                return DomeStatus.ABORTED
            elif time.time() > time_start + self._maxSlewTime:
                return DomeStatus.TIMEOUT

            cmd = tpl.getCmd(cmdid)
        self.log.debug('Command complete... Waiting while slit opens...')
        time_start = time.time()

        while not self.isSlitOpen():
            if time.time() > time_start + self._maxSlewTime:
                return DomeStatus.TIMEOUT


        return DomeStatus.OK

        # realpos = tpl.getobject('AUXILIARY.DOME.REALPOS')
        #
        # if realpos == 1:
        #     return DomeStatus.OK
        #
        # self.log.warning('Slit opened! Opening Flap...')

    def openFlap(self):

        if not self.isSlitOpen():
            self.log.warning('Slit is closed. Cannot open Flap.')
            raise InvalidDomePositionException("Cannot open dome flap with slit closed.")

        tpl = self.getTPL()
        cmdid = tpl.set('AUXILIARY.DOME.TARGETPOS', 4, wait=False)
        cmd = tpl.getCmd(cmdid)

        time_start = time.time()

        while not cmd.complete:

            if self._abort.isSet():
                return DomeStatus.ABORTED
            elif time.time() > time_start + self._maxSlewTime:
                return DomeStatus.TIMEOUT

            cmd = tpl.getCmd(cmdid)

        realpos = tpl.getobject('AUXILIARY.DOME.REALPOS')

        if realpos == 1:
            return DomeStatus.OK
        else:
            return DomeStatus.ABORTED

            # return DomeStatus.OK

    @lock
    def closeSlit(self):
        if not self.isSlitOpen():
            self.log.info('Slit already closed')
            return 0
        elif self.isFlapOpen():
            self.log.warning("Flap is open. Closing everything...")

        self.log.info("Closing slit")

        tpl = self.getTPL()

        realpos = tpl.getobject('AUXILIARY.DOME.REALPOS')

        cmdid = tpl.set('AUXILIARY.DOME.TARGETPOS', 0, wait=False)

        time_start = time.time()

        cmd = tpl.getCmd(cmdid)

        while not cmd.complete:

            # for line in tpl.commands_sent[cmdid].received:
            #     self.log.debug(line)

            if realpos == 0:
                return DomeStatus.OK
            elif self._abort.isSet():
                return DomeStatus.ABORTED
            elif time.time() > time_start + self._maxSlewTime:
                return DomeStatus.TIMEOUT

            cmd = tpl.getCmd(cmdid)

        realpos = tpl.getobject('AUXILIARY.DOME.REALPOS')

        while realpos != 0:

            if self._abort.isSet():
                return DomeStatus.ABORTED
            elif time.time() > time_start + self._maxSlewTime:
                return DomeStatus.TIMEOUT

            realpos = tpl.getobject('AUXILIARY.DOME.REALPOS')

        return DomeStatus.OK

    @lock
    def closeFlap(self):

        # Todo: Implement Close Flap. Still needs to find a way to close the flap.
        if not self.isFlapOpen():
            self.log.info('Flap already closed')
            return 0

        tpl = self.getTPL()
        cmdid = tpl.set('AUXILIARY.DOME.TARGETPOS', 2, wait=False)

        time_start = time.time()

        cmd = tpl.getCmd(cmdid)

        self._abort.clear()

        while not cmd.complete:

            if self._abort.isSet():
                return DomeStatus.ABORTED
            elif time.time() > time_start + self._maxSlewTime:
                return DomeStatus.TIMEOUT

            cmd = tpl.getCmd(cmdid)

        while self.isFlapOpen():

            if self._abort.isSet():
                return DomeStatus.ABORTED
            elif time.time() > time_start + self._maxSlewTime:
                return DomeStatus.TIMEOUT

        return DomeStatus.OK

    def slitMoving(self):
        # Todo: Find command to check if slit is movng
        return False

    def isSlitOpen(self):
        tpl = self.getTPL()
        openmask = tpl.getobject('AUXILIARY.DOME.OPEN_MASK')
        return (openmask & (1 << 1)) != 0

    def isFlapOpen(self):
        tpl = self.getTPL()
        openmask = tpl.getobject('AUXILIARY.DOME.OPEN_MASK')
        return (openmask & (1 << 2)) != 0

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

    def getMetadata(self, request):
        # Check first if there is metadata from an metadata override method.
        md = self.getMetadataOverride(request)
        if md is not None:
            return md
        # If not, just go on with the instrument's default metadata.

        baseHDR = super(DomeBase, self).getMetadata(request)
        newHDR = [("DOME_AZ", self.getAz().toDMS().__str__(), "Dome Azimuth"),
                  ("D_OFFSET", self.getAzOffset().toDMS().__str__(), "Dome Azimuth offset")]

        for new in newHDR:
            baseHDR.append(new)

        return baseHDR
