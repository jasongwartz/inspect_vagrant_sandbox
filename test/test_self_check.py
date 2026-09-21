"""
Runs Inspect's core sandbox "self_check" suite against the Vagrant sandbox.

`self_check` is the conformance test suite that ships with `inspect_ai`
(`inspect_ai.util._sandbox.self_check`). Every sandbox provider is expected to
run it to verify that its `SandboxEnvironment` implementation behaves the way
Inspect expects (file read/write, exec output/stderr/returncode/timeout, cwd,
env vars, exec-as-user, output limits, etc.).

The suite reuses a single sandbox environment across all its checks, so we spin
up one VM, run the whole suite against it, and assert that every check passes.

Run with:
    pytest test/test_self_check.py -v -s -m vm_required
"""

import os

import pytest
from inspect_ai.util import SandboxEnvironment
from inspect_ai.util._sandbox.self_check import self_check

from vagrantsandbox.vagrant_sandbox_provider import (
    VagrantSandboxEnvironment,
    VagrantSandboxEnvironmentConfig,
)


def get_basic_vagrantfile() -> str:
    """Path to the basic single-VM Vagrantfile used for the self-check."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "Vagrantfile.basic")


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_self_check() -> None:
    """Bring up a VM and run Inspect's core sandbox conformance suite against it."""
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "self_check",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_basic_vagrantfile()),
        {"sample_id": "self_check"},
    )
    sandbox: SandboxEnvironment = sandboxes["default"]
    assert isinstance(sandbox, VagrantSandboxEnvironment)

    try:
        results = await self_check(sandbox)
    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "self_check",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )

    failures = [
        f"{test_name}: {result}"
        for test_name, result in results.items()
        if result is not True
    ]
    assert not failures, "self_check failures:\n" + "\n".join(failures)
