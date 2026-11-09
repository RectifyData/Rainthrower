#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bind a candidate to the complete filesystems verified inside its images."""
import errno
import hashlib
import json
import os
from pathlib import Path
import stat


def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def tree_digest(root, *, ignore_lost_found=False):
    """Hash every entry's contents, link, Android ownership, mode and SELinux label.

    The root inode itself is not packed from the source root by all image tools.
    An ext4-generated lost+found may be excluded only when absent in the source.
    Never follow a symlink while walking the tree.
    """
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Image input tree must be a real directory: ' + str(root))
    digest = hashlib.sha256()

    def visit(directory, relative):
        with os.scandir(directory) as entries:
            entries = sorted(entries, key=lambda e: os.fsencode(e.name))
        for entry in entries:
            rel = relative / entry.name
            if ignore_lost_found and rel == Path('lost+found'):
                continue
            path = Path(entry.path)
            info = entry.stat(follow_symlinks=False)
            mode = info.st_mode
            if stat.S_ISDIR(mode):
                kind, content = 'directory', None
            elif stat.S_ISREG(mode):
                kind, content = 'file', file_digest(path)
            elif stat.S_ISLNK(mode):
                kind, content = 'symlink', os.readlink(path)
            elif stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
                kind, content = ('character' if stat.S_ISCHR(mode) else 'block'), info.st_rdev
            elif stat.S_ISFIFO(mode):
                kind, content = 'fifo', None
            else:
                raise ValueError('Unsupported image tree entry: ' + str(path))
            try:
                label = os.getxattr(path, 'security.selinux', follow_symlinks=False).hex()
            except OSError as error:
                if error.errno not in (errno.ENODATA, errno.ENOTSUP):
                    raise
                label = None
            row = [str(rel), kind, stat.S_IMODE(mode), info.st_uid, info.st_gid, label, content]
            digest.update(json.dumps(row, ensure_ascii=True, separators=(',', ':')).encode('ascii') + b'\n')
            if kind == 'directory':
                visit(path, rel)
    visit(root, Path())
    return digest.hexdigest()


def compare_image_tree(source, mounted, *, ext4=False):
    source, mounted = Path(source), Path(mounted)
    lost = mounted / 'lost+found'
    generated = ext4 and not os.path.lexists(source / 'lost+found') and os.path.lexists(lost)
    if generated and (lost.is_symlink() or not lost.is_dir() or any(lost.iterdir())):
        raise ValueError('Unexpected content in ext4 lost+found')
    expected = tree_digest(source)
    if tree_digest(mounted, ignore_lost_found=generated) != expected:
        raise ValueError('Complete packed filesystem differs from the candidate')
    return expected


def verify_binding(candidate, images):
    candidate = Path(candidate)
    trees = images.get('tree_sha256')
    if (images.get('schema') != 2 or images.get('apk_signatures_verified') is not True or not isinstance(trees, dict)
            or set(trees) != {'system', 'vendor'}
            or images.get('input_sha256') != file_digest(candidate / 'input.json')):
        raise ValueError('Image manifest lacks a matching complete candidate binding')
    for name, root in (('system', candidate / 'tree'), ('vendor', candidate / 'vendor')):
        if not isinstance(trees[name], str) or tree_digest(root) != trees[name]:
            raise ValueError('Candidate tree changed after image verification: ' + name)
