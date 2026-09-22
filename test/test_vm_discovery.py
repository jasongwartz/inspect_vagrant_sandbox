import pytest
import subprocess
import sys
import os
from unittest.mock import patch

# python-vagrant's status() returns Status namedtuples (name, state, provider),
# NOT dicts. The mocks below must use the real type so they can't drift from
# the library's actual contract (see issue #27).
from vagrant import Status

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
        vagrant,
        "status",
        return_value=[Status(name="default", state="not_created", provider="qemu")],
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
            Status(name="target", state="not_created", provider="qemu"),
            Status(name="attacker", state="not_created", provider="qemu"),
        ],
    ):
        vm_names = await vagrant.get_vm_names()
        assert set(vm_names) == {"target", "attacker"}


@pytest.mark.asyncio
async def test_vm_discovery_multi_with_suffix():
    """Test VM discovery preserves the full (suffixed) VM names."""
    vagrant = Vagrant(root="/tmp")

    with patch.object(
        vagrant,
        "status",
        return_value=[
            Status(name="target-sample01-abc123", state="running", provider="qemu"),
            Status(name="attacker-sample01-abc123", state="running", provider="qemu"),
        ],
    ):
        vm_names = await vagrant.get_vm_names()
        assert vm_names == ["target-sample01-abc123", "attacker-sample01-abc123"]


@pytest.mark.asyncio
async def test_vm_discovery_status_command_error_propagates():
    """A 'vagrant status' failure must propagate, not degrade to single-VM mode.

    The old fallback (return [] and assume a single unnamed VM) silently
    masked broken vagrant setups - see issue #27.
    """
    vagrant = Vagrant(root="/tmp")

    # Mock the status method to fail like a subprocess would
    with patch.object(
        vagrant,
        "status",
        side_effect=subprocess.CalledProcessError(1, ["vagrant", "status"]),
    ):
        with pytest.raises(subprocess.CalledProcessError):
            await vagrant.get_vm_names()


@pytest.mark.asyncio
async def test_vm_discovery_unexpected_error_propagates():
    """Unexpected errors (e.g. a bug in name extraction) must NOT be swallowed.

    A bare 'except Exception: return []' is what silently masked the
    namedtuple/dict mismatch in issue #27.
    """
    vagrant = Vagrant(root="/tmp")

    # A wrong status() contract (dicts instead of Status namedtuples) raises
    # AttributeError during extraction - it must propagate, not degrade to [].
    with patch.object(
        vagrant,
        "status",
        return_value=[{"name": "target", "state": "not_created"}],
    ):
        with pytest.raises(AttributeError):
            await vagrant.get_vm_names()


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
