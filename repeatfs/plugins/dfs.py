#! /usr/bin/env python3

#
#   This file is part of RepeatFS
#
#   SPDX-FileCopyrightText: 2024  Anthony Westbrook, University of New Hampshire <anthony.westbrook@unh.edu>
#
#   SPDX-License-Identifier: GPL-3.0-only WITH LicenseRef-repeatfs-graphviz-linking-source-exception
#

import base64

from repeatfs.plugins.distributed.provenance.management import Management as Provenance
from repeatfs.plugins.plugins import PluginBase

from repeatfs.plugins.distributed.utils.id.nodeID import ID
from repeatfs.plugins.distributed.utils.config.config import Config
from repeatfs.plugins.distributed.netman.msg import msgQueue, MessagesRegister
from repeatfs.plugins.distributed.netman.netman import BasicTCPPeerClient
from repeatfs.plugins.distributed.netman.messages import TimestampRequest, TimestampReply


class Plugin(PluginBase):
    """ dfs plugin """
    CONFIG_FIELDS = {
        "server": (False, False, "", str, "regex to select which filenames to snapshot"),
        "port": (False, False, 9000, int, "server listening port"),
        "localport": (False, False, 9001, int, "local port to listen on"),
        "id":(False, False, "", str, "node id")
    }

    def init(self):
        self.intercept = True

    def mount(self):
        # overwrite the old provenance management with the distributed one
        self.provenance = Provenance(self.core)
        self.core.provenance = self.provenance

        client_cfg = Config()
        client_q = msgQueue()
        client_reg = MessagesRegister()

        self.core.netman = BasicTCPPeerClient(
            id=ID(1, 1, True),
            local_host="0.0.0.0",
            local_port=self.configuration["localport"],
            msgQ=client_q,
            message_register=client_reg,
            cfg=client_cfg,
        )
        self.core.netman.register_message_type(TimestampReply)
        self.core.netman.register_reply_message(TimestampReply)
        self.provenance.serverID=ID(0, 0)
        try:
            ok = self.core.netman.connect_to_peer(self.configuration["server"], self.configuration["port"])
        except Exception as e:
            print("Failed to connect to server:", e)
            raise Exception("Failed to connect to server")
        if not ok:
            print("ok:", ok)
            raise Exception("Failed to connect to server")

    def unmount(self):
        self.core.netman.close()

    def s_get_access(self, path, mode):
        result = self.core.routing.s_get_access(path, mode, self.pidx + 1)
        return result

    def s_change_mode(self, path, mode):
        result = self.core.routing.s_change_mode(path, mode, self.pidx + 1)
        return result

    def s_change_owner(self, path, uid, gid):
        result = self.core.routing.s_change_owner(path, uid, gid, self.pidx + 1)
        return result

    def s_get_attributes(self, path, info):
        result = self.core.routing.s_get_attributes(path, info, self.pidx + 1)
        return result

    def s_open_directory(self, path):
        result = self.core.routing.s_open_directory(path, self.pidx + 1)
        return result

    def s_get_directory(self, path, fh):
        result = list(self.core.routing.s_get_directory(path, fh, self.pidx + 1))
        return result

    def s_get_link(self, path):
        result = self.core.routing.s_get_link(path, self.pidx + 1)
        return result

    def s_create_node(self, path, mode, dev):
        result = self.core.routing.s_create_node(path, mode, dev, self.pidx + 1)
        return result

    def s_remove_directory(self, path):
        result = self.core.routing.s_remove_directory(path, self.pidx + 1)
        return result

    def s_create_directory(self, path, mode):
        result = self.core.routing.s_create_directory(path, mode, self.pidx + 1)
        return result

    def s_fs_stats(self, path):
        result = self.core.routing.s_fs_stats(path, self.pidx + 1)
        return result

    def s_unlink(self, path):
        result = self.core.routing.s_unlink(path, self.pidx + 1)
        return result

    def s_make_symlink(self, src, link):
        result = self.core.routing.s_make_symlink(src, link, self.pidx + 1)
        return result

    def s_make_hardlink(self, src, link):
        result = self.core.routing.s_make_hardlink(src, link, self.pidx + 1)
        return result

    def s_rename(self, old, new):
        result = self.core.routing.s_rename(old, new, self.pidx + 1)
        return result

    def s_update_time(self, path, times):
        result = self.core.routing.s_update_time(path, times, self.pidx + 1)
        return result

    def s_open(self, path, info, mode):
        result = self.core.routing.s_open(path, info, mode, self.pidx + 1)
        return result

    def s_read(self, path, length, offset, info):
        result = self.core.routing.s_read(path, length, offset, info, self.pidx + 1)
        return result

    def s_write(self, path, buf, offset, info):
        result = self.core.routing.s_write(path, buf, offset, info, self.pidx + 1)
        return result

    def s_truncate(self, path, length, info):
        result = self.core.routing.s_truncate(path, length, info, self.pidx + 1)
        return result

    def s_close(self, info):
        result = self.core.routing.s_close(info, self.pidx + 1)
        return result

    def s_sync(self, path, info):
        result = self.core.routing.s_sync(path, info, self.pidx + 1)
        return result