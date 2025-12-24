"""
Tests for file operations via sandbox exec commands.

These tests ensure that file reading operations (cat, head, tail, etc.)
work correctly in various scenarios and don't hang or timeout.

Run with: pytest test/test_file_operations.py -v -s -m vm_required
"""

import asyncio
import os
import pytest

from vagrantsandbox.vagrant_sandbox_provider import (
    VagrantSandboxEnvironment,
    VagrantSandboxEnvironmentConfig,
)


def get_test_vagrantfile():
    """Get path to test Vagrantfile.

    Uses VAGRANT_TEST_VAGRANTFILE env var if set (for CI), otherwise defaults to Vagrantfile.basic.
    """
    vagrantfile = os.environ.get("VAGRANT_TEST_VAGRANTFILE", "Vagrantfile.basic")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), vagrantfile)


def get_basic_vagrantfile():
    """Get path to Vagrantfile.basic (or CI override)."""
    return get_test_vagrantfile()


# ==============================================================================
# BASIC FILE READING TESTS
# ==============================================================================


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_cat_basic_file():
    """Test reading a simple file with cat."""
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "cat_basic",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {"sample_id": "cat_basic"},
    )
    sandbox = sandboxes["default"]

    try:
        await sandbox.write_file("/tmp/test.txt", "hello world")
        result = await asyncio.wait_for(
            sandbox.exec(["cat", "/tmp/test.txt"]), timeout=20.0
        )
        assert result.stdout == "hello world"
        assert result.success

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "cat_basic",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_cat_system_files():
    """Test reading common system files."""
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "system_files",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_basic_vagrantfile()),
        {"sample_id": "system_files"},
    )
    sandbox = sandboxes["default"]

    try:
        system_files = [
            "/etc/os-release",
            "/etc/hostname",
            "/etc/passwd",
            "/proc/version",
            "/proc/cpuinfo",
        ]

        for filepath in system_files:
            result = await asyncio.wait_for(
                sandbox.exec(["cat", filepath]), timeout=20.0
            )
            assert result.success, f"Failed to read {filepath}"
            assert len(result.stdout) > 0, f"Empty output from {filepath}"

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "system_files",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_sequential_file_reads():
    """Test multiple sequential file read operations."""
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "sequential",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {"sample_id": "sequential"},
    )
    sandbox = sandboxes["default"]

    try:
        await sandbox.write_file("/tmp/test.txt", "content\n")

        # Read the same file multiple times
        for i in range(10):
            result = await asyncio.wait_for(
                sandbox.exec(["cat", "/tmp/test.txt"]), timeout=20.0
            )
            assert result.stdout == "content\n"
            assert result.success

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "sequential",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


# ==============================================================================
# FILE SIZE VARIATIONS
# ==============================================================================


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_cat_various_file_sizes():
    """Test reading files of different sizes."""
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "file_sizes",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {"sample_id": "file_sizes"},
    )
    sandbox = sandboxes["default"]

    try:
        test_sizes = [
            (10, "tiny"),
            (1024, "1KB"),
            (8192, "8KB"),
            (65536, "64KB"),
        ]

        for size, label in test_sizes:
            content = "x" * size
            filepath = f"/tmp/test_{label}.txt"
            await sandbox.write_file(filepath, content)

            result = await asyncio.wait_for(
                sandbox.exec(["cat", filepath]), timeout=30.0
            )
            assert len(result.stdout) == size, f"Wrong size for {label}"
            assert result.success

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "file_sizes",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


# ==============================================================================
# SPECIAL FILE CONTENTS
# ==============================================================================


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_cat_special_characters():
    """Test reading files with special characters and edge cases."""
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "special_chars",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {"sample_id": "special_chars"},
    )
    sandbox = sandboxes["default"]

    try:
        test_cases = [
            ("plain text\n", "plain"),
            ("line1\nline2\nline3\n", "multiline"),
            ("no newline", "no_newline"),
            ("\n\n\n\n", "only_newlines"),
            ("unicode: 你好世界 🎉\n", "unicode"),
            ("tabs\t\tand\tspaces   \n", "whitespace"),
            ("quotes \"and\" 'stuff'\n", "quotes"),
        ]

        for content, label in test_cases:
            filepath = f"/tmp/test_{label}.txt"
            await sandbox.write_file(filepath, content)

            result = await asyncio.wait_for(
                sandbox.exec(["cat", filepath]), timeout=20.0
            )
            assert result.stdout == content, f"Content mismatch for {label}"
            assert result.success

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "special_chars",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


# ==============================================================================
# MULTIPLE FILE OPERATIONS
# ==============================================================================


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_compare_file_reading_commands():
    """Test different commands for reading files."""
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "compare_commands",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {"sample_id": "compare_commands"},
    )
    sandbox = sandboxes["default"]

    try:
        test_content = "line1\nline2\nline3\n"
        await sandbox.write_file("/tmp/test.txt", test_content)

        commands = [
            (["cat", "/tmp/test.txt"], "cat"),
            (["head", "-n", "3", "/tmp/test.txt"], "head"),
            (["tail", "-n", "3", "/tmp/test.txt"], "tail"),
            (["grep", ".", "/tmp/test.txt"], "grep"),
            (["wc", "-l", "/tmp/test.txt"], "wc"),
        ]

        for cmd, name in commands:
            result = await asyncio.wait_for(sandbox.exec(cmd), timeout=20.0)
            assert result.success, f"{name} failed"
            assert len(result.stdout) > 0, f"{name} returned empty output"

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "compare_commands",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_cat_multiple_files():
    """Test reading multiple files in one command."""
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "multiple_files",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {"sample_id": "multiple_files"},
    )
    sandbox = sandboxes["default"]

    try:
        await sandbox.write_file("/tmp/file1.txt", "content 1\n")
        await sandbox.write_file("/tmp/file2.txt", "content 2\n")

        result = await asyncio.wait_for(
            sandbox.exec(["cat", "/tmp/file1.txt", "/tmp/file2.txt"]), timeout=20.0
        )
        assert "content 1" in result.stdout
        assert "content 2" in result.stdout
        assert result.success

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "multiple_files",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


# ==============================================================================
# REALISTIC WORKFLOW TESTS
# ==============================================================================


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_typical_command_workflow():
    """Test a typical sequence of commands including file operations."""
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "workflow",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_basic_vagrantfile()),
        {"sample_id": "workflow"},
    )
    sandbox = sandboxes["default"]

    try:
        workflow = [
            (["pwd"], "Check directory"),
            (["whoami"], "Check user"),
            (["uname", "-a"], "System info"),
            (["cat", "/etc/os-release"], "OS version"),
            (["ls", "/"], "List root"),
            (["cat", "/etc/hostname"], "Hostname"),
        ]

        for cmd, description in workflow:
            result = await asyncio.wait_for(sandbox.exec(cmd), timeout=20.0)
            assert result.success, f"Failed: {description}"

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "workflow",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_write_then_read_pattern():
    """Test the common pattern of writing a file then immediately reading it."""
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "write_read",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {"sample_id": "write_read"},
    )
    sandbox = sandboxes["default"]

    try:
        for i in range(5):
            content = f"iteration {i}\n" * 100
            await sandbox.write_file("/tmp/test.txt", content)

            result = await asyncio.wait_for(
                sandbox.exec(["cat", "/tmp/test.txt"]), timeout=20.0
            )
            assert result.stdout == content
            assert result.success

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "write_read",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_mixed_command_sequence():
    """Test cat mixed with various other commands."""
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "mixed_commands",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {"sample_id": "mixed_commands"},
    )
    sandbox = sandboxes["default"]

    try:
        workflow = [
            (["pwd"], "check directory"),
            (["echo", "test"], "echo test"),
            (["ls", "/tmp"], "list tmp"),
            (["touch", "/tmp/script.sh"], "create file"),
            (["cat", "/etc/hostname"], "cat system file"),
            (["whoami"], "check user"),
            (["cat", "/etc/passwd"], "cat passwd"),
            (["ls", "-la", "/tmp"], "list detailed"),
            (["cat", "/tmp/script.sh"], "cat created file"),
            (["rm", "/tmp/script.sh"], "cleanup"),
        ]

        for cmd, description in workflow:
            result = await asyncio.wait_for(sandbox.exec(cmd), timeout=20.0)
            assert result.success, f"Failed at: {description}"

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "mixed_commands",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )
