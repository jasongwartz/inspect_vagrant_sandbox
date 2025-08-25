import asyncio
from os import getenv
from pathlib import Path
import shutil
from typing import Any, override

import aiofiles  # type: ignore
from inspect_ai.util import (
    ExecResult,
    SandboxConnection,
    SandboxEnvironment,
    sandboxenv,
    SandboxEnvironmentConfigType,
    trace_action,
)

from logging import getLogger

from pydantic import BaseModel, Field

from vagrantsandbox._async_vagrant import AsyncVagrant


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


@sandboxenv(name="vagrant")
class VagrantSandboxEnvironment(SandboxEnvironment):
    logger = getLogger(__name__)

    TRACE_NAME = "vagrant_sandbox_environment"

    vagrant: AsyncVagrant

    def __init__(
        self,
        tmpdir_context: aiofiles.tempfile.AiofilesContextManagerTempDir,
        vagrant: AsyncVagrant,
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

        vagrant = AsyncVagrant(tmpdir)

        await vagrant.up()

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
                    await env.vagrant.destroy()
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
            vagrant = AsyncVagrant()
            await vagrant.destroy()
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

        with trace_action(
            self.logger,
            self.TRACE_NAME,
            # f"exec_command {self.vm_id=} {exec_response_pid=}",
            "exec_command ",
        ):
            returncode, stdout, stderr = await self.vagrant.ssh_command(" ".join(cmd))

            return ExecResult(
                success=returncode == 0,
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            )

    @override
    async def write_file(self, file: str, contents: str | bytes) -> None:
        await self.vagrant.ssh_command(["echo", contents, ">", file])

    @override
    async def read_file(self, file: str, text: bool = True) -> str | bytes:  # type: ignore
        returncode, stdout, stderr = await self.vagrant.ssh_command(f"cat f{file}")
        return stdout
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
