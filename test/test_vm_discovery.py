import pytest
import subprocess
import sys
import os
import traceback
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vagrantsandbox.vagrant_sandbox_provider import (
    Vagrant,
    VagrantSandboxEnvironmentConfig,
)

pytestmark = pytest.mark.unit


def status_output(*vm_names):
    """What `vagrant status --machine-readable` prints for these machines."""
    return "".join(
        f"1700000000,{name},provider-name,qemu\n1700000000,{name},state,not_created\n"
        for name in vm_names
    )


def fake_vagrant(vagrant, stdout="", stderr="", returncode=0):
    """Make vagrant commands print stdout and stderr and exit with returncode."""
    return patch.multiple(
        vagrant,
        # Not as arguments, which would show up in a CalledProcessError
        env={**os.environ, "OUT": stdout, "ERR": stderr, "RC": str(returncode)},
        _make_vagrant_command=lambda args: [
            "sh",
            "-c",
            'printf %s "$OUT"; printf %s "$ERR" >&2; exit "$RC"',
        ],
    )


@pytest.mark.asyncio
async def test_vm_discovery_single():
    """Test VM discovery for single-VM Vagrantfile."""
    vagrant = Vagrant(root="/tmp")

    # Mock `vagrant status` to return single VM
    with fake_vagrant(vagrant, stdout=status_output("default")):
        vm_names = await vagrant.get_vm_names()
        assert vm_names == ["default"]


@pytest.mark.asyncio
async def test_vm_discovery_multi():
    """Test VM discovery for multi-VM Vagrantfile."""
    vagrant = Vagrant(root="/tmp")

    # Mock `vagrant status` to return multiple VMs
    with fake_vagrant(vagrant, stdout=status_output("target", "attacker")):
        vm_names = await vagrant.get_vm_names()
        assert set(vm_names) == {"target", "attacker"}


@pytest.mark.asyncio
async def test_vm_discovery_multi_with_suffix():
    """Test VM discovery preserves the full (suffixed) VM names."""
    vagrant = Vagrant(root="/tmp")

    with fake_vagrant(
        vagrant,
        stdout=status_output("target-sample01-abc123", "attacker-sample01-abc123"),
    ):
        vm_names = await vagrant.get_vm_names()
        assert vm_names == ["target-sample01-abc123", "attacker-sample01-abc123"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stdout, stderr",
    [
        # The Vagrantfile doesn't load: vagrant says why on stderr
        ("", "Vagrant failed to initialize at a very early stage: [...]\n"),
        # Anything later, e.g. a missing provider: on stdout
        ("1700000000,,error-exit,Vagrant::Errors::NoDefaultProvider,[...]\n", ""),
    ],
    ids=["vagrantfile-error", "missing-provider"],
)
async def test_vm_discovery_status_command_error(stdout, stderr):
    """Test VM discovery raises, saying why, when 'vagrant status' itself fails."""
    vagrant = Vagrant(root="/tmp")

    # Mock `vagrant status` to fail like vagrant does
    with fake_vagrant(vagrant, stdout=stdout, stderr=stderr, returncode=1):
        with pytest.raises(subprocess.CalledProcessError) as excinfo:
            await vagrant.get_vm_names()
    # The reason is in what Inspect reports as the sample's error...
    assert (stdout + stderr).strip() in "".join(
        traceback.format_exception(excinfo.value)
    )
    # ...and in what sample_init's failure handler logs
    assert (excinfo.value.stdout, excinfo.value.stderr) == (stdout, stderr)


@pytest.mark.asyncio
async def test_vm_discovery_unexpected_error_propagates():
    """Unexpected errors (e.g. a bug in name extraction) must NOT be swallowed.

    A bare 'except Exception: return []' is what silently masked the
    namedtuple/dict mismatch in issue #27.
    """
    vagrant = Vagrant(root="/tmp")

    # A wrong parser contract (dicts instead of Status namedtuples) raises
    # AttributeError during extraction - it must propagate, not degrade to [].
    with (
        fake_vagrant(vagrant),
        patch.object(
            vagrant,
            "_parse_status",
            return_value=[{"name": "target", "state": "not_created"}],
        ),
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
