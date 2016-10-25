#!/usr/bin/env python
#
# Copyright (C) 2016 GNS3 Technologies Inc.
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
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
import logging
import asyncio
import psutil

from gns3server.compute.vmware import (
    VMware,
    VMwareError
)

from .base_gns3_vm import BaseGNS3VM
from .gns3_vm_error import GNS3VMError
log = logging.getLogger(__name__)


class VMwareGNS3VM(BaseGNS3VM):

    def __init__(self, controller):

        self._engine = "vmware"
        super().__init__(controller)
        self._vmware_manager = VMware()
        self._vmx_path = None

    @property
    def vmx_path(self):
        return self._vmx_path

    @asyncio.coroutine
    def _execute(self, subcommand, args, timeout=60):

        try:
            result = yield from self._vmware_manager.execute(subcommand, args, timeout)
            return (''.join(result))
        except VMwareError as e:
            raise GNS3VMError("Error while executing VMware command: {}".format(e))

    @asyncio.coroutine
    def _is_running(self):
        result = yield from self._vmware_manager.execute("list", [])
        if self._vmx_path in result:
            return True
        return False

    @asyncio.coroutine
    def _set_vcpus_ram(self, vcpus, ram):
        """
        Set the number of vCPU cores and amount of RAM for the GNS3 VM.

        :param vcpus: number of vCPU cores
        :param ram: amount of RAM
        """

        # memory must be a multiple of 4 (VMware requirement)
        if ram % 4 != 0:
            raise GNS3VMError("Allocated memory for the GNS3 VM must be a multiple of 4".format(available_ram))

        available_vcpus = psutil.cpu_count(logical=False)
        if vcpus > available_vcpus:
            raise GNS3VMError("You have allocated too many vCPUs for the GNS3 VM! (max available is {} vCPUs)".format(available_vcpus))

        try:
            pairs = VMware.parse_vmware_file(self._vmx_path)
            pairs["numvcpus"] = str(vcpus)
            pairs["memsize"] = str(ram)
            VMware.write_vmx_file(self._vmx_path, pairs)
            log.info("GNS3 VM vCPU count set to {} and RAM amount set to {}".format(vcpus, ram))
        except OSError as e:
            raise GNS3VMError('Could not read/write VMware VMX file "{}": {}'.format(self._vmx_path, e))

    @asyncio.coroutine
    def list(self):
        """
        List all VMware VMs
        """

        return (yield from self._vmware_manager.list_vms())

    @asyncio.coroutine
    def start(self):
        """
        Starts the GNS3 VM.
        """

        vms = yield from self.list()
        for vm in vms:
            if vm["vmname"] == self.vmname:
                self._vmx_path = vm["vmx_path"]
                break

        # check we have a valid VMX file path
        if not self._vmx_path:
            raise GNS3VMError("VMWare VM {} not found".format(self.vmname))
        if not os.path.exists(self._vmx_path):
            raise GNS3VMError("VMware VMX file {} doesn't exist".format(self._vmx_path))

        # check if the VMware guest tools are installed
        vmware_tools_state = yield from self._execute("checkToolsState", [self._vmx_path])
        if vmware_tools_state not in ("installed", "running"):
            raise GNS3VMError("VMware tools are not installed in {}".format(self.vmname))

        running = yield from self._is_running()
        if not running:
            log.info("Update GNS3 VM settings")
            # set the number of vCPUs and amount of RAM
            yield from self._set_vcpus_ram(self.vcpus, self.ram)

            # start the VM
            args = [self._vmx_path]
            if self._headless:
                args.extend(["nogui"])
            yield from self._execute("start", args)
            log.info("GNS3 VM has been started")

        # get the guest IP address (first adapter only)
        trial = 120
        guest_ip_address = ""
        while True:
            guest_ip_address = yield from self._execute("readVariable", [self._vmx_path, "guestVar", "gns3.eth0"], timeout=120)
            guest_ip_address = guest_ip_address.strip()
            if len(guest_ip_address) != 0:
                break
            trial -= 1
            # If ip not found fallback on old method
            if trial == 0:
                guest_ip_address = yield from self._execute("getGuestIPAddress", [self._vmx_path, "-wait"], timeout=120)
                break
            yield from asyncio.sleep(1)
        self.ip_address = guest_ip_address
        log.info("GNS3 VM IP address set to {}".format(guest_ip_address))
        self.running = True

    @asyncio.coroutine
    def suspend(self):
        """
        Suspend the GNS3 VM.
        """

        if self._vmx_path is None:
            raise GNS3VMError("No VMX path configured, can't suspend the VM")
        try:
            yield from self._execute("suspend", [self._vmx_path])
        except GNS3VMError as e:
            log.warning("Error when suspending the VM: {}".format(str(e)))
        log.info("GNS3 VM has been suspended")
        self.running = False

    @asyncio.coroutine
    def stop(self):
        """
        Stops the GNS3 VM.
        """

        if self._vmx_path is None:
            raise GNS3VMError("No VMX path configured, can't stop the VM")
        try:
            yield from self._execute("stop", [self._vmx_path, "soft"])
        except GNS3VMError as e:
            log.warning("Error when stopping the VM: {}".format(str(e)))
        log.info("GNS3 VM has been stopped")
        self.running = False
