import asyncio
import functools
import itertools
from os import getenv
from pathlib import Path
import shutil
import subprocess
from typing import Any, Callable, TypeVar, TypedDict, assert_never, override
from logging import getLogger
from vagrant import Vagrant as BaseVagrant
from pydantic import BaseModel, Field

import aiofiles  # type: ignore
from inspect_ai.util import (
    ExecResult,
    SandboxConnection,
    SandboxEnvironment,
    sandboxenv,
    SandboxEnvironmentConfigType,
    trace_action,
)


class ExecCommandReturn(TypedDict):
    returncode: int
    stdout: str
    stderr: str


class Vagrant(BaseVagrant):
    async def _run_vagrant_command_async(self, args) -> ExecCommandReturn:
        """
        Run a vagrant command and return everything, not just stdout.

        args: A sequence of arguments to a vagrant command line.
        e.g. ['up', 'my_vm_name', '--no-provision'] or
        ['up', None, '--no-provision'] for a non-Multi-VM environment.
        """
        # Make subprocess command
        command = self._make_vagrant_command(args)
        print("VAGRANTCOMmAND", command)
        print("VAGRANTC12", *command)

        result = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.root,
            env=self.env,
        )

        # Wait for process to complete and capture output
        stdout, stderr = await result.communicate()

        assert result.returncode is not None, (
            "returncode should be set after communicate()"
        )

        # Decode bytes to string
        stdout_str = stdout.decode("utf-8") if stdout else ""
        stderr_str = stderr.decode("utf-8") if stderr else ""

        return {
            "stdout": stdout_str,
            "stderr": stderr_str,
            "returncode": result.returncode,
        }

    @override
    def ssh(self, vm_name=None, command=None, extra_ssh_args=None):
        """
        Execute a command via ssh on the vm specified.
        command: The command to execute via ssh.
        extra_ssh_args: Corresponds to '--' option in the vagrant ssh command
        Returns the output of running the command.
        """
        cmd = ["ssh", vm_name, "--command", command]
        if extra_ssh_args is not None:
            cmd += ["--", extra_ssh_args]

        return self._run_vagrant_command_async(cmd)


T = TypeVar("T")


class VagrantSandboxEnvironmentConfig(BaseModel, frozen=True):
    vagrantfile_path: str = Field(
        default_factory=lambda: getenv("VAGRANTFILE_PATH", "./Vagrantfile")
    )
    # port: int = Field(default_factory=lambda: int(getenv("PROXMOX_PORT", "8006")))
    # user: str = Field(default_factory=lambda: getenv("PROXMOX_USER", "root"))
    # user_realm: str = Field(default_factory=lambda: getenv("PROXMOX_REALM", "pam"))
    # password: str = Field(
    #     default_factory=lambda: getenv("PROXMOX_PASSWORD", "password")
    # )
    # node: str = Field(default_factory=lambda: getenv("PROXMOX_NODE", "proxmox"))
    # verify_tls: bool = Field(
    #     default_factory=lambda: getenv("PROXMOX_VERIFY_TLS", "1") == "1"
    # )

    # @classmethod
    # def config_files(cls) -> list[str]:
    #     ...

    # @classmethod
    # def default_concurrency(cls) -> int | None:
    #     ...


async def _run_in_executor(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a function in the thread pool executor."""
    # TODO: is it necessary to allow passing a custom executor?
    return await asyncio.get_event_loop().run_in_executor(
        None, functools.partial(func, *args, **kwargs)
    )


@sandboxenv(name="vagrant")
class VagrantSandboxEnvironment(SandboxEnvironment):
    logger = getLogger(__name__)

    TRACE_NAME = "vagrant_sandbox_environment"

    vagrant: Vagrant

    def __init__(
        self,
        tmpdir_context: aiofiles.tempfile.AiofilesContextManagerTempDir,
        vagrant: Vagrant,
    ):
        self.vagrant = vagrant
        self.tmpdir_context = tmpdir_context

    @classmethod
    async def task_init(
        cls, task_name: str, config: SandboxEnvironmentConfigType | None
    ) -> None:
        if config is not None:
            if not isinstance(config, VagrantSandboxEnvironmentConfig):
                raise ValueError("config must be a VagrantSandboxEnvironmentConfig")
            # async_proxmox_api = cls._create_async_proxmox_api(config)
            # await ProxmoxSandboxEnvironment.ensure_vms(async_proxmox_api, config)
        return None

    @classmethod
    @override
    async def sample_init(
        cls,
        task_name: str,
        config: SandboxEnvironmentConfigType | None,
        metadata: dict[str, str],
    ) -> dict[str, SandboxEnvironment]:
        config = config or VagrantSandboxEnvironmentConfig()
        assert isinstance(config, VagrantSandboxEnvironmentConfig)

        tmpdir_context = aiofiles.tempfile.TemporaryDirectory()
        tmpdir = await tmpdir_context.__aenter__()
        await asyncio.to_thread(
            shutil.copy2,
            config.vagrantfile_path,
            (Path(tmpdir) / "Vagrantfile").as_posix(),
        )

        vagrant = Vagrant(root=tmpdir)

        try:
            await _run_in_executor(vagrant.up)
        except subprocess.CalledProcessError as e:
            cls.logger.error(e.stderr)
            raise e

        sandboxes: dict[str, SandboxEnvironment] = {}
        vagrant_sandbox_environment = VagrantSandboxEnvironment(tmpdir_context, vagrant)
        sandboxes["default"] = vagrant_sandbox_environment

        # borrowed from k8s provider
        def reorder_default_first(
            sandboxes: dict[str, SandboxEnvironment],
        ) -> dict[str, SandboxEnvironment]:
            # Inspect expects the default sandbox to be the first sandbox in the dict.
            if "default" in sandboxes:
                default = sandboxes.pop("default")
                return {"default": default, **sandboxes}
            return sandboxes

        return reorder_default_first(sandboxes)

    @classmethod
    @override
    async def sample_cleanup(
        cls,
        task_name: str,
        config: SandboxEnvironmentConfigType | None,
        environments: dict[str, SandboxEnvironment],
        interrupted: bool,
    ) -> None:
        if not interrupted:
            for env in environments.values():
                if isinstance(env, VagrantSandboxEnvironment):
                    # TODO: teardown group if more than one?
                    await _run_in_executor(env.vagrant.destroy)
                    await env.tmpdir_context.__aexit__(None, None, None)
        return None

    @classmethod
    @override
    async def task_cleanup(
        cls,
        task_name: str,
        config: SandboxEnvironmentConfigType | None,
        cleanup: bool,
    ) -> None:
        if config is None:
            config = VagrantSandboxEnvironmentConfig()

        if not isinstance(config, VagrantSandboxEnvironmentConfig):
            raise ValueError("config must be a VagrantSandboxEnvironmentConfig")

        if cleanup:
            print("NOT IMPLEMENTED!")
            # TODO:
            # Figure out how to clean up instances
        else:
            print(
                "\nCleanup all sandbox releases with: "
                "[blue]inspect sandbox cleanup vagrant[/blue]\n"
            )

    @classmethod
    @override
    async def cli_cleanup(cls, id: str | None) -> None:
        if id is None:
            config = VagrantSandboxEnvironmentConfig()
            vagrant = Vagrant()
            await _run_in_executor(vagrant.destroy)
            # TODO: is this right?
        else:
            print("\n[red]Cleanup by ID not implemented[/red]\n")

    @classmethod
    @override
    def config_deserialize(cls, config: dict[str, Any]) -> BaseModel:
        return VagrantSandboxEnvironmentConfig(**config)

    @override
    async def exec(
        self,
        cmd: list[str],
        input: str | bytes | None = None,
        cwd: str | None = None,
        env: dict[str, str] = {},
        user: str | None = None,
        timeout: int | None = None,
        timeout_retry: bool = True,
    ) -> ExecResult[str]:
        # tmp_start = f"/tmp/{__name__}{time.time_ns()}_"

        # @tenacity.retry(
        #     wait=tenacity.wait_exponential(min=0.1, exp_base=1.3),
        #     stop=tenacity.stop_after_delay(timeout)
        #     if timeout is not None
        #     else tenacity.stop_never,
        #     retry=tenacity.retry_if_result(lambda x: x is False),
        # )

        command = " ".join(
            itertools.chain.from_iterable(
                item.split() if isinstance(item, str) else item for item in cmd
            )
        )

        with trace_action(
            self.logger,
            self.TRACE_NAME,
            # f"exec_command {self.vm_id=} {exec_response_pid=}",
            "exec_command ",
        ):
            result = await self.vagrant.ssh(command=command)

            return ExecResult(
                success=result["returncode"] == 0,
                returncode=result["returncode"],
                stdout=result["stdout"],
                stderr=result["stderr"],
            )

    @override
    async def write_file(self, file: str, contents: str | bytes) -> None:
        contents_str: str
        if isinstance(contents, bytes):
            contents_str = contents.decode()
        elif isinstance(contents, str):
            contents_str = contents
        else:
            assert_never(contents)  # type: ignore[arg-type]

        command = " ".join(
                [
                    "echo",
                    contents_str,
                    ">",
                    file,
                ]
            ),
        result = await self.vagrant.ssh(
            command=command
        )
        if result["returncode"] != 0:
            raise subprocess.CalledProcessError(result["returncode"], command, result["stdout"])


    @override
    async def read_file(self, file: str, text: bool = True) -> str | bytes:  # type: ignore
        command = f"cat f{file}"
        result = await self.vagrant.ssh(command=command)
        if result["returncode"] != 0:
            raise subprocess.CalledProcessError(result["returncode"], command, result["stdout"])

        return result["stdout"]
        # type-ignore is because Mypy complains that this override doesn't implement bytes return

    @override
    async def connection(self, *, user: str | None = None) -> SandboxConnection:
        """Information required to connect to sandbox environment.

        Args:
          user: User to login as.

        Returns:
           SandboxConnection: connection information.

        Raises:
           NotImplementedError: For sandboxes that don't provide connections
           ConnectionError: If sandbox is not currently running.
        """
        """
        Returns a connection to the sandbox.

        Raises:
           NotImplementedError: For sandboxes that don't provide connections
           ConnectionError: If sandbox is not currently running.
        """
        return SandboxConnection(type="vagrant", command="vagrant ssh")
