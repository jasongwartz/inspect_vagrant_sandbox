import asyncio
import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vagrantsandbox.vagrant_sandbox_provider import (
    VagrantSandboxEnvironment,
    VagrantSandboxEnvironmentConfig,
)


@pytest.mark.unit
def test_config_defaults():
    """Test that configuration defaults work correctly."""
    config = VagrantSandboxEnvironmentConfig()
    assert config.vagrantfile_path == "./Vagrantfile"
    assert config.primary_vm_name is None


@pytest.mark.unit
def test_config_with_primary_vm():
    """Test configuration with primary VM specified."""
    config = VagrantSandboxEnvironmentConfig(
        vagrantfile_path="/path/to/Vagrantfile", primary_vm_name="attacker"
    )
    assert config.vagrantfile_path == "/path/to/Vagrantfile"
    assert config.primary_vm_name == "attacker"


@pytest.mark.unit
def test_config_validation():
    """Test that invalid configurations are caught."""
    # Test with valid config
    config = VagrantSandboxEnvironmentConfig(primary_vm_name="web")
    assert config.primary_vm_name == "web"

    # Test that frozen=True works
    with pytest.raises(ValidationError):
        config.primary_vm_name = "changed"


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_extra_envs_reach_vagrantfile():
    """Verify extra_envs values are accessible in Vagrantfile during provisioning."""
    test_value = "integration_test_value_12345"
    vagrantfile = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "Vagrantfile.extra_envs"
    )
    config = VagrantSandboxEnvironmentConfig(
        vagrantfile_path=vagrantfile,
        extra_envs=(("TEST_EXTRA_ENV", test_value),),
    )

    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "extra_envs_test",
        config,
        {"sample_id": "extra_envs_test"},
    )
    sandbox = sandboxes["default"]

    try:
        result = await asyncio.wait_for(
            sandbox.exec(["cat", "/tmp/extra_env_marker"]),
            timeout=20.0,
        )
        assert result.success, f"Failed to read marker: {result.stderr}"
        assert test_value in result.stdout
    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "extra_envs_test", config, sandboxes, interrupted=False
        )
