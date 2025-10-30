"""
Comprehensive tests for the cat command hanging bug.

This consolidates all direct/unit tests for reproducing and diagnosing
the bug where cat commands hang when executed via vagrant ssh.

Run with: pytest test/test_cat_hang.py -v -s -m vm_required
"""

import asyncio
import os
import time
import pytest

from vagrantsandbox.vagrant_sandbox_provider import (
    VagrantSandboxEnvironment,
    VagrantSandboxEnvironmentConfig,
)


def get_test_vagrantfile():
    """Get path to test Vagrantfile."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "Vagrantfile")


def get_basic_vagrantfile():
    """Get path to Vagrantfile.basic where bug was reported."""
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "Vagrantfile.basic"
    )


# ==============================================================================
# QUICK REPRODUCTION TESTS
# ==============================================================================

@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_cat_minimal_reproduction():
    """
    Minimal test case to reproduce cat hanging.
    This is the quickest test to run first.
    """
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "quick_test",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {"sample_id": "quick"},
    )
    sandbox = sandboxes["default"]

    try:
        # Write and read a test file
        await sandbox.write_file("/tmp/test.txt", "hello world")

        print("\n[TEST] Running: cat /tmp/test.txt (timeout: 10s)")
        start = time.time()

        try:
            result = await asyncio.wait_for(
                sandbox.exec(["cat", "/tmp/test.txt"]),
                timeout=10.0
            )
            elapsed = time.time() - start
            print(f"✓ cat succeeded in {elapsed:.2f}s")
            assert result.stdout == "hello world"

        except asyncio.TimeoutError:
            elapsed = time.time() - start
            print(f"✗ cat HUNG after {elapsed:.2f}s")
            pytest.fail("cat command hung - bug reproduced")

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "quick_test",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_stdin_devnull_fix():
    """
    Test if stdin=DEVNULL fixes the hanging issue.
    This validates the proposed solution.
    """
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "fix_test",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {"sample_id": "fix"},
    )
    sandbox = sandboxes["default"]

    try:
        await sandbox.write_file("/tmp/test.txt", "hello")

        # Test current implementation
        print("\n[TEST 1] Current implementation")
        start = time.time()
        try:
            result = await asyncio.wait_for(
                sandbox.vagrant.ssh(vm_name=sandbox.vm_name, command="cat /tmp/test.txt"),
                timeout=5.0
            )
            current_time = time.time() - start
            current_works = True
            print(f"  ✓ Works: {current_time:.2f}s")
        except asyncio.TimeoutError:
            current_time = time.time() - start
            current_works = False
            print(f"  ✗ HANGS: {current_time:.2f}s")

        # Test with stdin=DEVNULL fix
        print("\n[TEST 2] With stdin=DEVNULL")
        start = time.time()
        try:
            command = sandbox.vagrant._make_vagrant_command(
                ["ssh", sandbox.vm_name, "--no-tty", "--command", "cat /tmp/test.txt"]
            )

            proc = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,  # THE FIX
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=sandbox.vagrant.root,
                env=sandbox.vagrant.env,
            )

            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            fix_time = time.time() - start
            fix_works = True
            print(f"  ✓ Works: {fix_time:.2f}s")
        except asyncio.TimeoutError:
            fix_time = time.time() - start
            fix_works = False
            print(f"  ✗ HANGS: {fix_time:.2f}s")
            proc.kill()

        # Summary
        print(f"\n[RESULT] Current: {'✓' if current_works else '✗'} | Fix: {'✓' if fix_works else '✗'}")

        if not current_works and fix_works:
            print("⭐ stdin=DEVNULL FIXES THE BUG")

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "fix_test",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


# ==============================================================================
# SYSTEM FILES TESTS (Reported bug scenario)
# ==============================================================================

@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_cat_etc_os_release():
    """
    EXACT reproduction of reported bug:
    - Vagrantfile.basic
    - cat /etc/os-release
    """
    print("\n[TEST] cat /etc/os-release with Vagrantfile.basic")

    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "os_release_test",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_basic_vagrantfile()),
        {"sample_id": "os-release"},
    )
    sandbox = sandboxes["default"]

    try:
        start = time.time()
        try:
            result = await asyncio.wait_for(
                sandbox.exec(["cat", "/etc/os-release"]),
                timeout=15.0
            )
            elapsed = time.time() - start
            print(f"✓ SUCCESS in {elapsed:.2f}s")
            assert "Ubuntu" in result.stdout or "ubuntu" in result.stdout

        except asyncio.TimeoutError:
            elapsed = time.time() - start
            print(f"✗ TIMEOUT after {elapsed:.2f}s - BUG REPRODUCED")
            pytest.fail("cat /etc/os-release hung")

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "os_release_test",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_cat_common_system_files():
    """
    Test cat on various system files that models commonly query.
    """
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "system_files_test",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_basic_vagrantfile()),
        {"sample_id": "system-files"},
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

        print(f"\n[TEST] Testing {len(system_files)} system files")

        for filepath in system_files:
            start = time.time()
            try:
                result = await asyncio.wait_for(
                    sandbox.exec(["cat", filepath]),
                    timeout=10.0
                )
                elapsed = time.time() - start
                status = "✓" if result.success else "⚠"
                print(f"  {status} {filepath}: {elapsed:.3f}s")

            except asyncio.TimeoutError:
                elapsed = time.time() - start
                print(f"  ✗ {filepath}: TIMEOUT {elapsed:.3f}s")
                pytest.fail(f"cat {filepath} hung")

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "system_files_test",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


# ==============================================================================
# WORKFLOW TESTS (Realistic scenarios)
# ==============================================================================

@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_model_workflow_simulation():
    """
    Simulate typical model workflow exploring a system.
    """
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "workflow_test",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_basic_vagrantfile()),
        {"sample_id": "workflow"},
    )
    sandbox = sandboxes["default"]

    try:
        workflow = [
            (["pwd"], "Check directory"),
            (["whoami"], "Check user"),
            (["uname", "-a"], "System info"),
            (["cat", "/etc/os-release"], "OS version (CRITICAL)"),
            (["ls", "/"], "List root"),
            (["cat", "/etc/hostname"], "Hostname (CRITICAL)"),
        ]

        print(f"\n[TEST] Model workflow: {len(workflow)} commands")

        for cmd, description in workflow:
            start = time.time()
            try:
                result = await asyncio.wait_for(
                    sandbox.exec(cmd),
                    timeout=10.0
                )
                elapsed = time.time() - start
                print(f"  ✓ {description}: {elapsed:.3f}s")

            except asyncio.TimeoutError:
                elapsed = time.time() - start
                print(f"  ✗ {description}: TIMEOUT {elapsed:.3f}s")
                pytest.fail(f"Workflow hung at: {description}")

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "workflow_test",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_sequential_cats():
    """
    Test multiple cat commands in succession.
    """
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "sequential_test",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {"sample_id": "sequential"},
    )
    sandbox = sandboxes["default"]

    try:
        await sandbox.write_file("/tmp/test.txt", "content\n")

        num_iterations = 10
        print(f"\n[TEST] Running cat {num_iterations} times")

        for i in range(num_iterations):
            start = time.time()
            try:
                result = await asyncio.wait_for(
                    sandbox.exec(["cat", "/tmp/test.txt"]),
                    timeout=5.0
                )
                elapsed = time.time() - start
                print(f"  {i+1:2d}. {elapsed:.3f}s", end="")
                if (i + 1) % 5 == 0:
                    print()

            except asyncio.TimeoutError:
                elapsed = time.time() - start
                print(f"\n  ✗ TIMEOUT on iteration {i+1} after {elapsed:.3f}s")
                pytest.fail(f"Hang on iteration {i+1}")

        print(f"\n  ✓ All {num_iterations} completed")

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "sequential_test",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


# ==============================================================================
# COMPARISON TESTS
# ==============================================================================

@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_cat_vs_other_commands():
    """
    Compare cat with other file-reading commands to isolate the issue.
    """
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "comparison_test",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {"sample_id": "comparison"},
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
            (["ls", "-l", "/tmp/test.txt"], "ls"),
        ]

        print(f"\n[TEST] Comparing {len(commands)} commands")

        for cmd, name in commands:
            start = time.time()
            try:
                result = await asyncio.wait_for(
                    sandbox.exec(cmd),
                    timeout=5.0
                )
                elapsed = time.time() - start
                print(f"  ✓ {name:6s}: {elapsed:.3f}s")

            except asyncio.TimeoutError:
                elapsed = time.time() - start
                print(f"  ✗ {name:6s}: TIMEOUT {elapsed:.3f}s")
                pytest.fail(f"{name} hung")

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "comparison_test",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_file_size_variations():
    """
    Test if file size affects hanging behavior.
    """
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "filesize_test",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {"sample_id": "filesize"},
    )
    sandbox = sandboxes["default"]

    try:
        sizes = [
            (10, "tiny"),
            (1024, "1KB"),
            (8192, "8KB"),
            (65536, "64KB"),
        ]

        print(f"\n[TEST] Testing {len(sizes)} file sizes")

        for size, label in sizes:
            content = "x" * size
            filepath = f"/tmp/test_{label}.txt"
            await sandbox.write_file(filepath, content)

            start = time.time()
            try:
                result = await asyncio.wait_for(
                    sandbox.exec(["cat", filepath]),
                    timeout=10.0
                )
                elapsed = time.time() - start
                print(f"  ✓ {label:6s}: {elapsed:.3f}s")
                assert len(result.stdout) == size

            except asyncio.TimeoutError:
                elapsed = time.time() - start
                print(f"  ✗ {label:6s}: TIMEOUT {elapsed:.3f}s")
                pytest.fail(f"cat hung for {label}")

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "filesize_test",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )
