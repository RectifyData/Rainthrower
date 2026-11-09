# KernelSU-Next Legacy S10 source integration

`s10-legacy-manual-hooks.patch` targets the Exynos9820 kernel commit
`a97afb2c431802523a89fb875a3faadb7dcaac26` and KernelSU-Next Legacy commit
`b933b1f728116a456fbebf27b6a8772a640cdf23`. It restores manual exec, access,
stat, read, input and reboot hooks adapted to that Legacy revision. The exec
hook covers native and compat execve/execveat at their shared implementation;
the input hook is installed once. The kernel patch also fixes the existing
`path_umount` backport for Samsung's pointer-based `CONFIG_KDP_NS` layout.

`ksunext-exec-fd.patch` fixes Legacy's exec argument types and unsafe argv
logging, checks execveat arguments before manual sucompat escalation, handles
failed escalation and uses the upstream shell fallback when ksud is absent.
It also atomically claims the init.rc proxy and prevents Kbuild from editing
kernel sources after source verification. The pinned tree must already contain
the required backports; missing ones cause a build error.

With the pinned Legacy checkout available, run the host-stub tests with
`S10_KSU_REFERENCE=<checkout> python3 -B -m unittest discover -s scripts/tests
-p 'test_s10_ksunext*.py'`. These exercise the patched handler bodies but are
not a kernel compile or Android runtime test.

Prepare external checkouts with `scripts/prepare_s10_ksunext.py --kernel
<kernel> --ksu <ksu> --record <new-external-record>`. Pass that record as
`--ksu-integration` to `scripts/build_oneui9_s10_kernel.py` to build in KernelSU
and its manual hooks. The original kernel source diff and the new patch are
checked independently; an existing different KSU installation is rejected.

**Incomplete:** this is KernelSU-Next Legacy only. It does not integrate SUSFS,
remove an existing Magisk installation from userdata, preload a manager, or
establish compatibility with the target Android image. It has not been built
with the pinned Android compiler or tested on a device. Do not treat its record
as a release-ready kernel.
