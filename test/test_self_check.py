"""
Runs Inspect's core sandbox "self_check" suite against the Vagrant sandbox.

`self_check` is the conformance test suite that ships with `inspect_ai`
(`inspect_ai.util._sandbox.self_check`). Every sandbox provider is expected to
run it to verify that its `SandboxEnvironment` implementation behaves the way
Inspect expects (file read/write, exec output/stderr/returncode/timeout, cwd,
env vars, exec-as-user, output limits, etc.).

The suite reuses a single sandbox environment across all its checks, so we spin
up one VM, run the whole suite against it, and assert that nothing fails other
than the documented `KNOWN_FAILURES`.

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


# Checks that are expected to fail against the current Vagrant provider.
# Document each with its root cause. Listing a check here (rather than deleting
# it) keeps the rest of the suite acting as a regression guard, and the list
# shrinks naturally as the provider gains features: if a check in this list
# starts passing, the test fails and tells you to remove it.
KNOWN_FAILURES: list[str] = [
    # Not a provider bug: self_check creates its test user with
    # `adduser --comment`, but Ubuntu 22.04's Debian adduser does not support
    # `--comment` (it was added in adduser 3.13x, i.e. Ubuntu 23.04+), so user
    # creation fails with "Unknown option: comment". The provider's `user=`
    # forwarding itself works, as demonstrated by test_exec_as_nonexistent_user
    # passing. This will pass once the test box moves to Ubuntu 24.04+.
    "test_exec_as_user",
]


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

    unexpected_failures = [
        f"{test_name}: {result}"
        for test_name, result in results.items()
        if result is not True and test_name not in KNOWN_FAILURES
    ]
    # Surface checks we expected to fail but that unexpectedly passed, so the
    # KNOWN_FAILURES list can be trimmed once the provider is fixed.
    unexpected_passes = [
        test_name
        for test_name, result in results.items()
        if result is True and test_name in KNOWN_FAILURES
    ]

    message_parts = []
    if unexpected_failures:
        message_parts.append(
            "Unexpected self_check failures:\n" + "\n".join(unexpected_failures)
        )
    if unexpected_passes:
        message_parts.append(
            "Checks in KNOWN_FAILURES now pass (remove them from the list):\n"
            + "\n".join(unexpected_passes)
        )

    assert not message_parts, "\n\n".join(message_parts)
