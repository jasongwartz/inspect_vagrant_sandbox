"""
Runs Inspect's core sandbox "self_check" suite against the Vagrant sandbox.

`self_check` is the conformance test suite that ships with `inspect_ai`
(`inspect_ai.util._sandbox.self_check`). Every sandbox provider is expected to
run it to verify that its `SandboxEnvironment` implementation behaves the way
Inspect expects (file read/write, exec output/stderr/returncode/timeout, cwd,
env vars, exec-as-user, output limits, etc.).

The module defines each check as a `test_*` function taking a `sandbox_env`
and lists them all in its `__all__`. Inspect's own runner collects them as
separate pytest tests, but CI boots a VM for every collected `vm_required` test,
so instead we spin up one VM, run every check in `__all__` against it in turn
(they clean up after themselves), and assert that every check passes.

Run with:
    pytest test/test_self_check.py -v -s -m vm_required
"""

import os

import pytest
from inspect_ai.util import SandboxEnvironment
from inspect_ai.util._sandbox import self_check

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
    checks = [getattr(self_check, name) for name in self_check.__all__]
    # 44 checks as of inspect-ai 0.3.268: fail loudly rather than pass vacuously
    # if a change upstream stops `__all__` from listing them.
    assert len(checks) >= 40, f"only {len(checks)} self_check checks found"

    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "self_check",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_basic_vagrantfile()),
        {"sample_id": "self_check"},
    )
    sandbox: SandboxEnvironment = sandboxes["default"]
    assert isinstance(sandbox, VagrantSandboxEnvironment)

    results: dict[str, bool | str] = {}
    try:
        for check in checks:
            try:
                await check(sandbox_env=sandbox)
                results[check.__name__] = True
            except AssertionError as e:
                results[check.__name__] = f"FAILED: [{e}]"
            except Exception as e:
                results[check.__name__] = f"ERROR: [{e!r}]"
    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "self_check",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )

    failures = {
        test_name: " ".join(str(result).split())
        for test_name, result in results.items()
        if result is not True
    }
    if failures:
        # Keep the report readable: one line per failing check, truncated —
        # some failure reprs embed the check's full (multi-megabyte) output.
        lines = [
            f"  {name}: {message[:200]}{'…' if len(message) > 200 else ''}"
            for name, message in failures.items()
        ]
        pytest.fail(
            f"{len(failures)}/{len(results)} self_check checks failed:\n"
            + "\n".join(lines),
            pytrace=False,
        )
