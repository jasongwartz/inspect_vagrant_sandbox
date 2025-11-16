from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import model_graded_qa
from inspect_ai.solver import basic_agent
from inspect_ai.tool import bash
from inspect_ai.util import SandboxEnvironmentSpec

from vagrantsandbox.vagrant_sandbox_provider import VagrantSandboxEnvironmentConfig


@task
def vagrant_basic() -> Task:
    return Task(
        dataset=[
            Sample(
                input="What linux distribution is this?",
                target="ubuntu",
                sandbox=SandboxEnvironmentSpec(
                    "vagrant",
                    VagrantSandboxEnvironmentConfig(vagrantfile_path=vagrantfile),
                ),
            )
            for vagrantfile in [
                "./test/Vagrantfile.basic",
                "./test/Vagrantfile.docker_box",
            ]
        ],
        solver=[
            basic_agent(
                tools=[bash()],
                message_limit=50,
            ),
        ],
        scorer=model_graded_qa(),
    )
