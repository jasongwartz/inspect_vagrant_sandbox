import pytest
import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vagrantsandbox.vagrant_sandbox_provider import (
    Vagrant,
    VagrantSandboxEnvironmentConfig,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_vm_discovery_single():
    """Test VM discovery for single-VM Vagrantfile."""
    vagrant = Vagrant(root="/tmp")

    # Mock the status method to return single VM
    with patch.object(
        vagrant, "status", return_value=[{"name": "default", "state": "not_created"}]
    ):
        vm_names = await vagrant.get_vm_names()
        assert vm_names == ["default"]


@pytest.mark.asyncio
async def test_vm_discovery_multi():
    """Test VM discovery for multi-VM Vagrantfile."""
    vagrant = Vagrant(root="/tmp")

    # Mock the status method to return multiple VMs
    with patch.object(
        vagrant,
        "status",
        return_value=[
            {"name": "target", "state": "not_created"},
            {"name": "attacker", "state": "not_created"},
        ],
    ):
        vm_names = await vagrant.get_vm_names()
        assert set(vm_names) == {"target", "attacker"}


@pytest.mark.asyncio
async def test_vm_discovery_error():
    """Test VM discovery handles errors gracefully."""
    vagrant = Vagrant(root="/tmp")

    # Mock the status method to raise an exception
    with patch.object(vagrant, "status", side_effect=Exception("Vagrant not found")):
        vm_names = await vagrant.get_vm_names()
        assert vm_names == []


def test_config_primary_vm():
    """Test primary VM configuration."""
    # Test default (no primary specified)
    config = VagrantSandboxEnvironmentConfig()
    assert config.primary_vm_name is None

    # Test with primary specified
    config = VagrantSandboxEnvironmentConfig(primary_vm_name="attacker")
    assert config.primary_vm_name == "attacker"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
