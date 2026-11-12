#! /usr/bin/env python3

#
#   This file is part of RepeatFS
#
#   SPDX-FileCopyrightText: 2024  Anthony Westbrook, University of New Hampshire <anthony.westbrook@unh.edu>
#
#   SPDX-License-Identifier: GPL-3.0-only WITH LicenseRef-repeatfs-graphviz-linking-source-exception
#

import base64
import json

import os
import stat
import sys
import threading
import errno
from repeatfs.api import API
from repeatfs.cache_entry import CacheEntry
from repeatfs.configuration import Configuration
from repeatfs.descriptor_entry import DescriptorEntry
from repeatfs.file_entry import FileEntry
from repeatfs.fuse import Fuse
from repeatfs.routing import Routing
from repeatfs.plugins.distributed.provenance.management import Management as Provenance
from repeatfs.plugins.plugins import PluginBase as Plugins

from repeatfs.plugins.plugins import PluginBase

from repeatfs.plugins.distributed.utils.id.nodeID import ID
from repeatfs.plugins.distributed.utils.config.config import Config
from repeatfs.plugins.distributed.netman.msg import msgQueue, MessagesRegister
from repeatfs.plugins.distributed.netman.netman import BasicTCPPeerClient
from repeatfs.plugins.distributed.netman.messages import TimestampRequest, TimestampReply

class Plugin(PluginBase):
    """ dfs plugin """
    CONFIG_FIELDS = {
        "server": (False, False, "", str, "regex to select which filenames to snapshot")
    }

    def init(self):
        self.intercept = True
        # overwrite the old provenance management with the distributed one
        self.provenance = Provenance(self.core)
        

    def mount(self):
        print("Mounting DFS plugin")

    def unmount(self):
        pass

    def s_get_access(self, path, mode):
        """ Check if access mode is granted for path """
        file_entry = FileEntry(path, self)

        # Register provenance
        if file_entry.provenance:
            self.provenance.register_op_read(file_entry, Provenance.OP_ACCESS)

        if file_entry.derived_source:
            # Change derived directory execute check to target file read check
            if (file_entry.file_type == stat.S_IFDIR) and (mode & os.X_OK):
                mode = (mode - os.X_OK) | os.R_OK

            return self.get_access(file_entry.derived_source.paths["abs_virt"], mode)
        else:
            if not os.access(file_entry.paths["abs_real"], mode):
                raise self.fuse.fuse_error(errno.EACCES)

        return 0

    def s_change_mode(self, path, mode): 
        """ Change file mode """
        file_entry = FileEntry(path, self)

        # Register provenance
        if file_entry.provenance:
            self.provenance.register_op_read(file_entry, Provenance.OP_CHMOD)

        # Changing modes of virtual files is not allowed
        if file_entry.derived_source:
            raise self.fuse.fuse_error(errno.EPERM)

        return os.chmod(file_entry.paths["abs_real"], mode)

    def s_change_owner(self, path, uid, gid):
        """ Change file owner """
        file_entry = FileEntry(path, self)

        # Register provenance
        if file_entry.provenance:
            self.provenance.register_op_read(file_entry, Provenance.OP_CHOWN)

        # Changing owner of virtual files is not allowed
        if file_entry.derived_source:
            raise self.fuse.fuse_error(errno.EPERM)

        return os.chown(file_entry.paths["abs_real"], uid, gid)

    def s_get_attributes(self, path, info):
        """ Get attributes for a file """
        # Lookup descriptor if specified
        if info and info.fh != 0:
            desc_entry = DescriptorEntry.get(info.fh)
            file_entry = desc_entry.file_entry

            # Register provenance
            if file_entry.provenance:
                self.provenance.register_read(desc_entry.id, Provenance.OP_ATTR)
        else:
            file_entry = FileEntry(path, self)

            # Register provenance
            if file_entry.provenance:
                self.provenance.register_op_read(file_entry, Provenance.OP_ATTR)

        # Check for invalid file entry
        if not file_entry.valid:
            raise self.fuse.fuse_error(errno.ENOENT)

        if file_entry.api:
            stats = {}
            stats['st_mode'] = stat.S_IFREG | 0o777
            stats['st_size'] = self.configuration.values["api_size"]
            stats['st_mtime'] = 0
            stats['st_nlink'] = 1

        elif file_entry.derived_source:
            stats = self.fuse.getattr(file_entry.derived_source.paths["abs_virt"], info)
            stats['st_mode'] &= 0x01FF
            stats['st_mode'] |= file_entry.file_type
            stats['st_size'] = file_entry.init_size
            stats['st_mtime'] = 0
            if file_entry.file_type == stat.S_IFREG:
                # If file is cached, report correct file size (continue to override init size if in reset state)
                if file_entry.paths["abs_real"] in CacheEntry.entries:
                    cache_entry = CacheEntry.entries[file_entry.paths["abs_real"]]
                    if cache_entry.size > 0 or cache_entry.final:
                        stats['st_size'] = cache_entry.size
                    stats['st_mtime'] = cache_entry.mtime

        else:
            lstats = os.lstat(file_entry.paths["abs_real"])
            stats = {key: getattr(lstats, key) for key in dir(lstats) if "st_" in key}

        return stats

    def s_open_directory(self, path):
        """ Open directory by checking read access """
        self.s_get_access(path, os.R_OK)

        return 0

    def s_get_directory(self, path, fh):
        """ Get file listing of directory """
        file_entry = FileEntry(path, self)

        # Register provenance (directory)
        if file_entry.provenance:
            self.provenance.register_op_read(file_entry, Provenance.OP_GETDIR)

        # Ensure valid directory
        if file_entry.derived_source and (file_entry.file_type != stat.S_IFDIR):
            raise self.fuse.fuse_error(errno.ENOTDIR)
        if not file_entry.derived_source and not os.path.isdir(file_entry.paths["abs_real"]):
            raise self.fuse.fuse_error(errno.ENOTDIR)

        child_paths = self.get_files(file_entry)

        for child_path in child_paths:
            file_entry = FileEntry(child_path, self)

            # Register provenance (file)
            self.provenance.register_op_read(file_entry, Provenance.OP_GETDIR)
            yield child_path

    def s_get_link(self, path):
        """ Retrieve path to which symbolic link points """
        file_entry = FileEntry(path, self)

        # Register provenance
        if file_entry.provenance:
            self.provenance.register_op_read(file_entry, Provenance.OP_GETLINK)

        # Virtual files are never symbolic links
        if file_entry.derived_source:
            raise self.fuse.fuse_error(errno.EINVAL)

        return os.readlink(file_entry.paths["abs_real"])
    
    def s_create_node(self, path, mode, dev):
        """ Create a node """
        file_entry = FileEntry(path, self)

        # Only create nodes in real directories
        if file_entry.derived_source:
            raise self.fuse.fuse_error(errno.EPERM)

        os.mknod(file_entry.paths["abs_real"], mode, dev)

        # Register provenance after create (links to previous versions through get_attr call)
        if file_entry.provenance:
            self.provenance.register_op_write(file_entry, Provenance.OP_MKNOD, create=True)


    def s_remove_directory(self, path):
        """ Remove a directory """
        file_entry = FileEntry(path, self)

        # Register provenance
        if file_entry.provenance:
            self.provenance.register_op_write(file_entry, Provenance.OP_RMDIR)

        # Only remove real directories
        if file_entry.derived_source:
            raise self.fuse.fuse_error(errno.EPERM)

        return os.rmdir(file_entry.paths["abs_real"])


    def s_create_directory(self, path, mode):
        """ Create a directory """
        file_entry = FileEntry(path, self)

        # Only create subdirectories in real directories
        if file_entry.derived_source:
            raise self.fuse.fuse_error(errno.EPERM)

        err = os.mkdir(file_entry.paths["abs_real"], mode)

        # Register provenance after create (links to previous versions through get_attr call)
        if file_entry.provenance:
            self.provenance.register_op_write(file_entry, Provenance.OP_MKDIR, create=True)

        return err

    def s_fs_stats(self, path):
        """ Retrieve file system statistics """
        file_entry = FileEntry(path, self)

        # Register provenance
        if file_entry.provenance:
            self.provenance.register_op_read(file_entry, Provenance.OP_STATS)

        stv = os.statvfs(file_entry.paths["abs_real"])

        return dict((key, getattr(stv, key)) for key in (
            'f_bavail', 'f_bfree', 'f_blocks', 'f_bsize', 'f_favail',
            'f_ffree', 'f_files', 'f_flag', 'f_frsize', 'f_namemax'))

    def s_unlink(self, path):
        """ Delete a file """
        file_entry = FileEntry(path, self)

        # Register provenance
        if file_entry.provenance:
            self.provenance.register_op_write(file_entry, Provenance.OP_UNLINK)

        # Only remove real files
        if file_entry.derived_source:
            raise self.fuse.fuse_error(errno.EPERM)

        return os.unlink(file_entry.paths["abs_real"])

    def s_make_symlink(self, src, link):
        """ Create symbolic link """
        # Symlinks send src path exactly as specified, do not make an entry
        src_entry = FileEntry(src, self)
        link_entry = FileEntry(link, self)

        # Register provenance
        if src_entry.provenance:
            self.provenance.register_op_read(src_entry, Provenance.OP_MKSYM)

        # Only create link in real directories
        if link_entry.derived_source:
            raise self.fuse.fuse_error(errno.EPERM)

        err = os.symlink(src, link_entry.paths["abs_real"])

        # Register provenance
        if link_entry.provenance:
            self.provenance.register_op_write(link_entry, Provenance.OP_MKSYM, create=True)

        return err

    def s_make_hardlink(self, src, link):
        """ Create hard link """
        src_entry = FileEntry(src, self)
        link_entry = FileEntry(link, self)

        # Register provenance
        if src_entry.provenance:
            self.provenance.register_op_read(src_entry, Provenance.OP_MKHARD)

        # Only create link in real directories
        if link_entry.derived_source:
            raise self.fuse.fuse_error(errno.EPERM)

        err = os.link(src_entry.paths["abs_real"], link_entry.paths["abs_real"])

        # Register provenance
        if link_entry.provenance:
                self.provenance.register_op_write(link_entry, Provenance.OP_MKHARD, create=True)

        return err


    def s_rename(self, old, new):
        """ Move file """
        old_entry = FileEntry(old, self)
        new_entry = FileEntry(new, self)

        # Register provenance
        if old_entry.provenance:
            self.provenance.register_op_read(old_entry, Provenance.OP_MOVE)

        # Ensure neither source nor destination is virtual
        if old_entry.derived_source or new_entry.derived_source or old_entry.api or new_entry.api:
            raise self.fuse.fuse_error(errno.EPERM)

        err = os.rename(old_entry.paths["abs_real"], new_entry.paths["abs_real"])

        # Register provenance after create (links to previous versions through get_attr call)
        if new_entry.provenance:
            self.provenance.register_op_write(new_entry, Provenance.OP_MOVE, create=True)

        # Update active descriptors
        DescriptorEntry.rename(old_entry.paths["abs_real"], new_entry.paths["abs_virt"], self)

        return err

    def s_update_time(self, path, times):
        """ Update modified timestamp """
        file_entry = FileEntry(path, self)

        # Register provenance
        if file_entry.provenance:
            self.provenance.register_op_write(file_entry, Provenance.OP_TIME)

        if file_entry.derived_source or file_entry.api:
            return 0
        else:
            return os.utime(file_entry.paths["abs_real"], times)
        
    # TODO: Fully check/support modes (especially create modes)
    def s_open(self, path, info, mode):
        """ Open a file and create a descriptor """
        file_entry = FileEntry(path, self)
        create = mode is not None and not file_entry.valid

        # Create file if mode was set and path is not valid (ie not a virtual path)
        if create:
            os.close(os.open(file_entry.paths["abs_real"], os.O_WRONLY | os.O_CREAT, mode))
            file_entry = FileEntry(path, self)
            info.flags = os.O_RDWR

        # Ensure file exists
        if not file_entry.valid:
            raise self.fuse.fuse_error(errno.ENOENT)

        # Do not allow writes from non-owner to derived files
        cache_entry = None
        if self.is_flag_write(info.flags) and file_entry.derived_source:
            if file_entry.paths["abs_real"] not in CacheEntry.entries:
                raise self.fuse.fuse_error(errno.EACCES)

            pid = self.fuse.fuse_get_context()[2]
            cache_entry = CacheEntry.entries[file_entry.paths["abs_real"]]
            if not cache_entry.process_io.context_owner(pid=pid):
                raise self.fuse.fuse_error(errno.EACCES)

        # Propagate read access for derived files
        if file_entry.derived_source:
            self.get_access(path, os.R_OK)

        # Disable caches for derived/api/o_direct files
        if file_entry.derived_source or file_entry.api or (self.direct_support and (info.flags & os.O_DIRECT > 0)):
            info.direct_io = self.direct_support
            info.keep_cache = False
        else:
            info.direct_io = False
            info.keep_cache = True

        # Create descriptor
        flags = info.flags & ~os.O_DIRECT if self.direct_support else info.flags
        info.fh = self.create_descriptor(file_entry, flags)

        # Register provenance after create (links to previous versions through get_attr call), initial read for cached files
        if file_entry.provenance:
            self.routing.p_register_open(info.fh, read=True, write=create, update_last=create)

        return 0

    def s_read(self, path, length, offset, info):
        """ Perform file read operation """
        desc_entry = DescriptorEntry.get(info.fh)

        # Register provenance
        if desc_entry.file_entry.provenance:
            self.provenance.register_read(info.fh)

        # Perform operation
        if desc_entry.file_entry.api:
            read_buf = API.get(desc_entry.id).read(length)

        elif desc_entry.file_entry.derived_source:
            file_path = desc_entry.file_entry.paths["abs_real"]
            read_buf = CacheEntry.entries[file_path].io(CacheEntry.IO_READ, offset, None, length, info.fh)

        else:
            with desc_entry.fs_lock:
                try:
                    os.lseek(desc_entry.fs_descriptor, offset, os.SEEK_SET)
                    read_buf = os.read(desc_entry.fs_descriptor, length)
                except Exception as e:
                    self.log("Read error: {}".format(e), self.LOG_DEBUG)

        return read_buf

    def s_write(self, path, buf, offset, info):
        """ Perform file write operation """
        desc_entry = DescriptorEntry.get(info.fh)

        # Register provenance
        if desc_entry.file_entry.provenance:
            self.provenance.register_write(info.fh)

        if desc_entry.file_entry.api:
            write_len = API.get(desc_entry.id).write(buf)

        elif desc_entry.file_entry.derived_source:
            file_path = desc_entry.file_entry.paths["abs_real"]
            cache_entry = CacheEntry.entries[file_path]

            # Write stream buffer portion first, then direct memory for writes positions before stream
            direct_size = cache_entry.process_io.write(buf, offset, info.fh)
            if direct_size > 0:
                CacheEntry.entries[file_path].io(CacheEntry.IO_WRITE, offset, buf, direct_size, info.fh)
            write_len = len(buf)

        else:
            with desc_entry.fs_lock:
                try:
                    os.lseek(desc_entry.fs_descriptor, offset, os.SEEK_SET)
                    write_len = os.write(desc_entry.fs_descriptor, buf)
                except Exception as e:
                    self.log("Write error: {}".format(e), self.LOG_DEBUG)

        return write_len

    def s_truncate(self, path, length, info):
        """ Perform file truncate operation """
        err = 0
        desc_id = info.fh if info else 0

        # Create descriptor if not provided
        if desc_id == 0:
            file_entry = FileEntry(path, self)
            if not file_entry.valid: raise self.fuse.fuse_error(errno.ENOENT)
            desc_id = self.create_descriptor(file_entry, os.O_WRONLY | os.O_TRUNC)
            desc_entry = DescriptorEntry.get(desc_id)

            # Register provenance (ephemeral)
            if file_entry.provenance:
                self.provenance.register_op_write(file_entry, Provenance.OP_IO | Provenance.OP_TRUNCATE)
        else:
            desc_entry = DescriptorEntry.get(desc_id)

            # Register provenance
            if desc_entry.file_entry.provenance:
                self.provenance.register_write(desc_entry.id, op_type=Provenance.OP_IO | Provenance.OP_TRUNCATE)

        if desc_entry.file_entry.api:
            # Truncation has no effect on API
            pass

        elif desc_entry.file_entry.derived_source:
            # Attempt to truncate stream buffer, otherwise direct memory
            file_path = desc_entry.file_entry.paths["abs_real"]
            cache_entry = CacheEntry.entries[file_path]
            if not cache_entry.process_io.truncate(length, desc_id, False):
                CacheEntry.entries[file_path].io(CacheEntry.IO_TRUNCATE, length, None, 1, desc_id)

        else:
            err = os.truncate(desc_entry.fs_descriptor, length)

        # Cleanup temporary descriptor
        if info is None or info.fh == 0:
            self.remove_descriptor(desc_id)

        return err

    def s_close(self, info):
        """ Close a file and remove the descriptor """
        desc_entry = DescriptorEntry.get(info.fh)

        # Register provenance
        if desc_entry.file_entry.provenance:
            self.routing.p_register_close(info.fh)

        # Close and remove descriptor
        ret_code = self.remove_descriptor(info.fh)

        return ret_code

    def s_sync(self, path, info):
        """ Perform a sync for non-virtual files """
        desc_entry = DescriptorEntry.get(info.fh)

        if desc_entry.file_entry.derived_source or desc_entry.file_entry.api:
            return 0

        return os.fsync(desc_entry.fs_descriptor)
