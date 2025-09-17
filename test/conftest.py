"""
Pytest configuration and fixtures for inspect-vagrant-sandbox tests.

Test Categories:
- unit: Fast unit tests with no external dependencies
- vm_required: Tests that require spinning up actual VMs (slow)
- inspect_eval: Tests that use the Inspect AI evaluation framework
"""

import pytest


def pytest_configure(config):
    """Register custom markers for test categorization."""
    config.addinivalue_line(
        "markers", 
        "unit: Fast unit tests with no external dependencies"
    )
    config.addinivalue_line(
        "markers", 
        "vm_required: Tests that require spinning up actual VMs (slow, requires Vagrant/QEMU)"
    )
    config.addinivalue_line(
        "markers", 
        "inspect_eval: Tests that use the Inspect AI evaluation framework"
    )


# Example usage patterns in comments:
# pytest -m unit                    # Run only fast unit tests
# pytest -m vm_required            # Run only VM infrastructure tests  
# pytest -m inspect_eval           # Run only Inspect AI evaluation tests
# pytest -m "not vm_required"      # Skip slow VM tests
# pytest -m "unit or inspect_eval" # Run unit tests and eval tests
# pytest -m "vm_required and inspect_eval"  # Run tests that need both VMs and Inspect AI