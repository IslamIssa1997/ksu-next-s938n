#!/usr/bin/env python3
"""Apply Samsung KDP/RKP/DEFEX patches to KernelSU Next source tree."""

import os
import re
import sys
import shutil


def read_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def write_file(path, content):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)


def check(name, old, content):
    if old not in content:
        raise ValueError(f"[{name}] pattern not found — upstream changed?\nExpected:\n{old[:200]!r}")


def copy_new_files(ksu_dir, script_dir):
    compat_dir = os.path.join(ksu_dir, 'kernel', 'compat')
    include_dir = os.path.join(ksu_dir, 'kernel', 'include')
    os.makedirs(compat_dir, exist_ok=True)
    os.makedirs(include_dir, exist_ok=True)

    files = {
        'samsung_defex.c': os.path.join(compat_dir, 'samsung_defex.c'),
        'samsung_defex.h': os.path.join(compat_dir, 'samsung_defex.h'),
        'samsung_kdp.c':   os.path.join(compat_dir, 'samsung_kdp.c'),
        'ksu_samsung_kdp.h': os.path.join(include_dir, 'ksu_samsung_kdp.h'),
    }
    for src, dst in files.items():
        shutil.copy(os.path.join(script_dir, src), dst)
        print(f"  copied {src} -> {os.path.relpath(dst, ksu_dir)}")


def patch_kbuild(ksu_dir):
    path = os.path.join(ksu_dir, 'kernel', 'Kbuild')
    c = read_file(path)

    # 1. Add compat objects after the first kernelsu-objs line
    old = 'kernelsu-objs := core/init.o\n'
    new = ('kernelsu-objs := core/init.o\n\n'
           'kernelsu-objs += compat/samsung_kdp.o\n'
           'kernelsu-objs += compat/samsung_defex.o\n')
    check('Kbuild-objs', old, c)
    c = c.replace(old, new, 1)

    # 2. Add Samsung config flags before the closing endif of the outer ifdef block
    # The outer block ends with the x86 check endif, then a blank line before selinux include
    old = ('ifeq ($(CONFIG_KSU_X86_PATCH_SYSCALL_DISPATCHER),y)\n'
           'ccflags-y += -DCONFIG_KSU_X86_PATCH_SYSCALL_DISPATCHER=1\n'
           'endif\n'
           'endif\n')
    new = ('ifeq ($(CONFIG_KSU_X86_PATCH_SYSCALL_DISPATCHER),y)\n'
           'ccflags-y += -DCONFIG_KSU_X86_PATCH_SYSCALL_DISPATCHER=1\n'
           'endif\n'
           'ifeq ($(CONFIG_KSU_SAMSUNG_KDP),y)\n'
           'ccflags-y += -DCONFIG_KSU_SAMSUNG_KDP=1\n'
           'endif\n'
           'ifeq ($(CONFIG_KSU_SAMSUNG_RKP),y)\n'
           'ccflags-y += -DCONFIG_KSU_SAMSUNG_RKP=1\n'
           'endif\n'
           'ifeq ($(CONFIG_KSU_SAMSUNG_DEFEX),y)\n'
           'ccflags-y += -DCONFIG_KSU_SAMSUNG_DEFEX=1\n'
           'endif\n'
           'ifeq ($(CONFIG_KSU_SAMSUNG_NO_PATCH_TEXT),y)\n'
           'ccflags-y += -DCONFIG_KSU_SAMSUNG_NO_PATCH_TEXT=1\n'
           'endif\n'
           'endif\n')
    check('Kbuild-samsung-flags', old, c)
    c = c.replace(old, new, 1)

    # 3. Add parent directory to include path so compat/ can #include "ksu_samsung_kdp.h"
    old = 'ccflags-y += -I$(KSU_KERNEL_DIR) -I$(KSU_KERNEL_DIR)/include\n'
    new = 'ccflags-y += -I$(KSU_KERNEL_DIR) -I$(KSU_KERNEL_DIR)/include -I$(KSU_KERNEL_DIR)/..\n'
    check('Kbuild-include', old, c)
    c = c.replace(old, new, 1)

    write_file(path, c)
    print("  patched: kernel/Kbuild")


def patch_init_c(ksu_dir):
    path = os.path.join(ksu_dir, 'kernel', 'core', 'init.c')
    c = read_file(path)

    # 1. Add Samsung includes after symbol_resolver include
    old = '#include "infra/symbol_resolver.h"\n'
    new = ('#include "infra/symbol_resolver.h"\n'
           '#include "ksu_samsung_kdp.h"\n'
           '#include "compat/samsung_defex.h"\n')
    check('init.c-includes', old, c)
    c = c.replace(old, new, 1)

    # 2. Add int ret; at the start of kernelsu_init body
    old = 'int __init kernelsu_init(void)\n{\n'
    new = 'int __init kernelsu_init(void)\n{\n\tint ret;\n\n'
    check('init.c-ret-decl', old, c)
    c = c.replace(old, new, 1)

    # 3. Restructure init order: move symbol_resolver before prepare_creds,
    #    add kdp_init and defex_init with proper error handling.
    old = ('\tksu_cred = prepare_creds();\n'
           '\tif (!ksu_cred) {\n'
           '\t\tpr_err("prepare cred failed!\\n");\n'
           '\t\treturn -ENOSYS;\n'
           '\t}\n'
           '\n'
           '\tksu_init_symbol_resolver();\n'
           '\tksu_syscall_hook_init();\n')
    new = ('#ifdef CONFIG_KSU_SAMSUNG_KDP\n'
           '\tpr_info("Samsung KDP credential reference handling enabled\\n");\n'
           '#endif\n'
           '\n'
           '\tksu_init_symbol_resolver();\n'
           '\tret = ksu_samsung_kdp_init();\n'
           '\tif (ret)\n'
           '\t\treturn ret;\n'
           '\n'
           '\tksu_cred = prepare_creds();\n'
           '\tif (!ksu_cred) {\n'
           '\t\tpr_err("prepare cred failed!\\n");\n'
           '\t\tksu_samsung_kdp_exit();\n'
           '\t\treturn -ENOSYS;\n'
           '\t}\n'
           '\n'
           '\tret = ksu_samsung_defex_init();\n'
           '\tif (ret) {\n'
           '\t\tksu_put_cred(ksu_cred);\n'
           '\t\tksu_samsung_kdp_exit();\n'
           '\t\treturn ret;\n'
           '\t}\n'
           '\n'
           '\tksu_syscall_hook_init();\n')
    check('init.c-init-order', old, c)
    c = c.replace(old, new, 1)

    # 4. Replace put_cred in exit with Samsung-aware cleanup
    old = '\tput_cred(ksu_cred);\n'
    new = ('\tksu_samsung_defex_exit();\n'
           '\tksu_put_cred(ksu_cred);\n'
           '\tksu_samsung_kdp_exit();\n')
    check('init.c-put_cred', old, c)
    c = c.replace(old, new, 1)

    write_file(path, c)
    print("  patched: kernel/core/init.c")


def patch_syscall_hook_c(ksu_dir):
    path = os.path.join(ksu_dir, 'kernel', 'hook', 'arm64', 'syscall_hook.c')
    c = read_file(path)

    # 0. Guard patch_syscall_table() on Samsung RKP — only this function needs
    #    the NO_PATCH_TEXT protection, not ksu_patch_text() globally.
    #    selinux_hide also uses ksu_patch_text() on normal data pages, so we
    #    must NOT disable ksu_patch_text() itself.
    old = ('static int patch_syscall_table(int nr, syscall_fn_t fn)\n'
           '{\n'
           '    if (ksu_syscall_table == NULL)\n'
           '        return -ENOENT;\n')
    new = ('static int patch_syscall_table(int nr, syscall_fn_t fn)\n'
           '{\n'
           '#ifdef CONFIG_KSU_SAMSUNG_NO_PATCH_TEXT\n'
           '    /* Samsung RKP: syscall table is hypervisor-protected; use kprobe fallback */\n'
           '    return -EOPNOTSUPP;\n'
           '#endif\n'
           '    if (ksu_syscall_table == NULL)\n'
           '        return -ENOENT;\n')
    check('patch_syscall_table-rkp-guard', old, c)
    c = c.replace(old, new, 1)

    # 1. Change ksu_syscall_table_hook from void to int, add return values
    old = ('// Direct syscall table patching: overwrite syscall_table[nr] with fn,\n'
           '// save original to *old, and record for restoration at module exit.\n'
           'void ksu_syscall_table_hook(int nr, syscall_fn_t fn, syscall_fn_t *old)\n'
           '{\n'
           '    if (ksu_syscall_table == NULL)\n'
           '        return;\n'
           '    if (nr < 0 || nr >= __NR_syscalls) {\n'
           '        pr_info("invalid nr: %d\\n", nr);\n'
           '        return;\n'
           '    }\n'
           '\n'
           '    mutex_lock(&hooked_entries_lock);\n'
           '\n'
           '    syscall_fn_t orig = READ_ONCE(ksu_syscall_table[nr]);\n'
           '    if (old)\n'
           '        *old = orig;\n'
           '\n'
           '    // Record for later restoration\n'
           '    int i;\n'
           '    bool found = false;\n'
           '    for (i = 0; i < hooked_count; i++) {\n'
           '        if (hooked_entries[i].nr == nr) {\n'
           '            found = true;\n'
           '            break;\n'
           '        }\n'
           '    }\n'
           '    if (!found) {\n'
           '        if (hooked_count < ARRAY_SIZE(hooked_entries)) {\n'
           '            hooked_entries[hooked_count].nr = nr;\n'
           '            hooked_entries[hooked_count].orig = orig;\n'
           '            hooked_count++;\n'
           '        } else {\n'
           '            pr_warn(\n'
           '                "hooked_entries full, cannot track syscall %d for restoration\\n",\n'
           '                nr);\n'
           '        }\n'
           '    }\n'
           '\n'
           '    patch_syscall_table(nr, fn);\n'
           '\n'
           '    mutex_unlock(&hooked_entries_lock);\n'
           '}')
    new = ('// Direct syscall table patching: overwrite syscall_table[nr] with fn,\n'
           '// save original to *old, and record for restoration at module exit.\n'
           'int ksu_syscall_table_hook(int nr, syscall_fn_t fn, syscall_fn_t *old)\n'
           '{\n'
           '    int ret;\n'
           '\n'
           '    if (ksu_syscall_table == NULL)\n'
           '        return -ENOENT;\n'
           '    if (nr < 0 || nr >= __NR_syscalls) {\n'
           '        pr_info("invalid nr: %d\\n", nr);\n'
           '        return -EINVAL;\n'
           '    }\n'
           '\n'
           '    mutex_lock(&hooked_entries_lock);\n'
           '\n'
           '    syscall_fn_t orig = READ_ONCE(ksu_syscall_table[nr]);\n'
           '    if (old)\n'
           '        *old = orig;\n'
           '\n'
           '    // Record for later restoration\n'
           '    int i;\n'
           '    bool found = false;\n'
           '    for (i = 0; i < hooked_count; i++) {\n'
           '        if (hooked_entries[i].nr == nr) {\n'
           '            found = true;\n'
           '            break;\n'
           '        }\n'
           '    }\n'
           '    if (!found && hooked_count >= ARRAY_SIZE(hooked_entries)) {\n'
           '        pr_warn("hooked_entries full, cannot track syscall %d for restoration\\n", nr);\n'
           '        mutex_unlock(&hooked_entries_lock);\n'
           '        return -ENOSPC;\n'
           '    }\n'
           '\n'
           '    ret = patch_syscall_table(nr, fn);\n'
           '    if (ret) {\n'
           '        mutex_unlock(&hooked_entries_lock);\n'
           '        return ret;\n'
           '    }\n'
           '\n'
           '    if (!found) {\n'
           '        hooked_entries[hooked_count].nr = nr;\n'
           '        hooked_entries[hooked_count].orig = orig;\n'
           '        hooked_count++;\n'
           '    }\n'
           '\n'
           '    mutex_unlock(&hooked_entries_lock);\n'
           '    return 0;\n'
           '}')
    check('syscall_hook.c-hook-fn', old, c)
    c = c.replace(old, new, 1)

    # 2. Make dispatcher install check the return value
    old = ('    ksu_dispatcher_nr = ni_slot;\n'
           '    ksu_syscall_table_hook(ksu_dispatcher_nr,\n'
           '                           (syscall_fn_t)ksu_syscall_dispatcher, NULL);\n'
           '    pr_info("dispatcher installed at slot %d\\n", ksu_dispatcher_nr);')
    new = ('    ksu_dispatcher_nr = ni_slot;\n'
           '    if (ksu_syscall_table_hook(ksu_dispatcher_nr,\n'
           '                               (syscall_fn_t)ksu_syscall_dispatcher, NULL)) {\n'
           '        pr_warn("dispatcher unavailable at slot %d; syscall event hooks disabled\\n", ni_slot);\n'
           '        ksu_dispatcher_nr = -1;\n'
           '        return;\n'
           '    }\n'
           '    pr_info("dispatcher installed at slot %d\\n", ksu_dispatcher_nr);')
    check('syscall_hook.c-dispatcher', old, c)
    c = c.replace(old, new, 1)

    write_file(path, c)
    print("  patched: kernel/hook/arm64/syscall_hook.c")


def patch_syscall_hook_h(ksu_dir):
    path = os.path.join(ksu_dir, 'kernel', 'hook', 'syscall_hook.h')
    c = read_file(path)
    old = 'void ksu_syscall_table_hook(int nr, syscall_fn_t fn, syscall_fn_t *old);'
    new = 'int ksu_syscall_table_hook(int nr, syscall_fn_t fn, syscall_fn_t *old);'
    check('syscall_hook.h', old, c)
    c = c.replace(old, new, 1)
    write_file(path, c)
    print("  patched: kernel/hook/syscall_hook.h")


# Large block added before #ifdef CONFIG_KRETPROBES in syscall_hook_manager.c
_SAMSUNG_RKP_BLOCK = r"""
static bool syscall_hook_manager_initialized;

#if defined(CONFIG_KSU_SAMSUNG_RKP) && defined(CONFIG_KRETPROBES) && defined(__aarch64__)
#include "feature/adb_root.h"
#include "selinux/selinux.h"

struct ksu_setresuid_task_work {
    struct callback_head callback;
    uid_t old_uid;
    uid_t new_uid;
};

static bool setresuid_kretprobe_registered;
static bool samsung_sucompat_kprobes_registered;

#define SAMSUNG_SUCOMPAT_BYPASS_NR (-2)

static bool samsung_sucompat_should_redirect(int syscall_nr)
{
    struct pt_regs *syscall_regs = task_pt_regs(current);

    if (unlikely(syscall_regs->syscallno == SAMSUNG_SUCOMPAT_BYPASS_NR)) {
        syscall_regs->syscallno = syscall_nr;
        return false;
    }

    return ksu_su_compat_enabled &&
           ksu_is_allow_uid_for_current(current_uid().val);
}

static long __nocfi samsung_sucompat_execve(const struct pt_regs *regs)
{
    struct pt_regs *syscall_regs = (struct pt_regs *)regs;
    int syscall_nr = syscall_regs->syscallno;
    long ret;

    syscall_regs->syscallno = SAMSUNG_SUCOMPAT_BYPASS_NR;
    ret = ksu_hook_execve(__NR_execve, regs);
    syscall_regs->syscallno = syscall_nr;
    return ret;
}

static long __nocfi samsung_sucompat_execveat(const struct pt_regs *regs)
{
    struct pt_regs *syscall_regs = (struct pt_regs *)regs;
    int syscall_nr = syscall_regs->syscallno;
    long ret;

    syscall_regs->syscallno = SAMSUNG_SUCOMPAT_BYPASS_NR;
    ret = ksu_hook_execveat(__NR_execveat, regs);
    syscall_regs->syscallno = syscall_nr;
    return ret;
}

static long __nocfi samsung_adbroot_execve(const struct pt_regs *regs)
{
    long ret = ksu_adb_root_handle_execve((struct pt_regs *)regs);
    if (ret)
        pr_err("adb root failed: %ld\n", ret);
    return ksu_syscall_table[__NR_execve](regs);
}

static long __nocfi samsung_adbroot_execveat(const struct pt_regs *regs)
{
    long ret = ksu_adb_root_handle_execveat((struct pt_regs *)regs);
    if (ret)
        pr_err("adb root failed: %ld\n", ret);
    return ksu_syscall_table[__NR_execveat](regs);
}

static long __nocfi samsung_sucompat_newfstatat(const struct pt_regs *regs)
{
    struct pt_regs *syscall_regs = (struct pt_regs *)regs;
    int syscall_nr = syscall_regs->syscallno;
    long ret;

    syscall_regs->syscallno = SAMSUNG_SUCOMPAT_BYPASS_NR;
    ret = ksu_hook_newfstatat(__NR_newfstatat, regs);
    syscall_regs->syscallno = syscall_nr;
    return ret;
}

static long __nocfi samsung_sucompat_faccessat(const struct pt_regs *regs)
{
    struct pt_regs *syscall_regs = (struct pt_regs *)regs;
    int syscall_nr = syscall_regs->syscallno;
    long ret;

    syscall_regs->syscallno = SAMSUNG_SUCOMPAT_BYPASS_NR;
    ret = ksu_hook_faccessat(__NR_faccessat, regs);
    syscall_regs->syscallno = syscall_nr;
    return ret;
}

static long __nocfi samsung_sucompat_statx(const struct pt_regs *regs)
{
    struct pt_regs *syscall_regs = (struct pt_regs *)regs;
    int syscall_nr = syscall_regs->syscallno;
    long ret;

    syscall_regs->syscallno = SAMSUNG_SUCOMPAT_BYPASS_NR;
    ret = ksu_hook_newfstatat(__NR_statx, regs);
    syscall_regs->syscallno = syscall_nr;
    return ret;
}

static long __nocfi samsung_sucompat_faccessat2(const struct pt_regs *regs)
{
    struct pt_regs *syscall_regs = (struct pt_regs *)regs;
    int syscall_nr = syscall_regs->syscallno;
    long ret;

    syscall_regs->syscallno = SAMSUNG_SUCOMPAT_BYPASS_NR;
    ret = ksu_hook_faccessat(__NR_faccessat2, regs);
    syscall_regs->syscallno = syscall_nr;
    return ret;
}

static int samsung_sucompat_execve_pre_handler(struct kprobe *probe, struct pt_regs *regs)
{
    if (samsung_sucompat_should_redirect(__NR_execve)) {
        instruction_pointer_set(regs, (unsigned long)samsung_sucompat_execve);
        return 1;
    }

    if (current->pid != 1 && is_init(current_cred())) {
        instruction_pointer_set(regs, (unsigned long)samsung_adbroot_execve);
        return 1;
    }

    return 0;
}

static int samsung_sucompat_execveat_pre_handler(struct kprobe *probe, struct pt_regs *regs)
{
    if (samsung_sucompat_should_redirect(__NR_execveat)) {
        instruction_pointer_set(regs, (unsigned long)samsung_sucompat_execveat);
        return 1;
    }

    if (current->pid != 1 && is_init(current_cred())) {
        instruction_pointer_set(regs, (unsigned long)samsung_adbroot_execveat);
        return 1;
    }
    return 1;
}

static int samsung_sucompat_newfstatat_pre_handler(struct kprobe *probe, struct pt_regs *regs)
{
    if (!samsung_sucompat_should_redirect(__NR_newfstatat))
        return 0;

    instruction_pointer_set(regs, (unsigned long)samsung_sucompat_newfstatat);
    return 1;
}

static int samsung_sucompat_faccessat_pre_handler(struct kprobe *probe, struct pt_regs *regs)
{
    if (!samsung_sucompat_should_redirect(__NR_faccessat))
        return 0;

    instruction_pointer_set(regs, (unsigned long)samsung_sucompat_faccessat);
    return 1;
}

static int samsung_sucompat_statx_pre_handler(struct kprobe *probe, struct pt_regs *regs)
{
    if (!samsung_sucompat_should_redirect(__NR_statx))
        return 0;

    instruction_pointer_set(regs, (unsigned long)samsung_sucompat_statx);
    return 1;
}

static int samsung_sucompat_faccessat2_pre_handler(struct kprobe *probe, struct pt_regs *regs)
{
    if (!samsung_sucompat_should_redirect(__NR_faccessat2))
        return 0;

    instruction_pointer_set(regs, (unsigned long)samsung_sucompat_faccessat2);
    return 1;
}

static struct kprobe samsung_sucompat_execve_kprobe = {
    .pre_handler = samsung_sucompat_execve_pre_handler,
};

static struct kprobe samsung_sucompat_execveat_kprobe = {
    .pre_handler = samsung_sucompat_execveat_pre_handler,
};

static struct kprobe samsung_sucompat_newfstatat_kprobe = {
    .pre_handler = samsung_sucompat_newfstatat_pre_handler,
};

static struct kprobe samsung_sucompat_faccessat_kprobe = {
    .pre_handler = samsung_sucompat_faccessat_pre_handler,
};

static struct kprobe samsung_sucompat_statx_kprobe = {
    .pre_handler = samsung_sucompat_statx_pre_handler,
};

static struct kprobe samsung_sucompat_faccessat2_kprobe = {
    .pre_handler = samsung_sucompat_faccessat2_pre_handler,
};

static int samsung_sucompat_hook_init(void)
{
    int ret;

    if (!ksu_syscall_table)
        return -ENOENT;

    samsung_sucompat_execve_kprobe.addr =
        (kprobe_opcode_t *)READ_ONCE(ksu_syscall_table[__NR_execve]);
    samsung_sucompat_execveat_kprobe.addr =
        (kprobe_opcode_t *)READ_ONCE(ksu_syscall_table[__NR_execveat]);
    samsung_sucompat_newfstatat_kprobe.addr =
        (kprobe_opcode_t *)READ_ONCE(ksu_syscall_table[__NR_newfstatat]);
    samsung_sucompat_faccessat_kprobe.addr =
        (kprobe_opcode_t *)READ_ONCE(ksu_syscall_table[__NR_faccessat]);
    samsung_sucompat_statx_kprobe.addr =
        (kprobe_opcode_t *)READ_ONCE(ksu_syscall_table[__NR_statx]);
    samsung_sucompat_faccessat2_kprobe.addr =
        (kprobe_opcode_t *)READ_ONCE(ksu_syscall_table[__NR_faccessat2]);

    ksu_sucompat_init();

    ret = register_kprobe(&samsung_sucompat_execve_kprobe);
    if (ret)
        goto exit_sucompat;

    ret = register_kprobe(&samsung_sucompat_execveat_kprobe);
    if (ret)
        goto unregister_execve;

    ret = register_kprobe(&samsung_sucompat_newfstatat_kprobe);
    if (ret)
        goto unregister_execveat;

    ret = register_kprobe(&samsung_sucompat_faccessat_kprobe);
    if (ret)
        goto unregister_newfstatat;

    ret = register_kprobe(&samsung_sucompat_statx_kprobe);
    if (ret)
        goto unregister_faccessat;

    ret = register_kprobe(&samsung_sucompat_faccessat2_kprobe);
    if (ret)
        goto unregister_statx;

    samsung_sucompat_kprobes_registered = true;
    pr_info("hook_manager: Samsung sucompat kprobes registered\n");
    return 0;

unregister_statx:
    unregister_kprobe(&samsung_sucompat_statx_kprobe);
unregister_faccessat:
    unregister_kprobe(&samsung_sucompat_faccessat_kprobe);
unregister_newfstatat:
    unregister_kprobe(&samsung_sucompat_newfstatat_kprobe);
unregister_execveat:
    unregister_kprobe(&samsung_sucompat_execveat_kprobe);
unregister_execve:
    unregister_kprobe(&samsung_sucompat_execve_kprobe);
exit_sucompat:
    ksu_sucompat_exit();
    return ret;
}

static void samsung_sucompat_hook_exit(void)
{
    if (!samsung_sucompat_kprobes_registered)
        return;

    unregister_kprobe(&samsung_sucompat_faccessat2_kprobe);
    unregister_kprobe(&samsung_sucompat_statx_kprobe);
    unregister_kprobe(&samsung_sucompat_faccessat_kprobe);
    unregister_kprobe(&samsung_sucompat_newfstatat_kprobe);
    unregister_kprobe(&samsung_sucompat_execveat_kprobe);
    unregister_kprobe(&samsung_sucompat_execve_kprobe);
    samsung_sucompat_kprobes_registered = false;
    ksu_sucompat_exit();
}

static void setresuid_task_work_func(struct callback_head *callback)
{
    struct ksu_setresuid_task_work *work = container_of(callback, struct ksu_setresuid_task_work, callback);

    ksu_handle_setresuid(work->old_uid, work->new_uid);
    kfree(work);
}

static int setresuid_entry_handler(struct kretprobe_instance *ri, struct pt_regs *regs)
{
    *(uid_t *)ri->data = current_uid().val;
    return 0;
}

static int setresuid_return_handler(struct kretprobe_instance *ri, struct pt_regs *regs)
{
    struct ksu_setresuid_task_work *work;
    uid_t old_uid = *(uid_t *)ri->data;
    uid_t new_uid;

    if (regs_return_value(regs) < 0)
        return 0;

    new_uid = current_uid().val;
    if (old_uid == new_uid)
        return 0;

    work = kzalloc(sizeof(*work), GFP_ATOMIC);
    if (!work)
        return 0;

    work->old_uid = old_uid;
    work->new_uid = new_uid;
    work->callback.func = setresuid_task_work_func;

    if (task_work_add(current, &work->callback, TWA_RESUME))
        kfree(work);

    return 0;
}

static struct kretprobe setresuid_kretprobe = {
    .kp.symbol_name = "__arm64_sys_setresuid",
    .entry_handler = setresuid_entry_handler,
    .handler = setresuid_return_handler,
    .data_size = sizeof(uid_t),
};

static int samsung_setresuid_hook_init(void)
{
    int ret = register_kretprobe(&setresuid_kretprobe);

    if (ret) {
        pr_err("hook_manager: Samsung setresuid kretprobe failed: %d\n", ret);
        return ret;
    }

    setresuid_kretprobe_registered = true;
    ksu_setuid_hook_init();
    pr_info("hook_manager: Samsung setresuid kretprobe registered\n");
    return 0;
}

static void samsung_setresuid_hook_exit(void)
{
    if (!setresuid_kretprobe_registered)
        return;

    unregister_kretprobe(&setresuid_kretprobe);
    setresuid_kretprobe_registered = false;
    ksu_setuid_hook_exit();
}
#endif

"""


def patch_hook_manager(ksu_dir):
    path = os.path.join(ksu_dir, 'kernel', 'hook', 'syscall_hook_manager.c')
    c = read_file(path)

    # 1. Add <linux/task_work.h> include
    old = '#include <linux/slab.h>\n#include <trace/events/syscalls.h>'
    new = '#include <linux/slab.h>\n#include <linux/task_work.h>\n#include <trace/events/syscalls.h>'
    check('hook_manager-task_work', old, c)
    c = c.replace(old, new, 1)

    # 2. Add allowlist include
    old = '#include "hook/syscall_event_bridge.h"\n'
    new = '#include "hook/syscall_event_bridge.h"\n#include "policy/allowlist.h"\n'
    check('hook_manager-allowlist', old, c)
    c = c.replace(old, new, 1)

    # 3. Insert Samsung RKP block before #ifdef CONFIG_KRETPROBES
    old = '#ifdef CONFIG_KRETPROBES\n\nstatic struct kretprobe *init_kretprobe'
    new = _SAMSUNG_RKP_BLOCK + '#ifdef CONFIG_KRETPROBES\n\nstatic struct kretprobe *init_kretprobe'
    check('hook_manager-rkp-block', old, c)
    c = c.replace(old, new, 1)

    # 4. Add Samsung RKP early-return path at start of manager_init
    old = ('void __init ksu_syscall_hook_manager_init(void)\n'
           '{\n'
           '    int ret;\n'
           '    pr_info("hook_manager: ksu_hook_manager_init called\\n");\n'
           '\n'
           '#ifdef CONFIG_KRETPROBES\n')
    new = ('void __init ksu_syscall_hook_manager_init(void)\n'
           '{\n'
           '    int ret;\n'
           '    pr_info("hook_manager: ksu_hook_manager_init called\\n");\n'
           '\n'
           '    if (ksu_dispatcher_nr < 0) {\n'
           '        pr_warn("hook_manager: dispatcher unavailable; syscall event hooks disabled\\n");\n'
           '#if defined(CONFIG_KSU_SAMSUNG_RKP) && defined(CONFIG_KRETPROBES) && defined(__aarch64__)\n'
           '        samsung_setresuid_hook_init();\n'
           '        ret = samsung_sucompat_hook_init();\n'
           '        if (ret)\n'
           '            pr_err("hook_manager: Samsung sucompat hook init failed: %d\\n", ret);\n'
           '#endif\n'
           '        return;\n'
           '    }\n'
           '\n'
           '    syscall_hook_manager_initialized = true;\n'
           '\n'
           '#ifdef CONFIG_KRETPROBES\n')
    check('hook_manager-init-early-return', old, c)
    c = c.replace(old, new, 1)

    # 5. Add Samsung cleanup path at start of manager_exit
    old = ('void __exit ksu_syscall_hook_manager_exit(void)\n'
           '{\n'
           '    pr_info("hook_manager: ksu_hook_manager_exit called\\n");\n'
           '#ifdef CONFIG_HAVE_SYSCALL_TRACEPOINTS\n')
    new = ('void __exit ksu_syscall_hook_manager_exit(void)\n'
           '{\n'
           '    pr_info("hook_manager: ksu_hook_manager_exit called\\n");\n'
           '\n'
           '    if (!syscall_hook_manager_initialized) {\n'
           '#if defined(CONFIG_KSU_SAMSUNG_RKP) && defined(CONFIG_KRETPROBES) && defined(__aarch64__)\n'
           '        samsung_sucompat_hook_exit();\n'
           '        samsung_setresuid_hook_exit();\n'
           '#endif\n'
           '        ksu_syscall_hook_exit();\n'
           '        return;\n'
           '    }\n'
           '#ifdef CONFIG_HAVE_SYSCALL_TRACEPOINTS\n')
    check('hook_manager-exit-early-return', old, c)
    c = c.replace(old, new, 1)

    write_file(path, c)
    print("  patched: kernel/hook/syscall_hook_manager.c")


def patch_tp_marker(ksu_dir):
    path = os.path.join(ksu_dir, 'kernel', 'hook', 'tp_marker.c')
    c = read_file(path)

    # Add samsung kdp header after allowlist include
    old = '#include "policy/allowlist.h"\n#include "klog.h"'
    new = '#include "policy/allowlist.h"\n#include "ksu_samsung_kdp.h"\n#include "klog.h"'
    check('tp_marker-include', old, c)
    c = c.replace(old, new, 1)

    # Change put_cred to ksu_put_cred
    old = '        put_cred(cred);\n'
    new = '        ksu_put_cred(cred);\n'
    check('tp_marker-put_cred', old, c)
    c = c.replace(old, new, 1)

    write_file(path, c)
    print("  patched: kernel/hook/tp_marker.c")


def patch_sucompat(ksu_dir):
    path = os.path.join(ksu_dir, 'kernel', 'feature', 'sucompat.c')
    c = read_file(path)
    old = 'void __exit ksu_sucompat_exit()'
    new = 'void ksu_sucompat_exit()'
    check('sucompat-exit', old, c)
    c = c.replace(old, new, 1)
    write_file(path, c)
    print("  patched: kernel/feature/sucompat.c")


def patch_patch_memory(ksu_dir):
    # No longer patching ksu_patch_text() itself — the Samsung NO_PATCH_TEXT
    # guard now lives only in patch_syscall_table() so that selinux_hide can
    # still use ksu_patch_text() on normal (non-RKP-protected) data pages.
    pass


def patch_selinux_hide_c(ksu_dir):
    path = os.path.join(ksu_dir, 'kernel', 'feature', 'selinux_hide.c')
    c = read_file(path)

    # Add ksu_samsung_kdp.h include
    old = '#include "selinux_hide.h"\n'
    new = '#include "selinux_hide.h"\n#include "ksu_samsung_kdp.h"\n'
    check('selinux_hide-include', old, c)
    c = c.replace(old, new, 1)

    # Replace commit_creds(new) in ksu_setprocattr with KDP-aware version.
    # If the calling process holds a KDP-protected credential (because KSU
    # previously granted it root via ksu_samsung_kdp_commit_creds), the normal
    # commit_creds() will call put_cred() on the KDP-read-only old credential
    # and trigger a Samsung EL2 abort.  Routing through ksu_samsung_kdp_commit_creds
    # handles both the KDP case (CONFIG_KSU_SAMSUNG_KDP=y) and the normal case
    # (falls back to commit_creds() when KDP is disabled).
    old = ('    tsec->sid = sid;\n'
           '\n'
           '    commit_creds(new);\n'
           '    return size;\n'
           '\n'
           'abort_change:\n'
           '    abort_creds(new);\n'
           '    return error;\n')
    new = ('    tsec->sid = sid;\n'
           '\n'
           '    error = ksu_samsung_kdp_commit_creds(new);\n'
           '    if (error)\n'
           '        goto abort_change;\n'
           '    return size;\n'
           '\n'
           'abort_change:\n'
           '    abort_creds(new);\n'
           '    return error;\n')
    check('selinux_hide-commit_creds', old, c)
    c = c.replace(old, new, 1)

    # --- Samsung RKP: selinux_hide patches SELinux rodata (write_op[] table and
    #     sel_handle_status_ops.open) via ksu_patch_text(), which faults with -EFAULT under
    #     Samsung RKP (the pages are protected read-only at EL2 by physical address, so the
    #     fixmap alias ksu_patch_text() uses faults too).  Install kprobes on the target
    #     functions instead and redirect only app-UID callers (>= 10000) to the existing my_*
    #     replacements, whose app-UID paths never call the original -> no re-entrancy.  System
    #     UIDs fall through to the original function unchanged.

    # 1. Kprobe infrastructure + per-hook pre-handlers, injected before the forward decls.
    old = ('static void hook_selinux_status_open();\n'
           'static void ksu_selinux_hide_unhook();\n')
    new = (r'''#ifdef CONFIG_KSU_SAMSUNG_RKP
#include <linux/kprobes.h>
#include <linux/ptrace.h>

static struct kprobe samsung_sel_ctx_kp;
static struct kprobe samsung_sel_acc_kp;
static struct kprobe samsung_sel_setproc_kp;
static struct kprobe samsung_sel_status_kp;

static int samsung_sel_ctx_pre(struct kprobe *p, struct pt_regs *regs)
{
    if (current_uid().val < 10000)
        return 0;
    regs->pc = (unsigned long)my_write_context;
    return 1;
}

static int samsung_sel_acc_pre(struct kprobe *p, struct pt_regs *regs)
{
    if (current_uid().val < 10000)
        return 0;
    regs->pc = (unsigned long)my_write_access;
    return 1;
}

static int samsung_sel_setproc_pre(struct kprobe *p, struct pt_regs *regs)
{
    const char *name = (const char *)regs->regs[0];

    if (current_uid().val < 10000)
        return 0;
    /* my_setprocattr only replaces the "current" attr for apps; every other name falls through
     * to the original via its call_orig label, which would re-enter this kprobe.  Gate on
     * name == "current" here so the redirect never reaches that path. */
    if (!name || strcmp(name, "current"))
        return 0;
    regs->pc = (unsigned long)my_setprocattr;
    return 1;
}

static int samsung_sel_status_pre(struct kprobe *p, struct pt_regs *regs)
{
    /* Only redirect when a fake status page exists; otherwise my_sel_open_handle_status would
     * call the (kprobed) original and re-enter. */
    if (current_uid().val < 10000 || !ksu_selinux_hide_enabled || !READ_ONCE(fake_status))
        return 0;
    regs->pc = (unsigned long)my_sel_open_handle_status;
    return 1;
}
#endif

static void hook_selinux_status_open();
static void ksu_selinux_hide_unhook();
''')
    check('selinux_hide-kprobe-infra', old, c)
    c = c.replace(old, new, 1)

    # 2. hook_selinux_status_open(): kprobe instead of patching the file_operations.open slot.
    old = ('    sel_open_handle_status_fn new_fn = my_sel_open_handle_status;\n'
           '    orig_sel_open_handle_status = *sel_open_handle_status_slot;\n'
           '    int ret = ksu_patch_text(sel_open_handle_status_slot, &new_fn, sizeof(new_fn), KSU_PATCH_TEXT_FLUSH_DCACHE);\n'
           '    if (ret) {\n'
           '        pr_err("selinux_hide: init: patch_text sel_open_handle_status err: %d\\n", ret);\n'
           '        sel_open_handle_status_slot = NULL;\n'
           '        orig_sel_open_handle_status = NULL;\n'
           '    }\n')
    new = (r'''    orig_sel_open_handle_status = *sel_open_handle_status_slot;
#ifdef CONFIG_KSU_SAMSUNG_RKP
    memset(&samsung_sel_status_kp, 0, sizeof(samsung_sel_status_kp));
    samsung_sel_status_kp.addr = (kprobe_opcode_t *)orig_sel_open_handle_status;
    samsung_sel_status_kp.pre_handler = samsung_sel_status_pre;
    int ret = register_kprobe(&samsung_sel_status_kp);
    if (ret) {
        pr_err("selinux_hide: samsung: kprobe sel_open_handle_status err: %d\n", ret);
        samsung_sel_status_kp.addr = NULL;
        sel_open_handle_status_slot = NULL;
        orig_sel_open_handle_status = NULL;
    }
#else
    sel_open_handle_status_fn new_fn = my_sel_open_handle_status;
    int ret = ksu_patch_text(sel_open_handle_status_slot, &new_fn, sizeof(new_fn), KSU_PATCH_TEXT_FLUSH_DCACHE);
    if (ret) {
        pr_err("selinux_hide: init: patch_text sel_open_handle_status err: %d\n", ret);
        sel_open_handle_status_slot = NULL;
        orig_sel_open_handle_status = NULL;
    }
#endif
''')
    check('selinux_hide-status-kprobe', old, c)
    c = c.replace(old, new, 1)

    # 3. ksu_selinux_hide_enable(): kprobes for context_write / access_write / setprocattr.
    old = ('    context_write = &selinux_write_op[SEL_CONTEXT];\n'
           '    pr_info("selinux_hide: context_write: 0x%lx [%pSb]\\n", (unsigned long)*context_write, *context_write);\n'
           '    write_op_fn my = my_write_context;\n'
           '    orig_context_write = *context_write;\n'
           '    ret = ksu_patch_text(context_write, &my, sizeof(my), KSU_PATCH_TEXT_FLUSH_DCACHE);\n'
           '    if (ret) {\n'
           '        pr_err("selinux_hide: init: patch_text context_write err: %d\\n", ret);\n'
           '        goto unhook;\n'
           '    }\n'
           '\n'
           '    access_write = &selinux_write_op[SEL_ACCESS];\n'
           '    pr_info("selinux_hide: access_write: 0x%lx [%pSb]\\n", (unsigned long)*access_write, *access_write);\n'
           '    my = my_write_access;\n'
           '    orig_access_write = *access_write;\n'
           '    ret = ksu_patch_text(access_write, &my, sizeof(my), KSU_PATCH_TEXT_FLUSH_DCACHE);\n'
           '    if (ret) {\n'
           '        pr_err("selinux_hide: init: patch_text access_write err: %d\\n", ret);\n'
           '        goto unhook;\n'
           '    }\n'
           '\n'
           '    ret = ksu_lsm_hook(&selinux_setprocattr_hook);\n'
           '    if (ret) {\n'
           '        pr_err("selinux_hide: init: selinux_setprocattr_hook err: %d\\n", ret);\n'
           '        goto unhook;\n'
           '    }\n'
           '\n'
           '    return 0;\n')
    new = (r'''    context_write = &selinux_write_op[SEL_CONTEXT];
    access_write = &selinux_write_op[SEL_ACCESS];
    orig_context_write = *context_write;
    orig_access_write = *access_write;
#ifdef CONFIG_KSU_SAMSUNG_RKP
    memset(&samsung_sel_ctx_kp, 0, sizeof(samsung_sel_ctx_kp));
    samsung_sel_ctx_kp.addr = (kprobe_opcode_t *)orig_context_write;
    samsung_sel_ctx_kp.pre_handler = samsung_sel_ctx_pre;
    ret = register_kprobe(&samsung_sel_ctx_kp);
    if (ret) {
        pr_err("selinux_hide: samsung: kprobe context_write err: %d\n", ret);
        samsung_sel_ctx_kp.addr = NULL;
        goto unhook;
    }

    memset(&samsung_sel_acc_kp, 0, sizeof(samsung_sel_acc_kp));
    samsung_sel_acc_kp.addr = (kprobe_opcode_t *)orig_access_write;
    samsung_sel_acc_kp.pre_handler = samsung_sel_acc_pre;
    ret = register_kprobe(&samsung_sel_acc_kp);
    if (ret) {
        pr_err("selinux_hide: samsung: kprobe access_write err: %d\n", ret);
        samsung_sel_acc_kp.addr = NULL;
        goto unhook;
    }

    memset(&samsung_sel_setproc_kp, 0, sizeof(samsung_sel_setproc_kp));
    samsung_sel_setproc_kp.addr = (kprobe_opcode_t *)find_kernel_symbol_exact("selinux_setprocattr");
    if (!samsung_sel_setproc_kp.addr) {
        pr_err("selinux_hide: samsung: selinux_setprocattr not found\n");
        ret = -ENOENT;
        goto unhook;
    }
    samsung_sel_setproc_kp.pre_handler = samsung_sel_setproc_pre;
    ret = register_kprobe(&samsung_sel_setproc_kp);
    if (ret) {
        pr_err("selinux_hide: samsung: kprobe selinux_setprocattr err: %d\n", ret);
        samsung_sel_setproc_kp.addr = NULL;
        goto unhook;
    }

    return 0;
#else
    {
    write_op_fn my = my_write_context;
    ret = ksu_patch_text(context_write, &my, sizeof(my), KSU_PATCH_TEXT_FLUSH_DCACHE);
    if (ret) {
        pr_err("selinux_hide: init: patch_text context_write err: %d\n", ret);
        goto unhook;
    }

    my = my_write_access;
    ret = ksu_patch_text(access_write, &my, sizeof(my), KSU_PATCH_TEXT_FLUSH_DCACHE);
    if (ret) {
        pr_err("selinux_hide: init: patch_text access_write err: %d\n", ret);
        goto unhook;
    }
    }

    ret = ksu_lsm_hook(&selinux_setprocattr_hook);
    if (ret) {
        pr_err("selinux_hide: init: selinux_setprocattr_hook err: %d\n", ret);
        goto unhook;
    }

    return 0;
#endif
''')
    check('selinux_hide-enable-kprobe', old, c)
    c = c.replace(old, new, 1)

    # 4. ksu_selinux_hide_unhook(): unregister the kprobes instead of restoring slots.
    old = ('static void ksu_selinux_hide_unhook()\n'
           '{\n'
           '    int ret;\n'
           '    if (orig_context_write) {\n'
           '        ret =\n'
           '            ksu_patch_text(context_write, &orig_context_write, sizeof(orig_context_write), KSU_PATCH_TEXT_FLUSH_DCACHE);\n'
           '        orig_context_write = NULL;\n'
           '        if (ret) {\n'
           '            pr_err("selinux_hide: exit: patch_text context_write err: %d\\n", ret);\n'
           '        }\n'
           '    }\n'
           '    if (orig_access_write) {\n'
           '        ret = ksu_patch_text(access_write, &orig_access_write, sizeof(orig_access_write), KSU_PATCH_TEXT_FLUSH_DCACHE);\n'
           '        orig_access_write = NULL;\n'
           '        if (ret) {\n'
           '            pr_err("selinux_hide: exit: patch_text access_write err: %d\\n", ret);\n'
           '        }\n'
           '    }\n'
           '    if (sel_open_handle_status_slot && orig_sel_open_handle_status) {\n'
           '        ret = ksu_patch_text(sel_open_handle_status_slot, &orig_sel_open_handle_status,\n'
           '                             sizeof(orig_sel_open_handle_status), KSU_PATCH_TEXT_FLUSH_DCACHE);\n'
           '        orig_sel_open_handle_status = NULL;\n'
           '        if (ret) {\n'
           '            pr_err("selinux_hide: exit: patch_text sel_open_handle_status err: %d\\n", ret);\n'
           '        }\n'
           '    }\n'
           '    ksu_lsm_unhook(&selinux_setprocattr_hook);\n'
           '}\n')
    new = (r'''static void ksu_selinux_hide_unhook()
{
    int ret;
#ifdef CONFIG_KSU_SAMSUNG_RKP
    (void)ret;
    if (samsung_sel_ctx_kp.addr) {
        unregister_kprobe(&samsung_sel_ctx_kp);
        samsung_sel_ctx_kp.addr = NULL;
    }
    if (samsung_sel_acc_kp.addr) {
        unregister_kprobe(&samsung_sel_acc_kp);
        samsung_sel_acc_kp.addr = NULL;
    }
    if (samsung_sel_setproc_kp.addr) {
        unregister_kprobe(&samsung_sel_setproc_kp);
        samsung_sel_setproc_kp.addr = NULL;
    }
    if (samsung_sel_status_kp.addr) {
        unregister_kprobe(&samsung_sel_status_kp);
        samsung_sel_status_kp.addr = NULL;
    }
    orig_context_write = NULL;
    orig_access_write = NULL;
    orig_sel_open_handle_status = NULL;
#else
    if (orig_context_write) {
        ret =
            ksu_patch_text(context_write, &orig_context_write, sizeof(orig_context_write), KSU_PATCH_TEXT_FLUSH_DCACHE);
        orig_context_write = NULL;
        if (ret) {
            pr_err("selinux_hide: exit: patch_text context_write err: %d\n", ret);
        }
    }
    if (orig_access_write) {
        ret = ksu_patch_text(access_write, &orig_access_write, sizeof(orig_access_write), KSU_PATCH_TEXT_FLUSH_DCACHE);
        orig_access_write = NULL;
        if (ret) {
            pr_err("selinux_hide: exit: patch_text access_write err: %d\n", ret);
        }
    }
    if (sel_open_handle_status_slot && orig_sel_open_handle_status) {
        ret = ksu_patch_text(sel_open_handle_status_slot, &orig_sel_open_handle_status,
                             sizeof(orig_sel_open_handle_status), KSU_PATCH_TEXT_FLUSH_DCACHE);
        orig_sel_open_handle_status = NULL;
        if (ret) {
            pr_err("selinux_hide: exit: patch_text sel_open_handle_status err: %d\n", ret);
        }
    }
    ksu_lsm_unhook(&selinux_setprocattr_hook);
#endif
}
''')
    check('selinux_hide-unhook-kprobe', old, c)
    c = c.replace(old, new, 1)

    write_file(path, c)
    print("  patched: kernel/feature/selinux_hide.c")


def patch_susfs_makefile(ksu_dir):
    path = os.path.join(ksu_dir, 'kernel', 'susfs', 'Makefile')
    c = read_file(path)

    # The susfs guard LKM is built from its own Makefile (not the main kernelsu.ko Kbuild),
    # so the -DCONFIG_KSU_SAMSUNG_RKP define from patch_kbuild() does not reach it.  Add it
    # here so the guard's lsm_hook.c kprobe path compiles in.
    old = 'ccflags-y += -Wno-strict-prototypes -Wno-int-conversion -Wno-gcc-compat -Wno-missing-prototypes\n'
    new = old + 'ccflags-y += -DCONFIG_KSU_SAMSUNG_RKP=1\n'
    check('susfs-makefile-rkp', old, c)
    c = c.replace(old, new, 1)

    write_file(path, c)
    print("  patched: kernel/susfs/Makefile")


def patch_susfs_lsm_hook(ksu_dir):
    path = os.path.join(ksu_dir, 'kernel', 'susfs', 'lsm_hook.c')
    c = read_file(path)

    # The guard's <6.12 LSM "insert" places its node at the head of a security_hook_heads
    # hlist via ksu_patch_text().  That memory is __lsm_ro_after_init, which Samsung RKP
    # protects read-only at EL2, so the write faults with -EFAULT and the whole guard LKM
    # refuses to load (sus_path getattr hook fails -> "nothing would be hidden").  Install a
    # kprobe on selinux_<member> instead and run the replacement from the pre-handler: a
    # non-zero return (hide, e.g. -ENOENT) is handed back and SELinux's hook skipped; a zero
    # return lets SELinux run - exactly the "<6.12 insert at head, return 0 to pass through"
    # semantics.  On <6.12 the replacement never calls the original, so there is no re-entrancy.

    # 1. Samsung kprobe infrastructure, injected after the includes.
    old = '#include "susfs_log.h"\n'
    new = (old + r'''
#if defined(CONFIG_KSU_SAMSUNG_RKP) && LINUX_VERSION_CODE < KERNEL_VERSION(6, 12, 0)
#include <linux/kprobes.h>
#include <linux/ptrace.h>
#include "susfs_kprobe.h"

struct ksu_samsung_lsm_slot {
    struct kprobe kp;
    void *replacement;
    struct ksu_lsm_hook *hook;
    bool used;
};

static struct ksu_samsung_lsm_slot ksu_samsung_lsm_slots[16];

typedef long (*ksu_samsung_lsm_fn)(long, long, long, long, long);

static long __nocfi ksu_samsung_lsm_invoke(void *fn, struct pt_regs *regs)
{
    ksu_samsung_lsm_fn f = (ksu_samsung_lsm_fn)fn;
    return f(regs->regs[0], regs->regs[1], regs->regs[2], regs->regs[3], regs->regs[4]);
}

static int ksu_samsung_lsm_pre(struct kprobe *p, struct pt_regs *regs)
{
    struct ksu_samsung_lsm_slot *slot = container_of(p, struct ksu_samsung_lsm_slot, kp);
    long ret = ksu_samsung_lsm_invoke(slot->replacement, regs);

    if (ret) {
        regs->regs[0] = (unsigned long)ret;
        regs->pc = regs->regs[30];
        return 1;
    }
    return 0;
}

static int ksu_samsung_lsm_arm(struct ksu_lsm_hook *hook)
{
    char want[64];
    unsigned long addr;
    struct ksu_samsung_lsm_slot *slot = NULL;
    int i, ret;

    snprintf(want, sizeof(want), "selinux_%s", hook->head_name);
    addr = find_kernel_symbol_exact(want);
    if (!addr) {
        pr_err("lsm_hook: samsung: %s not found\n", want);
        return -ENOENT;
    }

    for (i = 0; i < ARRAY_SIZE(ksu_samsung_lsm_slots); i++) {
        if (!ksu_samsung_lsm_slots[i].used) {
            slot = &ksu_samsung_lsm_slots[i];
            break;
        }
    }
    if (!slot)
        return -ENOSPC;

    memset(&slot->kp, 0, sizeof(slot->kp));
    slot->kp.addr = (kprobe_opcode_t *)addr;
    slot->kp.pre_handler = ksu_samsung_lsm_pre;
    slot->replacement = hook->replacement;
    slot->hook = hook;

    ret = register_kprobe(&slot->kp);
    if (ret) {
        susfs_kp_forget_addr(&slot->kp);
        pr_err("lsm_hook: samsung: register_kprobe(%s) = %d\n", want, ret);
        return ret;
    }

    slot->used = true;
    /* non-NULL sentinel: callers test hook->entry to know the hook is armed */
    hook->entry = (struct security_hook_list *)slot;
    SUSFS_LOGI("lsm_hook: samsung kprobe armed on %s @ %lx\n", want, addr);
    return 0;
}

static bool ksu_samsung_lsm_disarm(struct ksu_lsm_hook *hook)
{
    int i;

    for (i = 0; i < ARRAY_SIZE(ksu_samsung_lsm_slots); i++) {
        struct ksu_samsung_lsm_slot *slot = &ksu_samsung_lsm_slots[i];

        if (slot->used && slot->hook == hook) {
            unregister_kprobe(&slot->kp);
            susfs_kp_forget_addr(&slot->kp);
            slot->used = false;
            slot->hook = NULL;
            slot->replacement = NULL;
            return true;
        }
    }
    return false;
}
#endif /* CONFIG_KSU_SAMSUNG_RKP && < 6.12 */
''')
    check('susfs-lsm-infra', old, c)
    c = c.replace(old, new, 1)

    # 2. ksu_lsm_hook(): take the kprobe path for the <6.12 insert branch.
    old = ('#else\n'
           '        heads_addr = find_kernel_symbol_exact("security_hook_heads");\n')
    new = ('#else\n'
           '#ifdef CONFIG_KSU_SAMSUNG_RKP\n'
           '        ret = ksu_lsm_hook_track(hook);\n'
           '        if (ret) {\n'
           '            pr_err("lsm_hook: too many hooks to track: %d\\n", ret);\n'
           '            goto out_unlock;\n'
           '        }\n'
           '        ret = ksu_samsung_lsm_arm(hook);\n'
           '        if (ret)\n'
           '            ksu_lsm_hook_untrack(hook);\n'
           '        goto out_unlock;\n'
           '#endif\n'
           '        heads_addr = find_kernel_symbol_exact("security_hook_heads");\n')
    check('susfs-lsm-arm', old, c)
    c = c.replace(old, new, 1)

    # 3. ksu_lsm_unhook(): disarm the kprobe for the <6.12 branch.
    old = ('#else\n'
           '    if (!hook->entry) {\n'
           '        mutex_unlock(&ksu_lsm_hook_lock);\n'
           '        return;\n'
           '    }\n'
           '\n'
           '    if (hook->entry == &hook->list) {\n')
    new = ('#else\n'
           '#ifdef CONFIG_KSU_SAMSUNG_RKP\n'
           '    if (ksu_samsung_lsm_disarm(hook)) {\n'
           '        ksu_lsm_hook_untrack(hook);\n'
           '        hook->entry = NULL;\n'
           '        mutex_unlock(&ksu_lsm_hook_lock);\n'
           '        ksu_lsm_hook_drain();\n'
           '        return;\n'
           '    }\n'
           '#endif\n'
           '    if (!hook->entry) {\n'
           '        mutex_unlock(&ksu_lsm_hook_lock);\n'
           '        return;\n'
           '    }\n'
           '\n'
           '    if (hook->entry == &hook->list) {\n')
    check('susfs-lsm-disarm', old, c)
    c = c.replace(old, new, 1)

    write_file(path, c)
    print("  patched: kernel/susfs/lsm_hook.c")


def patch_app_profile(ksu_dir):
    path = os.path.join(ksu_dir, 'kernel', 'policy', 'app_profile.c')
    c = read_file(path)

    # Add includes
    old = '#include "hook/tp_marker.h"\n'
    new = ('#include "hook/tp_marker.h"\n'
           '#include "compat/samsung_defex.h"\n'
           '#include "ksu_samsung_kdp.h"\n')
    check('app_profile-includes', old, c)
    c = c.replace(old, new, 1)

    # Replace commit_creds in escape_with_root_profile
    old = ('    commit_creds(cred);\n'
           '\n'
           '    disable_seccomp();')
    new = ('    ret = ksu_samsung_kdp_commit_creds(cred);\n'
           '    if (ret) {\n'
           '        pr_err("Samsung KDP credential install failed: %d\\n", ret);\n'
           '        goto out_abort_creds;\n'
           '    }\n'
           '    ksu_samsung_defex_sync_current();\n'
           '\n'
           '    disable_seccomp();')
    check('app_profile-commit_creds', old, c)
    c = c.replace(old, new, 1)

    # Add defex_sync_current in escape_to_root_for_init
    old = ('    setup_selinux(KERNEL_SU_CONTEXT, cred);\n'
           '    commit_creds(cred);\n'
           '}')
    new = ('    setup_selinux(KERNEL_SU_CONTEXT, cred);\n'
           '    commit_creds(cred);\n'
           '    ksu_samsung_defex_sync_current();\n'
           '}')
    check('app_profile-init-defex', old, c)
    c = c.replace(old, new, 1)

    write_file(path, c)
    print("  patched: kernel/policy/app_profile.c")


def patch_late_load_rs(ksu_dir):
    path = os.path.join(ksu_dir, 'userspace', 'ksud', 'src', 'late_load.rs')
    c = read_file(path)

    # Keep std::process::Command (used for adbd restart) but verify import exists
    old = 'use std::{process::Command, time::Instant};\n'
    check('late_load-import', old, c)

    # Change function signature: remove daemonize, rename package_name to _
    old = ('pub fn run(package_name: &String, kmi: Option<String>, allow_shell: bool) -> Result<()> {\n'
           '    utils::daemonize(false)?;\n'
           '    info!("late-load command triggered!");\n'
           '    dump_process_info("late-load start");\n'
           '\n'
           '    // 1. Check if KernelSU is already loaded\n')
    new = ('pub fn run(_package_name: &String, kmi: Option<String>, allow_shell: bool) -> Result<()> {\n'
           '    info!("late-load command triggered!");\n'
           '    dump_process_info("late-load start");\n'
           '\n'
           '    // Copy the daemon before loading the module changes this process\'s\n'
           '    // security context. The remaining install steps require KernelSU policy.\n'
           '    utils::stage_daemon_from("/data/local/tmp/.ksud-stage").context("Failed to stage ksud")?;\n'
           '\n'
           '    // 1. Check if KernelSU is already loaded\n')
    check('late_load-run-fn', old, c)
    c = c.replace(old, new, 1)

    # Change install to finish_install
    old = '    utils::install(None).context("Failed to install ksud")?;\n'
    new = '    utils::finish_install(None).context("Failed to finish ksud installation")?;\n'
    check('late_load-install', old, c)
    c = c.replace(old, new, 1)

    # Load susfs guard LKM during late-load itself, before the Manager gets a
    # chance to run its own ksud (which is the stock unpatched binary and would
    # load an unpatched guard that fails with -14 on Samsung RKP).
    old = ('    // 7. Initialize features\n'
           '    crate::feature::init_forced_features();\n'
           '    if let Err(e) = crate::feature::init_features() {\n'
           '        warn!("init features failed: {e}");\n'
           '    }\n'
           '\n'
           '    // 8. Execute late-load stage scripts')
    new = ('    // 7. Initialize features\n'
           '    crate::feature::init_forced_features();\n'
           '    if let Err(e) = crate::feature::init_features() {\n'
           '        warn!("init features failed: {e}");\n'
           '    }\n'
           '\n'
           '    // 7b. Load susfs guard LKM now so the Manager cannot race us with a stock binary\n'
           '    if let Err(e) = crate::susfsd::load(None) {\n'
           '        warn!("susfs guard load during late-load: {e}");\n'
           '    }\n'
           '\n'
           '    // 8. Execute late-load stage scripts')
    check('late_load-susfs-guard', old, c)
    c = c.replace(old, new, 1)

    # Replace manager restart with adbd restart for adb_root
    old = ('    // 14. Restart Manager so it gets a fresh ksu fd from the newly loaded kernel module\n'
           '    info!("Restarting KernelSU Next Manager {package_name}...");\n'
           '    let _ = Command::new("am").args(["force-stop", package_name]).status();\n'
           '    let _ = Command::new("am")\n'
           '        .args([\n'
           '            "start",\n'
           '            "-n",\n'
           '            &format!("{package_name}/com.rifsxd.ksunext.ui.MainActivity"),\n'
           '        ])\n'
           '        .status();\n'
           '\n'
           '    Ok(())\n'
           '}')
    new = ('    // 14. Restart adbd so adb_root LD_PRELOAD takes effect\n'
           '    info!("Restarting adbd for adb_root...");\n'
           '    let _ = Command::new("stop").arg("adbd").status();\n'
           '    let _ = Command::new("start").arg("adbd").status();\n'
           '\n'
           '    Ok(())\n'
           '}')
    check('late_load-manager-restart', old, c)
    c = c.replace(old, new, 1)

    write_file(path, c)
    print("  patched: userspace/ksud/src/late_load.rs")


def patch_utils_rs(ksu_dir):
    path = os.path.join(ksu_dir, 'userspace', 'ksud', 'src', 'utils.rs')
    c = read_file(path)

    # 1. Add chown to rustix::fs imports
    old = 'use rustix::fs::{Mode, OFlags, open};\n'
    new = 'use rustix::fs::{Mode, OFlags, chown, open};\n'
    check('utils-chown', old, c)
    c = c.replace(old, new, 1)

    # 2. Add Gid, Uid to thread imports (add separate import line after setpgid)
    old = 'use rustix::process::setpgid;\n'
    new = 'use rustix::process::setpgid;\nuse rustix::thread::{Gid, Uid};\n'
    check('utils-gid-uid', old, c)
    c = c.replace(old, new, 1)

    # 3. Replace install() with stage_daemon + stage_daemon_from + finish_install + refactored install
    old = ('pub fn install(libadbroot: Option<PathBuf>) -> Result<()> {\n'
           '    ensure_dir_exists(defs::ADB_DIR)?;\n'
           '    let _ = std::fs::remove_file(defs::DAEMON_PATH);\n'
           '    std::fs::copy(\n'
           '        std::env::current_exe().with_context(|| "Failed to get self exe path")?,\n'
           '        defs::DAEMON_PATH,\n'
           '    )?;\n'
           '    restorecon::lsetfilecon(defs::DAEMON_PATH, restorecon::KSU_CON)?;\n'
           '    // install binary assets\n'
           '    assets::ensure_binaries(false).with_context(|| "Failed to extract assets")?;\n'
           '\n'
           '    link_ksud_to_bin()?;\n'
           '\n'
           '    if let Some(libadbroot) = libadbroot {\n'
           '        ensure_dir_exists(defs::LIBRARY_DIR)?;\n'
           '        let _ = std::fs::remove_file(defs::LIBADBROOT_PATH);\n'
           '        let _ = std::fs::copy(libadbroot, defs::LIBADBROOT_PATH);\n'
           '    }\n'
           '\n'
           '    Ok(())\n'
           '}')
    new = ('pub fn stage_daemon() -> Result<()> {\n'
           '    ensure_dir_exists(defs::ADB_DIR)?;\n'
           '\n'
           '    let current_exe = std::env::current_exe().with_context(|| "Failed to get self exe path")?;\n'
           '    if current_exe == Path::new(defs::DAEMON_PATH) {\n'
           '        return Ok(());\n'
           '    }\n'
           '\n'
           '    let daemon = std::fs::read(&current_exe)\n'
           '        .with_context(|| format!("Failed to read {}", current_exe.display()))?;\n'
           '    std::fs::write(defs::DAEMON_PATH, daemon)\n'
           '        .with_context(|| format!("Failed to write {}", defs::DAEMON_PATH))?;\n'
           '    #[cfg(unix)]\n'
           '    set_permissions(defs::DAEMON_PATH, Permissions::from_mode(0o755))?;\n'
           '\n'
           '    Ok(())\n'
           '}\n'
           '\n'
           'pub fn stage_daemon_from(staged_exe: impl AsRef<Path>) -> Result<()> {\n'
           '    ensure_dir_exists(defs::ADB_DIR)?;\n'
           '\n'
           '    std::fs::rename(staged_exe.as_ref(), defs::DAEMON_PATH).with_context(|| {\n'
           '        format!(\n'
           '            "Failed to rename {} to {}",\n'
           '            staged_exe.as_ref().display(),\n'
           '            defs::DAEMON_PATH\n'
           '        )\n'
           '    })?;\n'
           '    chown(defs::DAEMON_PATH, Some(Uid::ROOT), Some(Gid::ROOT))?;\n'
           '    #[cfg(unix)]\n'
           '    set_permissions(defs::DAEMON_PATH, Permissions::from_mode(0o755))?;\n'
           '\n'
           '    Ok(())\n'
           '}\n'
           '\n'
           'pub fn finish_install(libadbroot: Option<PathBuf>) -> Result<()> {\n'
           '    restorecon::lsetfilecon(defs::DAEMON_PATH, restorecon::KSU_CON)?;\n'
           '    // install binary assets\n'
           '    assets::ensure_binaries(false).with_context(|| "Failed to extract assets")?;\n'
           '\n'
           '    link_ksud_to_bin()?;\n'
           '\n'
           '    if let Some(libadbroot) = libadbroot {\n'
           '        ensure_dir_exists(defs::LIBRARY_DIR)?;\n'
           '        let _ = std::fs::remove_file(defs::LIBADBROOT_PATH);\n'
           '        let _ = std::fs::copy(libadbroot, defs::LIBADBROOT_PATH);\n'
           '    }\n'
           '\n'
           '    Ok(())\n'
           '}\n'
           '\n'
           'pub fn install(libadbroot: Option<PathBuf>) -> Result<()> {\n'
           '    stage_daemon()?;\n'
           '    finish_install(libadbroot)\n'
           '}')
    check('utils-install', old, c)
    c = c.replace(old, new, 1)

    # 4. Remove standalone daemonize function
    old = ('pub fn daemonize(use_init_pgrp: bool) -> Result<()> {\n'
           '    daemonize_with(use_init_pgrp, || Ok(()))\n'
           '}\n'
           '\n'
           'pub fn create_daemon(use_init_pgrp: bool) -> Result<bool> {')
    new = 'pub fn create_daemon(use_init_pgrp: bool) -> Result<bool> {'
    check('utils-daemonize', old, c)
    c = c.replace(old, new, 1)

    write_file(path, c)
    print("  patched: userspace/ksud/src/utils.rs")


def main():
    if len(sys.argv) < 2:
        print("Usage: apply.py <ksu-next-dir>", file=sys.stderr)
        sys.exit(1)

    ksu_dir = os.path.abspath(sys.argv[1])
    script_dir = os.path.dirname(os.path.abspath(__file__))

    print(f"Applying Samsung KDP/RKP/DEFEX patches to: {ksu_dir}")

    copy_new_files(ksu_dir, script_dir)
    patch_kbuild(ksu_dir)
    patch_init_c(ksu_dir)
    patch_syscall_hook_c(ksu_dir)
    patch_syscall_hook_h(ksu_dir)
    patch_hook_manager(ksu_dir)
    patch_tp_marker(ksu_dir)
    patch_sucompat(ksu_dir)
    patch_patch_memory(ksu_dir)
    patch_app_profile(ksu_dir)
    patch_selinux_hide_c(ksu_dir)
    patch_susfs_makefile(ksu_dir)
    patch_susfs_lsm_hook(ksu_dir)
    patch_late_load_rs(ksu_dir)
    patch_utils_rs(ksu_dir)

    print("\nAll patches applied successfully.")


if __name__ == '__main__':
    main()
