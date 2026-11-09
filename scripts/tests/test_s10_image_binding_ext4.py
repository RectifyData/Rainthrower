#!/usr/bin/env python3
"""Compare a real mounted ext4 image to its input; requires Linux root and loop mounts."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from s10_image_binding import compare_image_tree, tree_digest


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Root is required for an isolated temporary ext4 loop mount')
    with tempfile.TemporaryDirectory(prefix='s10-ext4-binding-') as directory:
        root = Path(directory)
        source, mount = root / 'source', root / 'mount'
        source.mkdir(); mount.mkdir()
        (source / 'app').mkdir()
        (source / 'app/example.apk').write_bytes(b'fixture')
        (source / 'app/symlink').symlink_to('example.apk')
        image = root / 'vendor.img'
        with image.open('wb') as stream:
            stream.truncate(32 * 1024 * 1024)
        subprocess.run(['/usr/sbin/mke2fs', '-q', '-F', '-t', 'ext4', '-d', str(source), str(image)], check=True)
        subprocess.run(['mount', '-t', 'ext4', '-o', 'loop,ro,noload', str(image), str(mount)], check=True)
        try:
            assert compare_image_tree(source, mount, ext4=True) == tree_digest(source)
            (source / 'app/example.apk').write_bytes(b'mutated')
            try:
                compare_image_tree(source, mount, ext4=True)
                raise AssertionError('A changed source was accepted')
            except ValueError:
                pass
        finally:
            subprocess.run(['umount', str(mount)], check=True)
    print('PASS: real ext4 image matches all source entries and rejects changed source')


if __name__ == '__main__':
    main()
