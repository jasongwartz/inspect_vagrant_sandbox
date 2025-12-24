"""
Minimal VM test for GitHub Actions CI.

This test uses libvirt/KVM on Linux runners to verify VM functionality works in CI.
"""

import os
import pytest
from vagrantsandbox.vagrant_sandbox_provider import (
    VagrantSandboxEnvironment,
    VagrantSandboxEnvironmentConfig,
    _run_in_executor,
)


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_ci_vm_basic():
    """Test that we can start a VM, run a command, and clean up in CI."""
    vagrantfile_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "Vagrantfile.ci"
    )

    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "ci_test",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=vagrantfile_path),
        {},
    )
    sandbox = sandboxes["default"]
    assert isinstance(sandbox, VagrantSandboxEnvironment)

    try:
        # Run a simple command to verify the VM is working
        result = await sandbox.exec(["echo", "hello from CI"])
        assert result.success
        assert "hello from CI" in result.stdout

        # Verify we can check the OS
        result = await sandbox.exec(["cat", "/etc/os-release"])
        assert result.success
        assert "Ubuntu" in result.stdout

    finally:
        await _run_in_executor(sandbox.vagrant.destroy)
