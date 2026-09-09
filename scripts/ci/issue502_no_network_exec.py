#!/usr/bin/env python3
"""Execute one #502 pilot command with Linux x86-64 networking syscalls denied.

This helper is intentionally narrow evaluation infrastructure, not a general platform process
sandbox. It installs an unprivileged seccomp filter after ``PR_SET_NO_NEW_PRIVS`` and then execs the
requested command. The filter survives exec and is inherited by descendants.

The wrapper denies socket creation plus the principal socket I/O/configuration syscalls and rejects
the x32 syscall ABI. It is used only after the parent closes unrelated file descriptors. A separate
self-test in the ProjectAtlas pilot proves that socket creation returns EPERM before the provider is
executed.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import os
import platform

_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2

_BPF_LD_W_ABS = 0x20
_BPF_JMP_JEQ_K = 0x15
_BPF_JMP_JGE_K = 0x35
_BPF_RET_K = 0x06

_SECCOMP_RET_KILL_PROCESS = 0x80000000
_SECCOMP_RET_ERRNO = 0x00050000
_SECCOMP_RET_ALLOW = 0x7FFF0000

_AUDIT_ARCH_X86_64 = 0xC000003E
_X32_SYSCALL_BIT = 0x40000000
_SECCOMP_DATA_NR_OFFSET = 0
_SECCOMP_DATA_ARCH_OFFSET = 4

# Linux x86-64 socket-related syscalls. ``socket``/``socketpair`` prevent creation of new network
# or Unix-domain sockets; the remaining calls fail closed even if a socket descriptor were somehow
# inherited. Subprocess closes unrelated descriptors before this wrapper starts.
_DENIED_SYSCALLS = (
    41,  # socket
    42,  # connect
    43,  # accept
    44,  # sendto
    45,  # recvfrom
    46,  # sendmsg
    47,  # recvmsg
    48,  # shutdown
    49,  # bind
    50,  # listen
    51,  # getsockname
    52,  # getpeername
    53,  # socketpair
    54,  # setsockopt
    55,  # getsockopt
    288,  # accept4
    299,  # recvmmsg
    307,  # sendmmsg
)


class _SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint32),
    ]


class _SockFprog(ctypes.Structure):
    _fields_ = [
        ("len", ctypes.c_ushort),
        ("filter", ctypes.POINTER(_SockFilter)),
    ]


def _statement(code: int, value: int) -> _SockFilter:
    return _SockFilter(code=code, jt=0, jf=0, k=value)


def _jump(code: int, value: int, *, on_true: int, on_false: int) -> _SockFilter:
    return _SockFilter(code=code, jt=on_true, jf=on_false, k=value)


def _install_no_network_filter() -> None:
    if platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise RuntimeError("issue #502 seccomp pilot wrapper supports Linux x86-64 only")

    deny = _SECCOMP_RET_ERRNO | errno.EPERM
    instructions: list[_SockFilter] = [
        _statement(_BPF_LD_W_ABS, _SECCOMP_DATA_ARCH_OFFSET),
        _jump(_BPF_JMP_JEQ_K, _AUDIT_ARCH_X86_64, on_true=1, on_false=0),
        _statement(_BPF_RET_K, _SECCOMP_RET_KILL_PROCESS),
        _statement(_BPF_LD_W_ABS, _SECCOMP_DATA_NR_OFFSET),
        _jump(_BPF_JMP_JGE_K, _X32_SYSCALL_BIT, on_true=0, on_false=1),
        _statement(_BPF_RET_K, deny),
    ]
    for syscall_number in _DENIED_SYSCALLS:
        instructions.extend(
            (
                _jump(_BPF_JMP_JEQ_K, syscall_number, on_true=0, on_false=1),
                _statement(_BPF_RET_K, deny),
            )
        )
    instructions.append(_statement(_BPF_RET_K, _SECCOMP_RET_ALLOW))

    filters = (_SockFilter * len(instructions))(*instructions)
    program = _SockFprog(
        len=len(instructions),
        filter=ctypes.cast(filters, ctypes.POINTER(_SockFilter)),
    )
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.restype = ctypes.c_int

    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    if libc.prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.byref(program), 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a command is required after --")

    _install_no_network_filter()
    os.execvpe(command[0], command, dict(os.environ))
    raise AssertionError("exec returned unexpectedly")  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
