import asyncio
import functools
import itertools
from os import getenv
from pathlib import Path
import shlex
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
    logger = getLogger(__name__)

    async def get_vm_names(self) -> list[str]:
        """Get list of VM names defined in the Vagrantfile."""
        try:
            # Use python-vagrant's built-in status method
            status_info = await _run_in_executor(self.status)
            vm_names = [vm["name"] for vm in status_info]
            self.logger.debug(f"get_vm_names status_info: {status_info}")
            self.logger.debug(f"get_vm_names extracted names: {vm_names}")
            return vm_names
        except Exception as e:
            self.logger.debug(f"get_vm_names failed: {e}")
            return []

    async def _run_vagrant_command_async(self, args) -> ExecCommandReturn:
        """
        Run a vagrant command and return everything, not just stdout.

        args: A sequence of arguments to a vagrant command line.
        e.g. ['up', 'my_vm_name', '--no-provision'] or
        ['up', None, '--no-provision'] for a non-Multi-VM environment.
        """
        # Make subprocess command
        command = self._make_vagrant_command(args)
        self.logger.debug(f"Vagrant command: {command}")
        self.logger.debug(f"Working directory: {self.root}")
        self.logger.debug(
            f"Environment variables: {dict(self.env) if self.env else 'None'}"
        )

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
        cmd = ["ssh", vm_name, "--no-tty", "--command", command]
        if extra_ssh_args is not None:
            cmd += ["--", extra_ssh_args]

        return self._run_vagrant_command_async(cmd)


T = TypeVar("T")


class VagrantSandboxEnvironmentConfig(BaseModel, frozen=True):
    vagrantfile_path: str = Field(
        default_factory=lambda: getenv("VAGRANTFILE_PATH", "./Vagrantfile")
    )
    primary_vm_name: str | None = Field(
        default=None,
        description="Name of the VM to use as the 'default' sandbox environment. If None, uses first available VM.",
    )


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
        vm_name: str | None = None,
    ):
        self.vagrant = vagrant
        self.tmpdir_context = tmpdir_context
        self.vm_name = vm_name

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

        # Create unique suffix from sample metadata to avoid VM name conflicts
        sample_id = metadata.get("sample_id", "unknown")
        unique_suffix = (
            f"-{sample_id[:8]}"
            if sample_id != "unknown"
            else f"-{hash(tmpdir) % 10000:04d}"
        )
        cls.logger.info(f"Using unique VM suffix: {unique_suffix}")

        # Set environment variable for Vagrantfile to use
        import os

        env = os.environ.copy()
        env["INSPECT_VM_SUFFIX"] = unique_suffix

        vagrant = Vagrant(root=tmpdir, env=env)

        # Get available VMs before starting them
        try:
            vm_names = await vagrant.get_vm_names()
            cls.logger.info(f"Discovered VMs in Vagrantfile: {vm_names}")
        except Exception as e:
            cls.logger.error(
                f"Failed to get VM names: {e}. Assuming single-VM Vagrantfile."
            )
            vm_names = []

        # If no VMs found, assume single-VM Vagrantfile
        if not vm_names:
            cls.logger.info("No VMs discovered, assuming single-VM Vagrantfile")
            vm_names = [None]  # None means default/single VM

        try:
            # Start all VMs
            cls.logger.info(f"Starting VMs: {vm_names}")
            cls.logger.info(f"Vagrant working directory: {tmpdir}")
            cls.logger.info(
                f"Environment variables: INSPECT_VM_SUFFIX={env.get('INSPECT_VM_SUFFIX')}"
            )

            # Log the Vagrantfile content for debugging
            vagrantfile_path = Path(tmpdir) / "Vagrantfile"
            try:
                with open(vagrantfile_path, "r") as f:
                    vagrantfile_content = f.read()
                    cls.logger.debug(f"Vagrantfile contents:\n{vagrantfile_content}")
            except Exception as read_error:
                cls.logger.error(f"Could not read Vagrantfile: {read_error}")

            # First check current status before trying to start
            try:
                initial_status = await vagrant._run_vagrant_command_async(["status"])
                cls.logger.info(f"Initial VM status: {initial_status['stdout']}")
            except Exception as status_error:
                cls.logger.info(
                    f"Could not get initial status (this is normal for new VMs): {status_error}"
                )

            # Use our async method to capture stdout/stderr on failure
            up_result = await vagrant._run_vagrant_command_async(["up"])
            cls.logger.info("All VMs started successfully")
            if up_result["stdout"]:
                cls.logger.debug(f"Vagrant up stdout: {up_result['stdout']}")
            if up_result["stderr"]:
                cls.logger.debug(f"Vagrant up stderr: {up_result['stderr']}")

            # Check if command actually succeeded
            if up_result["returncode"] != 0:
                error = subprocess.CalledProcessError(
                    up_result["returncode"], ["vagrant", "up"]
                )
                error.stdout = up_result["stdout"]
                error.stderr = up_result["stderr"]
                raise error
        except subprocess.CalledProcessError as e:
            cls.logger.error(f"Failed to start VMs. Return code: {e.returncode}")
            cls.logger.error(f"Command that failed: {e.cmd}")

            # Display captured output
            if hasattr(e, "stdout") and e.stdout:
                cls.logger.error(f"Vagrant stdout: {e.stdout}")
            if hasattr(e, "stderr") and e.stderr:
                cls.logger.error(f"Vagrant stderr: {e.stderr}")

            # Try to get more info with vagrant status and logs
            try:
                status_result = await vagrant._run_vagrant_command_async(["status"])
                cls.logger.error(f"Post-failure VM status: {status_result['stdout']}")
                if status_result["stderr"]:
                    cls.logger.error(
                        f"Post-failure status stderr: {status_result['stderr']}"
                    )
            except Exception as status_error:
                cls.logger.error(f"Could not get post-failure status: {status_error}")

            # Try to get vagrant global-status to see if there are conflicting VMs
            try:
                global_status = await vagrant._run_vagrant_command_async(
                    ["global-status"]
                )
                cls.logger.error(f"Global VM status: {global_status['stdout']}")
            except Exception as global_error:
                cls.logger.error(f"Could not get global status: {global_error}")

            raise e

        sandboxes: dict[str, SandboxEnvironment] = {}

        # Determine which VM should be the default
        # The primary_vm_name from config needs to be matched with the actual VM names (which include suffix)
        primary_vm_base = config.primary_vm_name
        primary_vm = None

        if primary_vm_base:
            # Find VM that starts with the base name (handles suffix)
            for vm_name in vm_names:
                if vm_name and vm_name.startswith(primary_vm_base):
                    primary_vm = vm_name
                    break

            if not primary_vm:
                available_vms = [vm for vm in vm_names if vm is not None]
                cls.logger.warning(
                    f"Primary VM starting with '{primary_vm_base}' not found. "
                    f"Available VMs: {available_vms}. Using first available VM."
                )
                primary_vm = vm_names[0] if vm_names else None
        else:
            primary_vm = vm_names[0] if vm_names else None

        # Create sandbox environments for each VM
        cls.logger.info(f"Creating sandbox environments. Primary VM: {primary_vm}")
        for vm_name in vm_names:
            env = VagrantSandboxEnvironment(tmpdir_context, vagrant, vm_name)
            cls.logger.debug(f"Created environment for VM: {vm_name}")

            # The primary VM becomes "default"
            if vm_name == primary_vm:
                sandboxes["default"] = env
                cls.logger.info(f"Set '{vm_name}' as default sandbox environment")

            # Also add by VM name if it's not None (multi-VM case)
            if vm_name is not None:
                sandboxes[vm_name] = env

        # Ensure we always have a "default" sandbox
        if "default" not in sandboxes and sandboxes:
            first_key = next(iter(sandboxes))
            sandboxes["default"] = sandboxes[first_key]

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
            cls.logger.warning("Task cleanup not implemented yet")
            # TODO:
            # Figure out how to clean up instances
        else:
            cls.logger.info(
                "Cleanup all sandbox releases with: inspect sandbox cleanup vagrant"
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
            cls.logger.warning("Cleanup by ID not implemented")

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
            result = await self.vagrant.ssh(vm_name=self.vm_name, command=command)

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

        command = f"printf %s {shlex.quote(contents_str)} > {shlex.quote(file)}"
        result = await self.vagrant.ssh(vm_name=self.vm_name, command=command)
        if result["returncode"] != 0:
            raise subprocess.CalledProcessError(
                result["returncode"], command, result["stdout"]
            )

    @override
    async def read_file(self, file: str, text: bool = True) -> str | bytes:  # type: ignore
        command = f"cat {file}"
        result = await self.vagrant.ssh(vm_name=self.vm_name, command=command)
        if result["returncode"] != 0:
            raise subprocess.CalledProcessError(
                result["returncode"], command, result["stdout"]
            )

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
        tmpdir = self.tmpdir_context.__str__()
        return SandboxConnection(
            type="vagrant",
            command=f"VAGRANT_CWD={tmpdir} vagrant ssh",
        )
