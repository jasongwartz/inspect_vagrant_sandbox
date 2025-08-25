import pytest
from ..src.vagrantsandbox.vagrant_sandbox_provider import (
    VagrantSandboxEnvironment,
    VagrantSandboxEnvironmentConfig,
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
        status = await sandbox.vagrant.status_string()
        print(f"Status output:\n{status}")

        await sandbox.sample_cleanup(
            "test1", VagrantSandboxEnvironmentConfig(), {}, False
        )

    finally:
        await sandbox.vagrant.destroy()
