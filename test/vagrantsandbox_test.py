import os

import pytest

from vagrantsandbox.vagrant_sandbox_provider import (
    VagrantSandboxEnvironment,
    VagrantSandboxEnvironmentConfig,
    _run_in_executor,
)


def get_test_vagrantfile():
    """Get path to test Vagrantfile.

    Uses VAGRANT_TEST_VAGRANTFILE env var if set (for CI), otherwise defaults to Vagrantfile.basic.
    """
    vagrantfile = os.environ.get("VAGRANT_TEST_VAGRANTFILE", "Vagrantfile.basic")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), vagrantfile)


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_sandbox_up_down():
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "test1",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {},
    )
    sandbox = sandboxes["default"]
    assert isinstance(sandbox, VagrantSandboxEnvironment)
    try:
        # Get raw status
        await _run_in_executor(sandbox.vagrant.status)
        await sandbox.sample_cleanup(
            "test1", VagrantSandboxEnvironmentConfig(), {}, False
        )

    finally:
        await _run_in_executor(sandbox.vagrant.destroy)


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_readfile_writefile():
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "test1",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {},
    )
    sandbox = sandboxes["default"]
    assert isinstance(sandbox, VagrantSandboxEnvironment)
    try:
        await sandbox.write_file("/tmp/test-contents", "1234")

        ls_output = await sandbox.exec(["ls", "/tmp/test-contents"])
        assert ls_output.stdout != ""

        assert (await sandbox.exec(["cat", "/tmp/test-contents"])).stdout == "1234"

        assert await sandbox.read_file("/tmp/test-contents") == "12345"

        await sandbox.sample_cleanup(
            "test1", VagrantSandboxEnvironmentConfig(), {}, False
        )
    finally:
        await _run_in_executor(sandbox.vagrant.destroy)
