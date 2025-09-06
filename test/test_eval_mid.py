from inspect_ai import Task, eval, task
from inspect_ai.dataset import Sample
from inspect_ai.model import ModelOutput, get_model
from inspect_ai.scorer import includes
from inspect_ai.solver import basic_agent
from inspect_ai.tool import bash

import sys
import os

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


def test_inspect_eval() -> None:
    eval_logs = eval(
        tasks=[task_for_test()],
        model=get_model(
            "mockllm/model",
            custom_outputs=[
                ModelOutput.for_tool_call(
                    model="mockllm/model",
                    tool_name="bash",
                    tool_arguments={"cmd": "'uname -a'"},
                    # TODO: check if models do the quoting I added
                    # (was "uname -a" (without extra single quotes) in the Proxmox provider)
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
    assert eval_logs[0].samples
    sample = eval_logs[0].samples[0]
    tool_calls = [x for x in sample.messages if x.role == "tool"]
    print(tool_calls)
    assert "ubuntu" in tool_calls[0].text


if __name__ == "__main__":
    test_inspect_eval()
