import pytest
import sys
import os
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vagrantsandbox.vagrant_sandbox_provider import VagrantSandboxEnvironmentConfig

def test_config_defaults():
    """Test that configuration defaults work correctly."""
    config = VagrantSandboxEnvironmentConfig()
    assert config.vagrantfile_path == "./Vagrantfile"
    assert config.primary_vm_name is None

def test_config_with_primary_vm():
    """Test configuration with primary VM specified."""
    config = VagrantSandboxEnvironmentConfig(
        vagrantfile_path="/path/to/Vagrantfile",
        primary_vm_name="attacker"
    )
    assert config.vagrantfile_path == "/path/to/Vagrantfile"
    assert config.primary_vm_name == "attacker"

def test_config_validation():
    """Test that invalid configurations are caught."""
    # Test with valid config
    config = VagrantSandboxEnvironmentConfig(primary_vm_name="web")
    assert config.primary_vm_name == "web"
    
    # Test that frozen=True works
    with pytest.raises(ValidationError):
        config.primary_vm_name = "changed"

if __name__ == "__main__":
    test_config_defaults()
    test_config_with_primary_vm() 
    test_config_validation()
    print("All configuration tests passed!")