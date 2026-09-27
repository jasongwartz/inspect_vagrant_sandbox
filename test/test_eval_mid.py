from inspect_ai import Task, eval, task
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessageTool, ModelOutput, get_model
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
)  # noqa: F401


@task
def task_for_test() -> Task:
    return Task(
        dataset=[
            Sample(
                input="sample text",
                target="42",
            ),
        ],
        solver=[
            basic_agent(
                tools=[bash()],
                message_limit=20,
            ),
        ],
        scorer=includes(),
        # sandbox="vagrant",
        sandbox=SandboxEnvironmentSpec(
            "vagrant",
            VagrantSandboxEnvironmentConfig(
                vagrantfile_path=(os.path.dirname(os.path.abspath(__file__)))
                + "/Vagrantfile.basic"
            ),
        ),
    )


@pytest.mark.vm_required
@pytest.mark.inspect_eval
def test_inspect_eval() -> None:
    eval_logs = eval(
        tasks=[task_for_test()],
        model=get_model(
            "mockllm/model",
            custom_outputs=[
                ModelOutput.for_tool_call(
                    model="mockllm/model",
                    tool_name="bash",
                    tool_arguments={"cmd": "uname -a"},
                    # Extra quotes no longer needed: shlex.join() now handles
                    # shell escaping (previously ' '.join() required manual quoting)
                ),
                ModelOutput.for_tool_call(
                    model="mockllm/model",
                    tool_name="submit",
                    tool_arguments={"answer": "42"},
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
    # `uname -a` must have actually executed in the sandbox VM
    assert "ubuntu" in bash_outputs[0].text


if __name__ == "__main__":
    test_inspect_eval()
