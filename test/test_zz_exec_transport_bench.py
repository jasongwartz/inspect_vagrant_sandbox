"""EXPERIMENT ONLY (not for merging): time exec() transports on a real VM."""

import os
import time
from typing import Any

import pytest

from vagrantsandbox.vagrant_sandbox_provider import (
    VagrantSandboxEnvironment,
    VagrantSandboxEnvironmentConfig,
)

CHUNK = "x" * 65536
CASES: list[tuple[str, list[str], dict[str, Any]]] = [
    ("small", ["true"], {}),
    ("small user=root", ["true"], {"user": "root"}),
    (
        "small env+cwd",
        ["sh", "-c", 'test "$K" = v && pwd'],
        {"env": {"K": "v"}, "cwd": "/tmp"},
    ),
    ("1MiB argv", ["printf", "%s", *([CHUNK] * 16)], {}),
    ("1MiB argv user=root", ["printf", "%s", *([CHUNK] * 16)], {"user": "root"}),
    ("1MiB argv timeout=60", ["printf", "%s", *([CHUNK] * 16)], {"timeout": 60}),
    (
        "1MiB argv + 5MiB input",
        ["sh", "-c", 'wc -c; printf %s "$@" | wc -c', "sh", *([CHUNK] * 16)],
        {"input": "y" * (5 * 1024 * 1024)},
    ),
    ("quote-heavy 60KiB", ["printf", "%s", "'" * 60000], {}),
]


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_zz_exec_transport_bench() -> None:
    vagrantfile = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "Vagrantfile.basic"
    )
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "bench",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=vagrantfile),
        {"sample_id": "bench"},
    )
    env = sandboxes["default"]
    lines: list[str] = []
    try:
        for label, cmd, kw in CASES:
            times = []
            for _ in range(5):
                start = time.monotonic()
                result = await env.exec(cmd, **kw)
                times.append(time.monotonic() - start)
                assert result.success, (label, result.returncode, result.stderr[:500])
            if label.startswith("1MiB argv") and "input" not in label:
                assert result.stdout == CHUNK * 16, label
            if "input" in label:
                assert result.stdout.split() == [
                    str(5 * 1024 * 1024),
                    str(16 * 65536),
                ], result.stdout
            if label.startswith("quote-heavy"):
                assert result.stdout == "'" * 60000
            times.sort()
            lines.append(
                f"{label:28s} min={times[0]:.2f}s med={times[2]:.2f}s max={times[-1]:.2f}s"
            )
        left = await env.exec(
            ["sh", "-c", "ls -a /tmp | grep -c inspect-exec || true"], user="root"
        )
        lines.append(f"leftover /tmp/inspect-exec-* files: {left.stdout.strip()}")
    finally:
        print(
            "\nBENCH "
            + "B-threshold (final #63, d0746c6)"
            + "\n"
            + "\n".join("BENCH " + ln for ln in lines)
        )
        await VagrantSandboxEnvironment.sample_cleanup(
            "bench", VagrantSandboxEnvironmentConfig(), sandboxes, interrupted=False
        )
