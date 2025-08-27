import pytest
from ..src.vagrantsandbox.vagrant_sandbox_provider import (
    VagrantSandboxEnvironment,
    VagrantSandboxEnvironmentConfig,
    _run_in_executor,
)
import os


@pytest.mark.asyncio
async def test_sandbox_up_down():
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "test1",
        VagrantSandboxEnvironmentConfig(
            vagrantfile_path=(os.path.dirname(os.path.abspath(__file__)))
            + "/Vagrantfile"
        ),
        {},
    )
    sandbox = sandboxes["default"]
    assert isinstance(sandbox, VagrantSandboxEnvironment)
    try:
        # Get raw status
        status = await _run_in_executor(sandbox.vagrant.status)
        print(f"Status output:\n{status}")

        await sandbox.sample_cleanup(
            "test1", VagrantSandboxEnvironmentConfig(), {}, False
        )

    finally:
        await _run_in_executor(sandbox.vagrant.destroy)


@pytest.mark.asyncio
async def test_readfile_writefile():
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "test1",
        VagrantSandboxEnvironmentConfig(
            vagrantfile_path=(os.path.dirname(os.path.abspath(__file__)))
            + "/Vagrantfile"
        ),
        {},
    )
    sandbox = sandboxes["default"]
    assert isinstance(sandbox, VagrantSandboxEnvironment)
    try:
        await sandbox.write_file("/test-contents", "1234")

        ls_output = await sandbox.exec(["ls", "/test-contents"])
        assert ls_output.stdout != ""
        print(ls_output)

        assert (await sandbox.exec(["cat", "/test-contents"])).stdout == "1234"

        assert await sandbox.read_file("/test-contents") == "1234"

        await sandbox.sample_cleanup(
            "test1", VagrantSandboxEnvironmentConfig(), {}, False
        )
    finally:
        await _run_in_executor(sandbox.vagrant.destroy)
