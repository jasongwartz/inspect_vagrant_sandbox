"""
Stress tests for cat hanging bug (OPTIONAL).

These tests are more aggressive and may trigger intermittent issues
that don't appear in regular tests. Run these if the bug is suspected
to be timing or load-dependent.

Run with: pytest test/test_cat_hang_stress.py -v -s -m vm_required
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
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "Vagrantfile")


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_cat_after_write_read_pattern():
    """
    Test the common pattern: write file, then immediately cat it.
    This mimics how the model might work: create a file, then read it back.
    """
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "write_read_pattern",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {"sample_id": "pattern-test"},
    )
    sandbox = sandboxes["default"]

    try:
        for i in range(5):
            print(f"\n[ITERATION {i+1}] Write then cat pattern...")

            # Write file
            content = f"iteration {i}\n" * 100
            await sandbox.write_file("/tmp/test.txt", content)

            # Immediately cat it
            start = time.time()
            try:
                result = await asyncio.wait_for(
                    sandbox.exec(["cat", "/tmp/test.txt"]),
                    timeout=5.0
                )
                elapsed = time.time() - start
                print(f"  ✓ cat succeeded in {elapsed:.3f}s")
                assert len(result.stdout) > 0
            except asyncio.TimeoutError:
                elapsed = time.time() - start
                print(f"  ✗ cat HUNG after {elapsed:.3f}s")
                pytest.fail(f"Hang detected on iteration {i+1}")

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "write_read_pattern",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_cat_with_model_like_commands():
    """
    Test command patterns that a model might generate.
    Models often generate variations of cat commands.
    """
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "model_commands",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {"sample_id": "model-test"},
    )
    sandbox = sandboxes["default"]

    try:
        # Create test files
        await sandbox.write_file("/tmp/file1.txt", "content 1\n")
        await sandbox.write_file("/tmp/file2.txt", "content 2\n")
        await sandbox.write_file("/tmp/long.txt", "x" * 10000)

        # Various cat patterns a model might use
        test_commands = [
            (["cat", "/tmp/file1.txt"], "simple cat"),
            (["cat", "/tmp/file1.txt", "/tmp/file2.txt"], "cat multiple files"),
            (["cat", "/tmp/long.txt"], "cat large file"),
            (["cat", "/etc/passwd"], "cat system file"),
            (["cat", "/proc/cpuinfo"], "cat proc file"),
            (["sh", "-c", "cat /tmp/file1.txt"], "cat in shell"),
            (["bash", "-c", "cat /tmp/file1.txt"], "cat in bash"),
        ]

        for cmd, description in test_commands:
            print(f"\n[TEST] {description}: {' '.join(cmd)}")
            start = time.time()
            try:
                result = await asyncio.wait_for(
                    sandbox.exec(cmd),
                    timeout=5.0
                )
                elapsed = time.time() - start
                print(f"  ✓ {elapsed:.3f}s - {len(result.stdout)} bytes")
            except asyncio.TimeoutError:
                elapsed = time.time() - start
                print(f"  ✗ TIMEOUT after {elapsed:.3f}s")
                pytest.fail(f"Hang on: {description}")

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "model_commands",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_cat_with_special_file_contents():
    """
    Test cat with files containing special characters, binary data, etc.
    Sometimes encoding issues can cause hangs.
    """
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "special_contents",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {"sample_id": "special-test"},
    )
    sandbox = sandboxes["default"]

    try:
        test_contents = [
            ("plain text\n", "plain text"),
            ("line1\nline2\nline3\n", "multiline"),
            ("no newline", "no newline"),
            ("\n\n\n\n", "only newlines"),
            ("" * 1000, "empty lines"),
            ("unicode: 你好世界 🎉\n", "unicode"),
            ("tabs\t\tand\tspaces   \n", "whitespace"),
            ('quotes "and" \'stuff\'\n', "quotes"),
            ("$(echo test)\n", "shell metacharacters"),
            ("`backticks`\n", "backticks"),
        ]

        for content, description in test_contents:
            print(f"\n[TEST] cat file with {description}")
            filepath = f"/tmp/test_{description.replace(' ', '_')}.txt"

            await sandbox.write_file(filepath, content)

            start = time.time()
            try:
                result = await asyncio.wait_for(
                    sandbox.exec(["cat", filepath]),
                    timeout=5.0
                )
                elapsed = time.time() - start
                print(f"  ✓ {elapsed:.3f}s")
            except asyncio.TimeoutError:
                elapsed = time.time() - start
                print(f"  ✗ TIMEOUT after {elapsed:.3f}s")
                pytest.fail(f"Hang on: {description}")

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "special_contents",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_rapid_fire_commands():
    """
    Rapidly execute many cat commands in succession.
    Race conditions often appear under load.
    """
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "rapid_fire",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {"sample_id": "rapid-test"},
    )
    sandbox = sandboxes["default"]

    try:
        await sandbox.write_file("/tmp/test.txt", "test\n")

        num_commands = 20
        print(f"\n[TEST] Executing {num_commands} cat commands rapidly")

        for i in range(num_commands):
            start = time.time()
            try:
                result = await asyncio.wait_for(
                    sandbox.exec(["cat", "/tmp/test.txt"]),
                    timeout=3.0
                )
                elapsed = time.time() - start
                print(f"  {i+1:2d}. {elapsed:.3f}s", end="")
                if (i + 1) % 5 == 0:
                    print()  # Newline every 5
            except asyncio.TimeoutError:
                elapsed = time.time() - start
                print(f"\n  ✗ TIMEOUT on command {i+1} after {elapsed:.3f}s")
                pytest.fail(f"Hang on rapid-fire command {i+1}")

        print(f"\n  ✓ All {num_commands} commands succeeded")

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "rapid_fire",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_cat_mixed_with_other_commands():
    """
    Test cat mixed with other commands - the realistic eval scenario.
    """
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "mixed_commands",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {"sample_id": "mixed-test"},
    )
    sandbox = sandboxes["default"]

    try:
        print("\n[TEST] Realistic eval scenario with mixed commands")

        # Simulate a realistic task flow
        workflow = [
            (["pwd"], "check directory"),
            (["echo", "test"], "echo test"),
            (["ls", "/tmp"], "list tmp"),
            (["touch", "/tmp/script.sh"], "create file"),
            (["echo", "#!/bin/bash\necho hello"], "echo to stdout"),
            (["cat", "/etc/hostname"], "cat system file"),  # First cat
            (["whoami"], "check user"),
            (["cat", "/etc/passwd"], "cat passwd"),  # Second cat
            (["ls", "-la", "/tmp"], "list detailed"),
            (["cat", "/tmp/script.sh"], "cat created file"),  # Third cat
            (["rm", "/tmp/script.sh"], "cleanup"),
        ]

        for i, (cmd, description) in enumerate(workflow, 1):
            print(f"\n  {i:2d}. {description}: {' '.join(cmd)}")
            start = time.time()
            try:
                result = await asyncio.wait_for(
                    sandbox.exec(cmd),
                    timeout=5.0
                )
                elapsed = time.time() - start
                print(f"      ✓ {elapsed:.3f}s")
            except asyncio.TimeoutError:
                elapsed = time.time() - start
                print(f"      ✗ TIMEOUT after {elapsed:.3f}s")
                pytest.fail(f"Hang on step {i}: {description}")

        print("\n  ✓ Complete workflow succeeded")

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "mixed_commands",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_cat_concurrent_commands():
    """
    Test if concurrent cat commands cause issues.
    Note: Current implementation runs serially, but this tests if
    rapid succession without waiting causes problems.
    """
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "concurrent",
        VagrantSandboxEnvironmentConfig(vagrantfile_path=get_test_vagrantfile()),
        {"sample_id": "concurrent-test"},
    )
    sandbox = sandboxes["default"]

    try:
        # Create multiple test files
        for i in range(5):
            await sandbox.write_file(f"/tmp/file{i}.txt", f"content {i}\n")

        print("\n[TEST] Launching 5 cat commands concurrently")

        # Launch all at once
        tasks = [
            asyncio.wait_for(
                sandbox.exec(["cat", f"/tmp/file{i}.txt"]),
                timeout=10.0
            )
            for i in range(5)
        ]

        start = time.time()
        try:
            results = await asyncio.gather(*tasks)
            elapsed = time.time() - start
            print(f"  ✓ All completed in {elapsed:.3f}s")
            for i, result in enumerate(results):
                print(f"    File {i}: {result.stdout.strip()}")
        except asyncio.TimeoutError:
            elapsed = time.time() - start
            print(f"  ✗ TIMEOUT after {elapsed:.3f}s with concurrent commands")
            pytest.fail("Hang with concurrent cat commands")

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "concurrent",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )
