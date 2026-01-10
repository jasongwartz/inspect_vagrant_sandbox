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


@pytest.mark.unit
def test_vagrantfile_env_vars_accepts_dict():
    """Test that vagrantfile_env_vars accepts dict input and converts to tuple."""
    config = VagrantSandboxEnvironmentConfig(
        vagrantfile_env_vars={"FOO": "bar", "BAZ": "qux"}
    )
    assert config.vagrantfile_env_vars == (("FOO", "bar"), ("BAZ", "qux"))


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_vagrantfile_env_vars():
    """Verify vagrantfile_env_vars are passed to the Vagrant subprocess environment."""
    test_key = "TEST_VAGRANTFILE_ENV"
    test_value = "integration_test_value_12345"
    vagrantfile = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "Vagrantfile.basic"
    )

    config = VagrantSandboxEnvironmentConfig(
        vagrantfile_path=vagrantfile,
        vagrantfile_env_vars={test_key: test_value},
    )

    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "vagrantfile_env_vars_test",
        config,
        {"sample_id": "vagrantfile_env_vars_test"},
    )
    sandbox = sandboxes["default"]

    try:
        assert sandbox.vagrant.env.get(test_key) == test_value
        assert test_key not in os.environ
    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "vagrantfile_env_vars_test", config, sandboxes, interrupted=False
        )
