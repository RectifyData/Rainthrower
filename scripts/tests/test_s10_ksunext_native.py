#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the patched Legacy exec handlers with host-side kernel stubs.

Set S10_KSU_REFERENCE to the pinned Legacy checkout; tests reconstruct its
unmodified source from Git, apply the maintained patch, and compile the actual
handler bodies. This is not a kernel build or a device test.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PIN = 'b933b1f728116a456fbebf27b6a8772a640cdf23'
PATCH = ROOT / 'scripts/ksunext/ksunext-exec-fd.patch'


def function(source, marker):
    start = source.index(marker)
    end = source.index('\n}\n', start) + 3
    return source[start:end]


@unittest.skipUnless(os.environ.get('S10_KSU_REFERENCE'), 'set S10_KSU_REFERENCE to the pinned checkout')
class LegacyNativeTests(unittest.TestCase):
    def test_manual_exec_paths(self):
        source = Path(os.environ['S10_KSU_REFERENCE'])
        self.assertEqual(subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip(), PIN)
        with tempfile.TemporaryDirectory(prefix='s10-ksu-native-') as tmp:
            root = Path(tmp)
            for name in ('kernel/Kbuild', 'kernel/core/init.c', 'kernel/feature/sucompat.c', 'kernel/runtime/ksud_integration.c'):
                dest = root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(subprocess.check_output(['git', '-C', str(source), 'show', f'HEAD:{name}']))
            subprocess.run(['git', 'apply', str(PATCH)], cwd=root, check=True)
            compat = function((root / 'kernel/feature/sucompat.c').read_text(), 'int ksu_handle_execveat_sucompat(')
            bridge = function((root / 'kernel/core/init.c').read_text(), 'int ksu_handle_execveat(')
            harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>
#define AT_FDCWD (-100)
#define O_RDONLY 0
#define GFP_KERNEL 0
#define SU_PATH "/system/bin/su"
#define SH_PATH "/system/bin/sh"
#define KSUD_PATH "/data/adb/ksud"
#define likely(x) (x)
#define unlikely(x) (x)
#define IS_ERR(x) ((void *)(x) == (void *)-1)
#define pr_info(...) ((void)0)
struct filename { char *name; };
struct user_arg_ptr { int unused; };
struct file { int unused; };
struct ksu_sulog_pending_event { int unused; };
struct uid { int val; };
static bool ksu_su_compat_enabled = true, allowed = true, present = true;
static int escape_result, escaped, captured, emitted, event_status, boot_calls;
static struct file fake_file;
static struct ksu_sulog_pending_event event;
static struct uid current_uid(void) { return (struct uid){12345}; }
static bool ksu_is_allow_uid_for_current(int x) { (void)x; return allowed; }
static struct ksu_sulog_pending_event *ksu_sulog_capture_root_execve_kern(const char *s, const char *const *args, int flags) {
    assert(strcmp(s, SU_PATH) == 0 && args == NULL && flags == 0); captured++; return &event;
}
static int escape_with_root_profile(void) { escaped++; return escape_result; }
static void ksu_sulog_emit_pending(struct ksu_sulog_pending_event *p, int ret, int flags) {
    assert(p == &event && flags == 0); emitted++; event_status = ret;
}
static struct file *ksu_filp_open_compat(const char *s, int flags, int mode) {
    assert(strcmp(s, KSUD_PATH) == 0 && flags == 0 && mode == 0);
    return present ? &fake_file : (void *)-1;
}
static int filp_close(struct file *f, void *id) { assert(f == &fake_file && !id); return 0; }
static int ksu_handle_execveat_ksud(int *fd, struct filename **name, struct user_arg_ptr *argv, struct user_arg_ptr *envp, int *flags) {
    (void)fd; (void)name; (void)argv; (void)envp; (void)flags; boot_calls++; return 0;
}
''' + compat + '\n' + bridge + r'''
static void reset(void) { escaped = captured = emitted = event_status = boot_calls = 0; escape_result = 0; allowed = true; present = true; }
int main(void) {
    char name[64]; struct filename filename = {name}, *p = &filename; int fd = AT_FDCWD, flags = 0;
    strcpy(name, SU_PATH); reset(); ksu_handle_execveat(&fd, &p, NULL, NULL, &flags);
    assert(!strcmp(name, KSUD_PATH) && escaped == 1 && captured == 1 && emitted == 1 && event_status == 0 && boot_calls == 1);
    strcpy(name, SU_PATH); reset(); present = false; ksu_handle_execveat(&fd, &p, NULL, NULL, &flags);
    assert(!strcmp(name, SH_PATH) && escaped == 1 && emitted == 1 && event_status == 0);
    strcpy(name, SU_PATH); reset(); escape_result = -12; ksu_handle_execveat(&fd, &p, NULL, NULL, &flags);
    assert(!strcmp(name, SU_PATH) && escaped == 1 && emitted == 1 && event_status == -12);
    strcpy(name, SU_PATH); reset(); allowed = false; ksu_handle_execveat(&fd, &p, NULL, NULL, &flags);
    assert(!strcmp(name, SU_PATH) && !escaped && !captured && !emitted);
    strcpy(name, SU_PATH); reset(); flags = 1; ksu_handle_execveat(&fd, &p, NULL, NULL, &flags);
    assert(!strcmp(name, SU_PATH) && !escaped && boot_calls == 1);
    flags = 0; fd = 42; ksu_handle_execveat(&fd, &p, NULL, NULL, &flags);
    assert(!strcmp(name, SU_PATH) && !escaped && boot_calls == 2);
    puts("manual exec handlers passed");
}
'''
            (root / 'test.c').write_text(harness)
            subprocess.run(['gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', str(root / 'test.c'), '-o', str(root / 'test')], check=True)
            subprocess.run([str(root / 'test')], check=True)


if __name__ == '__main__':
    unittest.main()
