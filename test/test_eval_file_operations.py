"""
Inspect eval-based tests for file operations via bash tool.

These tests use Inspect's full framework with MockLLM to verify that
file reading operations work correctly through the complete tool execution
pipeline.

Run with: pytest test/test_eval_file_operations.py -v -s -m vm_required
Or: inspect eval test/test_eval_file_operations.py::task_name --model mockllm/model
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
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from vagrantsandbox.vagrant_sandbox_provider import (
    VagrantSandboxEnvironmentConfig,
)


def get_basic_vagrantfile():
    """Get path to Vagrantfile.basic."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "Vagrantfile.basic")


# ==============================================================================
# INSPECT TASK DEFINITIONS
# ==============================================================================


@task
def read_os_release():
    """Task requiring model to read /etc/os-release."""
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
            VagrantSandboxEnvironmentConfig(vagrantfile_path=get_basic_vagrantfile()),
        ),
        max_messages=5,
    )


@task
def read_multiple_system_files():
    """Task requiring multiple file read operations."""
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
            VagrantSandboxEnvironmentConfig(vagrantfile_path=get_basic_vagrantfile()),
        ),
        max_messages=15,
    )


@task
def system_exploration_workflow():
    """Realistic task where model explores the system."""
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
            VagrantSandboxEnvironmentConfig(vagrantfile_path=get_basic_vagrantfile()),
        ),
        max_messages=20,
    )


# ==============================================================================
# PYTEST TESTS WITH MOCKLLM
# ==============================================================================


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_eval_read_system_file():
    """Test reading system file via Inspect + MockLLM."""
    eval_task = read_os_release()
    model_name = "mockllm/model"

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
        timeout=180.0,
    )

    assert result[0].status == "success", "Eval should complete successfully"


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_eval_multiple_file_reads():
    """Test multiple file read operations in sequence using MockLLM."""
    eval_task = read_multiple_system_files()
    model_name = "mockllm/model"

    result = await asyncio.wait_for(
        asyncio.to_thread(
            eval,
            eval_task,
            model=model_name,
            model_args={
                "choices": [
                    # Read /etc/os-release
                    [
                        ChatMessageAssistant(
                            content="Checking OS...",
                            tool_calls=[
                                {
                                    "id": "c1",
                                    "function": "bash",
                                    "arguments": {"cmd": "cat /etc/os-release"},
                                    "type": "function",
                                }
                            ],
                        )
                    ],
                    # Read /etc/hostname
                    [
                        ChatMessageAssistant(
                            content="Checking hostname...",
                            tool_calls=[
                                {
                                    "id": "c2",
                                    "function": "bash",
                                    "arguments": {"cmd": "cat /etc/hostname"},
                                    "type": "function",
                                }
                            ],
                        )
                    ],
                    # Read /proc/cpuinfo
                    [
                        ChatMessageAssistant(
                            content="Checking CPU...",
                            tool_calls=[
                                {
                                    "id": "c3",
                                    "function": "bash",
                                    "arguments": {"cmd": "cat /proc/cpuinfo"},
                                    "type": "function",
                                }
                            ],
                        )
                    ],
                    # Final answer
                    [ChatMessageAssistant(content="Ubuntu 22.04 system.")],
                ]
            },
        ),
        timeout=120.0,
    )

    assert result[0].status == "success"


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_eval_mixed_command_workflow():
    """Test file reads interspersed with other bash commands."""
    eval_task = system_exploration_workflow()
    model_name = "mockllm/model"

    result = await asyncio.wait_for(
        asyncio.to_thread(
            eval,
            eval_task,
            model=model_name,
            model_args={
                "choices": [
                    [
                        ChatMessageAssistant(
                            content="Checking location...",
                            tool_calls=[
                                {
                                    "id": "c1",
                                    "function": "bash",
                                    "arguments": {"cmd": "pwd"},
                                    "type": "function",
                                }
                            ],
                        )
                    ],
                    [
                        ChatMessageAssistant(
                            content="Checking user...",
                            tool_calls=[
                                {
                                    "id": "c2",
                                    "function": "bash",
                                    "arguments": {"cmd": "whoami"},
                                    "type": "function",
                                }
                            ],
                        )
                    ],
                    [
                        ChatMessageAssistant(
                            content="Checking OS...",
                            tool_calls=[
                                {
                                    "id": "c3",
                                    "function": "bash",
                                    "arguments": {"cmd": "cat /etc/os-release"},
                                    "type": "function",
                                }
                            ],
                        )
                    ],
                    [
                        ChatMessageAssistant(
                            content="Checking kernel...",
                            tool_calls=[
                                {
                                    "id": "c4",
                                    "function": "bash",
                                    "arguments": {"cmd": "uname -a"},
                                    "type": "function",
                                }
                            ],
                        )
                    ],
                    [
                        ChatMessageAssistant(
                            content="Checking hostname...",
                            tool_calls=[
                                {
                                    "id": "c5",
                                    "function": "bash",
                                    "arguments": {"cmd": "cat /etc/hostname"},
                                    "type": "function",
                                }
                            ],
                        )
                    ],
                    [ChatMessageAssistant(content="Ubuntu system, vagrant user.")],
                ]
            },
        ),
        timeout=180.0,
    )

    assert result[0].status == "success"


# ==============================================================================
# MAIN
# ==============================================================================

if __name__ == "__main__":
    print("\nTo run these tests:")
    print("\n# As pytest:")
    print("  pytest test/test_eval_file_operations.py -v -s -m vm_required")
    print("\n# As standalone evals:")
    print(
        "  inspect eval test/test_eval_file_operations.py::read_os_release --model mockllm/model"
    )
    print(
        "  inspect eval test/test_eval_file_operations.py::read_multiple_system_files --model mockllm/model"
    )
    print("\n# With real model:")
    print(
        "  inspect eval test/test_eval_file_operations.py::read_os_release --model openai/gpt-4"
    )
