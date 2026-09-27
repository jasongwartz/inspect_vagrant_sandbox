from inspect_ai import Task, eval, task
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessageTool, ModelOutput, get_model
from inspect_ai.scorer import includes
from inspect_ai.solver import basic_agent
from inspect_ai.tool import bash

import asyncio
import shutil
import sys
import os
import pytest

from inspect_ai.util import SandboxEnvironmentSpec

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vagrantsandbox.vagrant_sandbox_provider import (
    Vagrant,
    VagrantSandboxEnvironment,
    VagrantSandboxEnvironmentConfig,
)

MULTI_VAGRANTFILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "Vagrantfile.multi"
)


@task
def multi_vm_task() -> Task:
    return Task(
        dataset=[
            Sample(
                input="Scan the target VM from the attacker VM",
                target="attacker",
            ),
        ],
        solver=[
            basic_agent(
                tools=[bash()],
                message_limit=5,
            ),
        ],
        scorer=includes(),
        sandbox=SandboxEnvironmentSpec(
            "vagrant",
            VagrantSandboxEnvironmentConfig(
                vagrantfile_path=(os.path.dirname(os.path.abspath(__file__)))
                + "/Vagrantfile.multi",
                # Note: primary_vm_name will be "attacker" + unique suffix at runtime
                primary_vm_name="attacker",
            ),
        ),
    )


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_multi_vm_name_discovery(tmp_path):
    """Regression test for issue #27: get_vm_names() must report every VM.

    Only needs 'vagrant status' - no VM is booted, so this is fast.
    """
    shutil.copy2(MULTI_VAGRANTFILE, tmp_path / "Vagrantfile")
    vagrant = Vagrant(
        root=str(tmp_path),
        env={**os.environ, "INSPECT_VM_SUFFIX": "-vmdisco"},
    )

    vm_names = await vagrant.get_vm_names()
    assert vm_names == ["target-vmdisco", "attacker-vmdisco"]


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_multi_vm_named_sandboxes_and_routing():
    """Boot a multi-VM Vagrantfile and verify per-VM sandbox routing.

    Guards against the issue #27 failure mode, where VM discovery silently
    returned [] and every multi-VM environment collapsed into a single
    unnamed sandbox. This test fails loudly if discovery regresses:
    - both named sandboxes must exist in the returned dict
    - primary_vm_name must select the "default" sandbox
    - commands must reach the specific VM they were addressed to
    """
    config = VagrantSandboxEnvironmentConfig(
        vagrantfile_path=MULTI_VAGRANTFILE,
        primary_vm_name="attacker",
    )
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "multi_vm_routing",
        config,
        {"sample_id": "multirt"},
    )

    try:
        # Both named sandboxes must exist, keyed by their Vagrantfile names
        assert "target" in sandboxes, f"Missing 'target' in {list(sandboxes)}"
        assert "attacker" in sandboxes, f"Missing 'attacker' in {list(sandboxes)}"

        # primary_vm_name="attacker" must select the attacker VM as "default"
        assert "default" in sandboxes
        assert sandboxes["default"] is sandboxes["attacker"]
        assert sandboxes["default"] is not sandboxes["target"]

        # Commands must reach the VM they were addressed to (each VM sets a
        # distinct hostname in Vagrantfile.multi)
        for name in ("target", "attacker"):
            result = await asyncio.wait_for(
                sandboxes[name].exec(["hostname"]), timeout=60.0
            )
            assert result.success, f"hostname failed on '{name}': {result.stderr}"
            assert result.stdout.strip() == name, (
                f"Command for sandbox '{name}' reached VM "
                f"'{result.stdout.strip()}' instead"
            )

        # The default sandbox routes to the primary (attacker) VM
        result = await asyncio.wait_for(
            sandboxes["default"].exec(["hostname"]), timeout=60.0
        )
        assert result.success
        assert result.stdout.strip() == "attacker"
    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "multi_vm_routing",
            config,
            sandboxes,
            interrupted=False,
        )


@pytest.mark.vm_required
@pytest.mark.inspect_eval
def test_multi_vm_config():
    """Test that multi-VM configuration works correctly."""
    eval_logs = eval(
        tasks=[multi_vm_task()],
        model=get_model(
            "mockllm/model",
            custom_outputs=[
                ModelOutput.for_tool_call(
                    model="mockllm/model",
                    tool_name="bash",
                    tool_arguments={"cmd": "hostname"},
                ),
                ModelOutput.for_tool_call(
                    model="mockllm/model",
                    tool_name="submit",
                    tool_arguments={"answer": "attacker"},
                ),
            ],
        ),
        log_level="trace",
    )

    assert len(eval_logs) == 1
    assert eval_logs[0]
    assert eval_logs[0].error is None
    assert eval_logs[0].status == "success"
    assert eval_logs[0].samples
    for sample in eval_logs[0].samples:
        assert sample.error is None, f"sample {sample.id} errored: {sample.error}"
    sample = eval_logs[0].samples[0]
    bash_outputs = [
        x
        for x in sample.messages
        if isinstance(x, ChatMessageTool) and x.function == "bash"
    ]
    assert bash_outputs, "no bash tool output recorded in sample messages"
    assert bash_outputs[0].error is None, (
        f"bash tool call failed: {bash_outputs[0].error}"
    )
    # `hostname` must have executed on the primary (attacker) VM
    assert bash_outputs[0].text.strip() == "attacker", (
        f"expected `hostname` output 'attacker', got: {bash_outputs[0].text!r}"
    )


if __name__ == "__main__":
    test_multi_vm_config()
