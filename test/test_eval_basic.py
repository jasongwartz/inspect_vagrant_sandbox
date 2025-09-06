from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.solver import basic_agent
from inspect_ai.tool import bash
from inspect_ai.util import SandboxEnvironmentSpec

from vagrantsandbox.vagrant_sandbox_provider import VagrantSandboxEnvironmentConfig


@task
def my_vagrant_task() -> Task:
    return Task(
        dataset=[
            Sample(
                input="What operating system is running?",
                target="ubuntu",
            ),
        ],
        solver=[
            basic_agent(
                tools=[bash()],
                message_limit=50,
            ),
        ],
        sandbox=SandboxEnvironmentSpec(
            "vagrant",
            VagrantSandboxEnvironmentConfig(
                vagrantfile_path="./test/Vagrantfile.basic"
            ),
        ),
    )
