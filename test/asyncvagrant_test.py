import pytest
from ..src.vagrantsandbox.vagrant_sandbox_provider import AsyncVagrant
import os


@pytest.mark.asyncio
async def test_up_down():
    vagrant = AsyncVagrant(os.path.dirname(os.path.abspath(__file__)))

    try:
        await vagrant.up()

        # Get raw status
        status = await vagrant.status_string()
        print(f"Status output:\n{status}")

        # Run your command
        result = await vagrant.ssh_command("whoami")
        print(f"Script output: {result}")

    finally:
        await vagrant.destroy()
