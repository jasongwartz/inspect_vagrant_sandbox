from inspect_ai import Task, eval, task
from inspect_ai.dataset import Sample
from inspect_ai.model import ModelOutput, get_model
from inspect_ai.scorer import includes
from inspect_ai.solver import basic_agent
from inspect_ai.tool import bash

import sys
import os
import pytest

from inspect_ai.util import SandboxEnvironmentSpec

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vagrantsandbox.vagrant_sandbox_provider import (
    VagrantSandboxEnvironmentConfig,
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


if __name__ == "__main__":
    test_multi_vm_config()
