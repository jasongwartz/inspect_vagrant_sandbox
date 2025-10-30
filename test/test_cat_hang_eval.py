"""
Inspect eval-based tests for cat hanging bug.

These tests use Inspect's full framework with MockLLM to force specific
bash tool calls, testing the complete tool execution pipeline.

Run with: pytest test/test_cat_hang_eval.py -v -s -m vm_required
Or: inspect eval test/test_cat_hang_eval.py::task_name --model mockllm/model
"""

import asyncio
import os
import sys
import pytest

from inspect_ai import Task, eval, task
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessageAssistant
from inspect_ai.scorer import includes
from inspect_ai.solver import generate
from inspect_ai.util import SandboxEnvironmentSpec

# Import after adding to path if needed
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from vagrantsandbox.vagrant_sandbox_provider import (
    VagrantSandboxEnvironmentConfig,
)


def get_basic_vagrantfile():
    """Get path to Vagrantfile.basic where bug was reported."""
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "Vagrantfile.basic"
    )


# ==============================================================================
# INSPECT TASK DEFINITIONS
# ==============================================================================

@task
def cat_os_release():
    """
    Simple task forcing model to run 'cat /etc/os-release'.
    Can be run as standalone eval with MockLLM or real model.
    """
    return Task(
        dataset=[
            Sample(
                input=(
                    "You are in a Linux system. Use the bash tool to check what "
                    "operating system version is running. Read the /etc/os-release file."
                ),
                target="Ubuntu",
                id="os-check",
            )
        ],
        plan=[generate()],
        scorer=includes(),
        sandbox=SandboxEnvironmentSpec(
            "vagrant",
            VagrantSandboxEnvironmentConfig(
                vagrantfile_path=get_basic_vagrantfile()
            ),
        ),
        max_messages=5,
    )


@task
def cat_multiple_files():
    """
    Task requiring multiple cat commands in sequence.
    """
    return Task(
        dataset=[
            Sample(
                input=(
                    "Using bash commands, gather the following system information:\n"
                    "1. Operating system version (from /etc/os-release)\n"
                    "2. Hostname (from /etc/hostname)\n"
                    "3. CPU information (from /proc/cpuinfo)\n"
                    "Provide a summary."
                ),
                target=["Ubuntu", "ubuntu", "vagrant"],
                id="system-info",
            )
        ],
        plan=[generate()],
        scorer=includes(),
        sandbox=SandboxEnvironmentSpec(
            "vagrant",
            VagrantSandboxEnvironmentConfig(
                vagrantfile_path=get_basic_vagrantfile()
            ),
        ),
        max_messages=15,
    )


@task
def cat_realistic_workflow():
    """
    Realistic task where model explores the system.
    """
    return Task(
        dataset=[
            Sample(
                input=(
                    "You are investigating a Linux system. Gather basic system "
                    "information: OS version, kernel version, hostname, and current user. "
                    "Provide a brief summary."
                ),
                target="Ubuntu",
                id="system-analysis",
            )
        ],
        plan=[generate()],
        scorer=includes(),
        sandbox=SandboxEnvironmentSpec(
            "vagrant",
            VagrantSandboxEnvironmentConfig(
                vagrantfile_path=get_basic_vagrantfile()
            ),
        ),
        max_messages=20,
    )


# ==============================================================================
# PYTEST TESTS WITH MOCKLLM
# ==============================================================================

@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_eval_cat_os_release():
    """
    Test using MockLLM to force 'cat /etc/os-release' command.
    Goes through Inspect's complete tool execution pipeline.
    """
    print("\n" + "="*70)
    print("EVAL TEST: cat /etc/os-release via Inspect + MockLLM")
    print("="*70)

    eval_task = cat_os_release()
    model_name = "mockllm/model"

    print(f"\n[TEST] Running eval")
    print(f"  Model: {model_name}")
    print(f"  Command: bash(cmd='cat /etc/os-release')")
    print(f"  Timeout: 60 seconds\n")

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                eval,
                eval_task,
                model=model_name,
                model_args={
                    "choices": [
                        # First: call bash tool with cat command
                        [
                            ChatMessageAssistant(
                                content="Checking OS version...",
                                tool_calls=[
                                    {
                                        "id": "call_1",
                                        "function": "bash",
                                        "arguments": {"cmd": "cat /etc/os-release"},
                                        "type": "function",
                                    }
                                ],
                            )
                        ],
                        # Second: provide answer
                        [
                            ChatMessageAssistant(
                                content="This is Ubuntu 22.04.5 LTS (Jammy Jellyfish).",
                            )
                        ],
                    ]
                },
            ),
            timeout=60.0,
        )

        print("\n" + "="*70)
        print("✓ EVAL COMPLETED")
        print("="*70)
        print(f"Status: {result[0].status}")
        print("\n✓ No hang detected - cat command completed")

        assert result[0].status == "success", "Eval should complete successfully"

    except asyncio.TimeoutError:
        print("\n" + "="*70)
        print("✗ EVAL TIMEOUT - BUG REPRODUCED")
        print("="*70)
        pytest.fail("cat /etc/os-release hung in Inspect framework")


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_eval_multiple_cats():
    """
    Test multiple cat commands in sequence using MockLLM.
    """
    print("\n" + "="*70)
    print("EVAL TEST: Multiple cat commands")
    print("="*70)

    eval_task = cat_multiple_files()
    model_name = "mockllm/model"

    print(f"\n[TEST] Running eval with 3 cat commands")

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                eval,
                eval_task,
                model=model_name,
                model_args={
                    "choices": [
                        # cat /etc/os-release
                        [ChatMessageAssistant(content="Checking OS...", tool_calls=[
                            {"id": "c1", "function": "bash", "arguments": {"cmd": "cat /etc/os-release"}, "type": "function"}
                        ])],
                        # cat /etc/hostname
                        [ChatMessageAssistant(content="Checking hostname...", tool_calls=[
                            {"id": "c2", "function": "bash", "arguments": {"cmd": "cat /etc/hostname"}, "type": "function"}
                        ])],
                        # cat /proc/cpuinfo
                        [ChatMessageAssistant(content="Checking CPU...", tool_calls=[
                            {"id": "c3", "function": "bash", "arguments": {"cmd": "cat /proc/cpuinfo"}, "type": "function"}
                        ])],
                        # Final answer
                        [ChatMessageAssistant(content="Ubuntu 22.04 system.")],
                    ]
                },
            ),
            timeout=120.0,
        )

        print("\n" + "="*70)
        print("✓ MULTI-CAT EVAL COMPLETED")
        print("="*70)
        print(f"Status: {result[0].status}")
        print("All 3 cat commands completed")

        assert result[0].status == "success"

    except asyncio.TimeoutError:
        print("\n" + "="*70)
        print("✗ TIMEOUT on multiple cats")
        print("="*70)
        pytest.fail("One of the cat commands hung")


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_eval_mixed_workflow():
    """
    Test cat interspersed with other bash commands (realistic scenario).
    """
    print("\n" + "="*70)
    print("EVAL TEST: Mixed workflow with cat")
    print("="*70)

    eval_task = cat_realistic_workflow()
    model_name = "mockllm/model"

    print(f"\n[TEST] Realistic workflow: 6 commands including 2 cats")

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                eval,
                eval_task,
                model=model_name,
                model_args={
                    "choices": [
                        [ChatMessageAssistant(content="Checking location...", tool_calls=[
                            {"id": "c1", "function": "bash", "arguments": {"cmd": "pwd"}, "type": "function"}
                        ])],
                        [ChatMessageAssistant(content="Checking user...", tool_calls=[
                            {"id": "c2", "function": "bash", "arguments": {"cmd": "whoami"}, "type": "function"}
                        ])],
                        [ChatMessageAssistant(content="Checking OS...", tool_calls=[
                            {"id": "c3", "function": "bash", "arguments": {"cmd": "cat /etc/os-release"}, "type": "function"}
                        ])],
                        [ChatMessageAssistant(content="Checking kernel...", tool_calls=[
                            {"id": "c4", "function": "bash", "arguments": {"cmd": "uname -a"}, "type": "function"}
                        ])],
                        [ChatMessageAssistant(content="Checking hostname...", tool_calls=[
                            {"id": "c5", "function": "bash", "arguments": {"cmd": "cat /etc/hostname"}, "type": "function"}
                        ])],
                        [ChatMessageAssistant(content="Ubuntu system, vagrant user.")],
                    ]
                },
            ),
            timeout=180.0,
        )

        print("\n" + "="*70)
        print("✓ WORKFLOW COMPLETED")
        print("="*70)
        print(f"Status: {result[0].status}")
        print("6 commands including 2 cats - all completed")

        assert result[0].status == "success"

    except asyncio.TimeoutError:
        print("\n" + "="*70)
        print("✗ TIMEOUT during workflow")
        print("="*70)
        pytest.fail("Workflow hung")


# ==============================================================================
# MAIN
# ==============================================================================

if __name__ == "__main__":
    print("\nTo run these tests:")
    print("\n# As pytest:")
    print("  pytest test/test_cat_hang_eval.py -v -s -m vm_required")
    print("\n# As standalone evals:")
    print("  inspect eval test/test_cat_hang_eval.py::cat_os_release --model mockllm/model")
    print("  inspect eval test/test_cat_hang_eval.py::cat_multiple_files --model mockllm/model")
    print("\n# With real model:")
    print("  inspect eval test/test_cat_hang_eval.py::cat_os_release --model openai/gpt-4")
