#! /usr/bin/env python3

#
#   This file is part of RepeatFS
#
#   SPDX-FileCopyrightText: 2020  Anthony Westbrook, University of New Hampshire <anthony.westbrook@unh.edu>
#
#   SPDX-License-Identifier: GPL-3.0-only WITH LicenseRef-repeatfs-graphviz-linking-source-exception
#


import time
from repeatfs.descriptor_entry import DescriptorEntry
from repeatfs.file_entry import FileEntry
from repeatfs.plugins.distributed.provenance.file_record import FileRecord
from repeatfs.plugins.distributed.provenance.process_record import ProcessRecord
from repeatfs.plugins.distributed.utils.logger.basicLogger import Logger
import threading
class IORecord:
    """ Provides IO information """
    IO_READ, IO_WRITE = range(2)
    IO_START, IO_END = range(2)

    _lookup = dict()

    @classmethod
    def get(cls, descriptor, management, pid=None):
        """ Get IO for a descriptor and pid """
        with management.lock:
            if descriptor not in cls._lookup:
                return {} if pid is None else None

            if not pid:
                return cls._lookup[descriptor]
            else:
                return cls._lookup[descriptor].get(pid, None)

    @classmethod
    def remove(cls, descriptor, management):
        """ Remove IO for a descriptor """
        with management.lock:
            to_delete=cls._lookup.pop(descriptor, None)
            for _, record in to_delete.items():
                record.stop_update()

    def __init__(self, descriptor, pid, management, io_time=None):
        """ Process related provenance information """
        self.management = management
        self.descriptor = descriptor
        self.pid = pid
        self.times = [[None, None], [None, None]]
        self.to_update = [[None, None], [None, None]]
        self.operations = [0, 0]

        # Save record to lookup
        with self.management.lock:
            self._lookup.setdefault(descriptor, dict())
            self._lookup[descriptor].setdefault(pid, self)

    def update_callback(self):
        with self.management.lock:
            for i in self.times:
                if self.to_update[i]:
                    if not self.times[i]:
                        Logger.ERROR("empty times.")
                        return
                    set_time=self.management.clock.tick()
                    self.times[i][self.IO_END] = set_time

    def stop_update(self):
        if hasattr(self, "stop_event") and self.stop_event is not None:
            self.stop_event.set()

        if hasattr(self, "update_thread") and self.update_thread is not None:
            self.update_thread.join(timeout=2)
            self.update_thread = None

    def update_callback(self):
        # phase 1: collect targets
        with self.management.lock:
            targets = [
                io_type
                for io_type in self.times
                if self.times[io_type][self.IO_END] == -1
            ]

        # phase 2: tick outside lock, then write back safely
        for io_type in targets:
            set_time = self.management.clock.tick()

            with self.management.lock:
                # re-check, because another thread may have updated it already, then we don't want to re-update it.
                if self.times[io_type][self.IO_END] == -1:
                    self.times[io_type][self.IO_END] = set_time

    def schedule_update(self, batch):
        if hasattr(self, "update_thread") and self.update_thread is not None:
            if self.update_thread.is_alive():
                return
        self.stop_event = threading.Event()
        def loop():
            while not self.stop_event.is_set():
                self.update_callback()
                self.stop_event.wait(batch)
        self.update_thread = threading.Thread(target=loop, daemon=True)
        self.update_thread.start()
            
    def update(self, io_type, op_type, io_time=None,batch=-1):
        """ Update IO record latest times  """
        # set_time = io_time if io_time else round(time.time(), 3)
        if batch > 0:
            with self.management.lock:
                # first time
                if not self.times[io_type][self.IO_START]:
                    set_time = io_time if io_time else self.management.clock.tick()
                    self.times[io_type][self.IO_START] = set_time
                    self.times[io_type][self.IO_END] = set_time
                    self.schedule_update(batch)
                else:
                    self.times[io_type][self.IO_END] = -1
                self.operations[io_type] |= op_type
            return
        

        set_time = io_time if io_time else self.management.clock.tick()
        with self.management.lock:
            # Update times (set start if first operation)
            if not self.times[io_type][self.IO_START]:
                self.times[io_type][self.IO_START] = set_time

            self.times[io_type][self.IO_END] = set_time

            # Add operation
            self.operations[io_type] |= op_type

    def write(self):
        """ Write IO record to the database """
        dir_names = ("read", "write")

        with self.management.lock:
            for direction in range(2):
                query_lookup = ("SELECT start, ops FROM {0} "
                                "WHERE phost = ? AND pstart = ? AND pid = ? AND path = ? AND fcreate = ? ".format(dir_names[direction]))
                query_update = ("REPLACE INTO {0} VALUES (?, ?, ?, ?, ?, ?, ?, ?) ".format(dir_names[direction]))

                file_entry = DescriptorEntry.get(self.descriptor).file_entry
                file_record = FileRecord.get(self.descriptor, self.management)
                process_record = ProcessRecord.get(self.pid, self.management)

                if self.times[direction][self.IO_START] is not None:
                    # Lookup start and operations for previous descriptors between same process and file
                    cursor = self.management.db_connection.cursor()
                    values = (self.management.system_name, process_record.pstart, self.pid, file_entry.paths["abs_real"], file_record.fcreate)
                    cursor.execute(query_lookup, values)
                    result = cursor.fetchone()
                    
                    # Merge values if applicable
                    start = self.times[direction][self.IO_START]
                    end = self.times[direction][self.IO_END]
                    if end == -1:
                        end = self.management.clock.tick()
                    ops = self.operations[direction]
                    if result:
                        start = result[0]
                        ops |= result[1]

                    # Update I/O table
                    values = (self.management.system_name, process_record.pstart, self.pid, file_entry.paths["abs_real"], file_record.fcreate, start, end, ops)
                    cursor.execute(query_update, values)

            self.management.db_connection.commit()
