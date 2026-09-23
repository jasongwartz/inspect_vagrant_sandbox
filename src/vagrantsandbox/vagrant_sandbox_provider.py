import asyncio
import base64
import errno
import os
import re
import shlex
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from logging import getLogger
from os import getenv
from pathlib import Path
from typing import (
    Any,
    Callable,
    Coroutine,
    Final,
    Literal,
    TypedDict,
    TypeVar,
    assert_never,
    overload,
    override,
)

from contextlib import nullcontext
from typing import AsyncContextManager

from inspect_ai.util import (
    ExecResult,
    OutputLimitExceededError,
    SandboxConnection,
    SandboxEnvironment,
    SandboxEnvironmentConfigType,
    SandboxEnvironmentLimits,
    concurrency,
    sandboxenv,
    trace_action,
)
from inspect_ai.util._subprocess import default_max_subprocesses
from platformdirs import user_cache_dir
from pydantic import BaseModel, Field, field_validator
from vagrant import Status, Vagrant as BaseVagrant


def _get_max_vagrant_startups() -> int | None:
    """Get the maximum number of concurrent vagrant up operations.

    Returns None if not configured (rely on Inspect's sandbox concurrency).
    """
    env_value = os.environ.get("INSPECT_MAX_VAGRANT_STARTUPS")
    if env_value is not None:
        return int(env_value)
    return None


def _startup_semaphore() -> AsyncContextManager[None]:
    """Limit concurrent vagrant up operations.

    Vagrant up is resource-intensive (disk I/O, CPU, memory allocation).
    Running too many in parallel can overwhelm the system and cause failures.
    This semaphore limits concurrency to prevent resource exhaustion.

    Configure via INSPECT_MAX_VAGRANT_STARTUPS. If not set, relies on
    Inspect's sandbox concurrency (--max-sandboxes or sample concurrency).
    """
    max_startups = _get_max_vagrant_startups()
    if max_startups is None:
        return nullcontext()
    return concurrency("vagrant-startup", max_startups)


class SandboxUnrecoverableError(Exception):
    """Raised when the sandbox enters an unrecoverable state.

    This exception indicates that the sandbox cannot continue to operate
    reliably, for example when a process cannot be terminated even after
    SIGKILL. Unlike TimeoutError (which allows the sample to continue),
    this exception will cause the sample to fail.
    """

    pass


@dataclass
class TimeoutConfig:
    timeout: float
    terminate_grace: float = 5.0
    kill_grace: float = 5.0


# This value will be used to create directories like eg.
# `~/.cache/inspect-vagrant-sandbox/...` or equivalent on other
# operating systems.
SANDBOX_VAGRANTFILE_CONFIG_DIRECTORY_NAME = "inspect-vagrant-sandbox"


def get_sandbox_cache_dir() -> Path:
    """Get the base cache directory for vagrant sandboxes.

    The cache directory can be customized via environment variables:
    - INSPECT_SANDBOX_CACHE_DIR: Override the entire cache directory path
    - INSPECT_SANDBOX_CACHE_SUFFIX: Append a subdirectory to the default cache path
      (useful for isolating parallel test workers or CI environments)
    """
    # Allow complete override of cache directory
    custom_dir = os.environ.get("INSPECT_SANDBOX_CACHE_DIR")
    if custom_dir:
        return Path(custom_dir)

    base_dir = Path(user_cache_dir(SANDBOX_VAGRANTFILE_CONFIG_DIRECTORY_NAME))

    # Allow appending a suffix for isolation (e.g., parallel test workers)
    suffix = os.environ.get("INSPECT_SANDBOX_CACHE_SUFFIX")
    if suffix:
        return base_dir / suffix

    return base_dir


class SandboxDirectory:
    """
    Manages sandbox directories stored in user cache.

    Unlike TemporaryDirectory, these persist until explicitly cleaned up,
    making them easier to locate and manage.
    """

    logger = getLogger(__name__)

    def __init__(self, path: Path):
        self.path = path

    @classmethod
    async def create(cls, sample_id: str | None = None) -> "SandboxDirectory":
        """Create a new sandbox directory in user cache."""
        base_dir = get_sandbox_cache_dir()
        await asyncio.to_thread(base_dir.mkdir, parents=True, exist_ok=True)

        # Create unique subdirectory name. The sample_id is user-controlled
        # (it comes from the sample's metadata), and the name is used both as
        # a directory name and as the VM name suffix, so strip anything
        # filesystem- or hostname-hostile (e.g. "/", spaces).
        short_uuid = uuid.uuid4().hex[:8]
        if sample_id and sample_id != "unknown":
            safe_id = re.sub(r"[^A-Za-z0-9_-]", "-", sample_id)
            subdir_name = f"{safe_id[:8]}-{short_uuid}"
        else:
            subdir_name = short_uuid

        path = base_dir / subdir_name
        await asyncio.to_thread(path.mkdir, exist_ok=True)

        cls.logger.debug(f"Created sandbox directory: {path}")
        return cls(path)

    async def cleanup(self) -> None:
        """Remove the sandbox directory."""
        if self.path.exists():
            await asyncio.to_thread(shutil.rmtree, self.path)
            self.logger.debug(f"Cleaned up sandbox directory: {self.path}")
        else:
            self.logger.warning(
                f"Unable to clean up sandbox directory, does not exist: {self.path}"
            )

    def __str__(self) -> str:
        return str(self.path)


async def destroy_sandbox_vms(sandbox_path: Path) -> None:
    """Destroy any Vagrant VMs in a sandbox directory."""
    logger = getLogger(__name__)
    vagrant_dir = sandbox_path / ".vagrant"

    if not vagrant_dir.exists():
        logger.debug(f"No .vagrant directory in {sandbox_path}, skipping destroy")
        return

    logger.info(f"Destroying VMs in {sandbox_path}")
    vagrant = Vagrant(root=str(sandbox_path))
    result = await vagrant._run_vagrant_command_async(["destroy", "-f"])
    if result["returncode"] != 0:
        logger.warning(
            f"vagrant destroy returned {result['returncode']}: {result['stderr']}"
        )


def list_sandbox_directories() -> list[Path]:
    """List all sandbox directories in the cache."""
    base_dir = get_sandbox_cache_dir()
    if not base_dir.exists():
        return []
    return [p for p in base_dir.iterdir() if p.is_dir()]


def cleanup_sandbox_directory(path: Path) -> None:
    """Remove a specific sandbox directory."""
    logger = getLogger(__name__)

    if not path.exists():
        return

    if not path.is_dir():
        raise ValueError(f"Path is not a directory: {path}")

    # Safety check: only delete if it's under our cache directory
    base_dir = get_sandbox_cache_dir()
    if base_dir not in path.parents and path.parent != base_dir:
        raise ValueError(f"Refusing to delete directory outside cache: {path}")

    shutil.rmtree(path)
    logger.info(f"Cleaned up sandbox directory: {path}")


async def cleanup_sandbox_with_vms(path: Path) -> None:
    """Destroy VMs and remove a sandbox directory."""
    await destroy_sandbox_vms(path)
    await asyncio.to_thread(cleanup_sandbox_directory, path)


class ExecCommandReturn(TypedDict):
    returncode: int
    stdout: str
    stderr: str


class Vagrant(BaseVagrant):
    logger = getLogger(__name__)

    async def get_vm_names(self) -> list[str]:
        """Get list of VM names defined in the Vagrantfile.

        python-vagrant's ``status()`` returns a list of ``Status`` namedtuples
        with fields ``(name, state, provider)`` - one per machine defined in
        the Vagrantfile, whether or not it has been created yet.
        """
        try:
            # Use python-vagrant's built-in status method
            status_info: list[Status] = await _run_in_executor(self.status)
        except (subprocess.SubprocessError, OSError) as e:
            self.logger.warning(
                f"'vagrant status' failed while discovering VM names: {e}. "
                "Falling back to single-VM mode."
            )
            return []
        vm_names: list[str] = [vm.name for vm in status_info]
        self.logger.debug(f"get_vm_names status_info: {status_info}")
        self.logger.debug(f"get_vm_names extracted names: {vm_names}")
        return vm_names

    async def _run_vagrant_command_async(
        self,
        args: list[str | None],
        input: str | bytes | None = None,
        timeout: int | float | TimeoutConfig | None = None,
    ) -> ExecCommandReturn:
        """
        Run a vagrant command and return everything, not just stdout.

        args: A sequence of arguments to a vagrant command line.
        e.g. ['up', 'my_vm_name', '--no-provision'] or
        ['up', None, '--no-provision'] for a non-Multi-VM environment.
        input: Optional input to pass to stdin.
        timeout: Optional timeout - can be a number (seconds) or TimeoutConfig
            for fine-grained control over grace periods.
        """
        # Extract timeout configuration
        timeout_val: float | None
        if isinstance(timeout, TimeoutConfig):
            timeout_val = timeout.timeout
            terminate_grace = timeout.terminate_grace
            kill_grace = timeout.kill_grace
        else:
            timeout_val = float(timeout) if timeout is not None else None
            terminate_grace = 5.0
            kill_grace = 5.0
        # Make subprocess command
        command = self._make_vagrant_command(args)
        self.logger.debug(f"Vagrant command: {command}")
        self.logger.debug(f"Working directory: {self.root}")
        self.logger.debug(
            f"Environment variables: {dict(self.env) if self.env else 'None'}"
        )
        self.logger.debug(f"Input provided: {input is not None}")

        stdin_mode = (
            asyncio.subprocess.PIPE if input is not None else asyncio.subprocess.DEVNULL
        )

        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=stdin_mode,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.root,
            env=self.env,
        )

        try:
            communicate_coro = process.communicate(
                input=input.encode("utf-8") if isinstance(input, str) else input
            )
            if timeout_val is not None:
                if timeout_val <= 0:
                    raise ValueError(f"timeout must be positive, got {timeout_val}")
                stdout, stderr = await asyncio.wait_for(
                    communicate_coro, timeout=float(timeout_val)
                )
            else:
                stdout, stderr = await communicate_coro
        except asyncio.TimeoutError:
            # Try graceful termination first
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=terminate_grace)
            except asyncio.TimeoutError:
                # Force kill if termination didn't work
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=kill_grace)
                except asyncio.TimeoutError:
                    # Give up waiting - process is likely orphaned
                    self.logger.error(
                        "Process did not respond to kill signal, abandoning."
                    )
                    raise SandboxUnrecoverableError(
                        f"Process could not be terminated after {timeout_val}s timeout - "
                        "sandbox may be in an inconsistent state"
                    )
            raise TimeoutError(
                f"Command execution timed out after {timeout_val} seconds."
            )

        assert process.returncode is not None, (
            "returncode should be set after communicate()"
        )

        # Decode bytes to string
        stdout_str = stdout.decode("utf-8") if stdout else ""
        stderr_str = stderr.decode("utf-8") if stderr else ""

        return {
            "stdout": stdout_str,
            "stderr": stderr_str,
            "returncode": process.returncode,
        }

    @override
    def ssh(
        self,
        vm_name: str | None = None,
        command: str | None = None,
        extra_ssh_args: str | None = None,
        input: str | bytes | None = None,
        timeout: int | float | TimeoutConfig | None = None,
    ) -> Coroutine[Any, Any, ExecCommandReturn]:
        """
        Execute a command via ssh on the vm specified.

        command: The command to execute via ssh.
        extra_ssh_args: Corresponds to '--' option in the vagrant ssh command
        input: Optional input to pass to stdin of the command.
        timeout: Optional timeout - can be a number (seconds) or TimeoutConfig.
        Returns the output of running the command.
        """
        cmd = ["ssh", vm_name, "--no-tty", "--command", command]
        if extra_ssh_args is not None:
            cmd += ["--", extra_ssh_args]

        return self._run_vagrant_command_async(cmd, input=input, timeout=timeout)


T = TypeVar("T")


class VagrantSandboxEnvironmentConfig(BaseModel, frozen=True):
    vagrantfile_path: str = Field(
        default_factory=lambda: getenv("VAGRANTFILE_PATH", "./Vagrantfile")
    )
    primary_vm_name: str | None = Field(
        default=None,
        description="Name of the VM to use as the 'default' sandbox environment. If None, uses first available VM.",
    )
    vagrantfile_env_vars: tuple[tuple[str, str], ...] = Field(
        default=(),
        description="Environment variables available to the Vagrantfile during vagrant commands. Accepts dict[str, str] or tuple of (key, value) pairs.",
    )

    @field_validator("vagrantfile_env_vars", mode="before")
    @classmethod
    def _convert_dict_to_tuple(cls, v: Any) -> tuple[tuple[str, str], ...]:
        if isinstance(v, dict):
            return tuple(v.items())
        return v


async def _run_in_executor(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a synchronous function in a thread pool."""
    return await asyncio.to_thread(func, *args, **kwargs)


@sandboxenv(name="vagrant")
class VagrantSandboxEnvironment(SandboxEnvironment):
    logger = getLogger(__name__)

    TRACE_NAME = "vagrant_sandbox_environment"

    # Printed to the guest's stderr just before exec() runs the command.
    # `vagrant ssh` prints its own warnings (e.g. fog's under libvirt, while
    # it looks up the VM) before it starts ssh, and ssh then writes to the
    # same stderr, so only what follows the marker is the command's stderr.
    # Anything vagrant prints after ssh exits (e.g. a user-defined `after`
    # trigger in the Vagrantfile) would still be included.
    STDERR_MARKER: Final = "__inspect_vagrant_stderr_8f2c41a6__"

    vagrant: Vagrant

    def __init__(
        self,
        sandbox_dir: SandboxDirectory,
        vagrant: Vagrant,
        vm_name: str | None = None,
    ):
        self.vagrant = vagrant
        self.sandbox_dir = sandbox_dir
        self.vm_name = vm_name

    @classmethod
    def default_concurrency(cls) -> int | None:
        """Default concurrent sandbox limit for vagrant environments.

        VMs are resource-intensive, so limit to cpu_count().
        Can be overridden via --max-sandboxes flag.
        """
        return default_max_subprocesses()

    @classmethod
    async def task_init(
        cls, task_name: str, config: SandboxEnvironmentConfigType | None
    ) -> None:
        if config is not None:
            if not isinstance(config, VagrantSandboxEnvironmentConfig):
                raise ValueError("config must be a VagrantSandboxEnvironmentConfig")

    @classmethod
    @override
    async def sample_init(
        cls,
        task_name: str,
        config: SandboxEnvironmentConfigType | None,
        metadata: dict[str, str],
    ) -> dict[str, SandboxEnvironment]:
        config = config or VagrantSandboxEnvironmentConfig()
        if not isinstance(config, VagrantSandboxEnvironmentConfig):
            raise TypeError(
                f"config must be VagrantSandboxEnvironmentConfig, got {type(config).__name__}"
            )

        # Create unique suffix from sample metadata to avoid VM name conflicts.
        # Although the base class annotates metadata as dict[str, str], Inspect
        # actually passes the sample's raw metadata (dict[str, Any]), so the
        # value can be any JSON-ish type (e.g. an int sample_id). Type it as
        # `object` so mypy forces the coercion to str.
        sample_id_value: object = metadata.get("sample_id", "unknown")
        sample_id = str(sample_id_value)

        # Use SandboxDirectory for user-local cache storage (easier to locate/cleanup)
        sandbox_dir = await SandboxDirectory.create(sample_id=sample_id)

        await asyncio.to_thread(
            shutil.copy2,
            config.vagrantfile_path,
            (sandbox_dir.path / "Vagrantfile").as_posix(),
        )

        # Use the sandbox directory name as the unique suffix - it already contains
        # both the sample_id prefix and a UUID, making it truly unique per test instance
        unique_suffix = f"-{sandbox_dir.path.name}"
        cls.logger.debug(f"Using unique VM suffix: {unique_suffix}")
        cls.logger.debug(f"Sandbox directory: {sandbox_dir.path}")

        # Set environment variable for Vagrantfile to use
        vagrant_env = os.environ.copy()
        vagrant_env["INSPECT_VM_SUFFIX"] = unique_suffix
        vagrant_env.update(dict(config.vagrantfile_env_vars))

        vagrant = Vagrant(root=str(sandbox_dir), env=vagrant_env)

        # Get available VMs before starting them
        # list[str | None] because when no VMs are discovered, [None] is used
        # below to mean "the default VM" (list is invariant, so a copy is
        # needed to widen the element type).
        vm_names: list[str | None] = list(await vagrant.get_vm_names())
        cls.logger.debug(f"Discovered VMs in Vagrantfile: {vm_names}")

        # If no VMs found, assume single-VM Vagrantfile
        if not vm_names:
            cls.logger.warning(
                "No VMs discovered via 'vagrant status', assuming single-VM Vagrantfile"
            )
            vm_names = [None]  # None means default/single VM

        try:
            # Start all VMs
            cls.logger.info(f"Starting VMs: {vm_names}")
            cls.logger.debug(f"Vagrant working directory: {sandbox_dir.path}")
            cls.logger.debug(
                f"Environment variables: INSPECT_VM_SUFFIX={vagrant_env.get('INSPECT_VM_SUFFIX')}"
            )

            # Log the Vagrantfile content for debugging
            vagrantfile_path = sandbox_dir.path / "Vagrantfile"
            try:
                vagrantfile_content = await asyncio.to_thread(
                    vagrantfile_path.read_text
                )
                cls.logger.debug(f"Vagrantfile contents:\n{vagrantfile_content}")
            except Exception as read_error:
                cls.logger.error(f"Could not read Vagrantfile: {read_error}")

            # First check current status before trying to start
            try:
                initial_status = await vagrant._run_vagrant_command_async(["status"])
                cls.logger.debug(f"Initial VM status: {initial_status['stdout']}")
            except Exception as status_error:
                cls.logger.debug(
                    f"Could not get initial status (this is normal for new VMs): {status_error}"
                )

            # Use our async method to capture stdout/stderr on failure
            # Throttle concurrent vagrant up operations to prevent resource exhaustion
            async with _startup_semaphore():
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

            await cls._discard_failed_sandbox(sandbox_dir)
            raise e
        except BaseException:
            # Timeouts, cancellation, anything else: the sample never starts, so
            # Inspect will not call sample_cleanup for it.
            await cls._discard_failed_sandbox(sandbox_dir)
            raise

        sandboxes: dict[str, SandboxEnvironment] = {}

        def base_vm_name(vm_name: str) -> str:
            """Strip the per-sample unique suffix from a discovered VM name.

            VM names reported by 'vagrant status' include the unique suffix
            appended by the Vagrantfile (via INSPECT_VM_SUFFIX), e.g.
            'attacker-sample01-abc123'. Sandbox dict keys and primary VM
            matching use the base name from the Vagrantfile ('attacker') so
            that eval code can reference sandboxes by a stable name.
            """
            return vm_name.removesuffix(unique_suffix)

        # Determine which VM should be the default
        # The primary_vm_name from config needs to be matched with the actual VM names (which include suffix)
        primary_vm_base = config.primary_vm_name
        primary_vm = None

        if primary_vm_base:
            # Find VM whose base name (suffix stripped) matches
            for vm_name in vm_names:
                if vm_name and base_vm_name(vm_name) == primary_vm_base:
                    primary_vm = vm_name
                    break

            if not primary_vm:
                available_vms = [base_vm_name(vm) for vm in vm_names if vm is not None]
                cls.logger.warning(
                    f"Primary VM '{primary_vm_base}' not found. "
                    f"Available VMs: {available_vms}. Using first available VM."
                )
                primary_vm = vm_names[0] if vm_names else None
        else:
            primary_vm = vm_names[0] if vm_names else None

        # Create sandbox environments for each VM
        cls.logger.debug(f"Creating sandbox environments. Primary VM: {primary_vm}")
        primary_env: SandboxEnvironment | None = None
        for vm_name in vm_names:
            env = VagrantSandboxEnvironment(sandbox_dir, vagrant, vm_name)
            cls.logger.debug(f"Created environment for VM: {vm_name}")

            # Add by base VM name if it's not None (multi-VM case)
            if vm_name is not None:
                sandboxes[base_vm_name(vm_name)] = env

            if vm_name == primary_vm:
                primary_env = env

        # The primary VM becomes "default"
        if primary_env is not None:
            sandboxes["default"] = primary_env
            cls.logger.debug(f"Set '{primary_vm}' as default sandbox environment")

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
    async def _discard_failed_sandbox(cls, sandbox_dir: SandboxDirectory) -> None:
        """Destroy a sandbox whose startup failed.

        Inspect only calls `sample_cleanup` for samples that started, so without
        this a failed `vagrant up` leaves the (possibly half-created) VM and its
        directory behind for the user to find with `vagrant global-status`.
        """
        cls.logger.info(f"Cleaning up sandbox after failed startup: {sandbox_dir.path}")
        try:
            await cleanup_sandbox_with_vms(sandbox_dir.path)
        except Exception as cleanup_error:
            cls.logger.error(
                f"Failed to clean up {sandbox_dir.path} after a failed startup: "
                f"{cleanup_error}. Clean up manually with: "
                "inspect sandbox cleanup vagrant"
            )

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
            # Deduplicate environments - the same env may be added under multiple keys
            # (e.g., "default" and the actual VM name)
            seen_ids: set[int] = set()
            for env in environments.values():
                if isinstance(env, VagrantSandboxEnvironment):
                    env_id = id(env)
                    if env_id in seen_ids:
                        continue
                    seen_ids.add(env_id)

                    if not env.sandbox_dir.path.exists():
                        cls.logger.warning(
                            f"Sandbox directory already deleted: {env.sandbox_dir.path}"
                        )
                        continue

                    result = await env.vagrant._run_vagrant_command_async(
                        ["destroy", "-f"]
                    )
                    if result["returncode"] != 0:
                        # Keep the directory: `.vagrant` inside it is the only
                        # handle on the VM that is still running, and without it
                        # neither `inspect sandbox cleanup vagrant` nor a manual
                        # `vagrant destroy` can reach it.
                        cls.logger.warning(
                            f"vagrant destroy returned {result['returncode']}: "
                            f"{result['stderr']}. Keeping {env.sandbox_dir.path} so "
                            "the VM can still be destroyed; retry with: "
                            "inspect sandbox cleanup vagrant"
                        )
                        continue

                    await env.sandbox_dir.cleanup()

    @classmethod
    @override
    async def task_cleanup(
        cls,
        task_name: str,
        config: SandboxEnvironmentConfigType | None,
        cleanup: bool,
    ) -> None:
        cache_dir = get_sandbox_cache_dir()
        directories = list_sandbox_directories()

        if not directories:
            return

        if cleanup:
            cls.logger.info(f"Cleaning up {len(directories)} sandbox(es)")
            for path in directories:
                try:
                    await cleanup_sandbox_with_vms(path)
                except Exception as e:
                    cls.logger.error(f"Failed to clean up {path}: {e}")
        else:
            cls.logger.info(f"Sandbox cache directory: {cache_dir}")
            for path in directories:
                cls.logger.info(f"  {path.name}")
            cls.logger.info(
                "Cleanup orphaned sandboxes with: inspect sandbox cleanup vagrant"
            )

    @classmethod
    @override
    async def cli_cleanup(cls, id: str | None) -> None:
        if id is None:
            # Clean up all sandbox directories (destroy VMs first)
            cache_dir = get_sandbox_cache_dir()
            directories = list_sandbox_directories()
            print(f"Sandbox cache directory: {cache_dir}")
            print(f"Found {len(directories)} sandbox(es)")

            if not directories:
                print("Nothing to clean up.")
                return

            removed = 0
            for path in directories:
                print(f"  Cleaning up: {path.name}...", end=" ", flush=True)
                try:
                    await cleanup_sandbox_with_vms(path)
                    print("done")
                    removed += 1
                except Exception as e:
                    print(f"FAILED: {e}")
                    cls.logger.error(f"Failed to clean up {path}: {e}")

            print(f"Cleaned up {removed}/{len(directories)} sandboxes")
        else:
            # Clean up specific sandbox by ID (directory name)
            cache_dir = get_sandbox_cache_dir()
            sandbox_path = cache_dir / id
            if sandbox_path.exists():
                print(f"Cleaning up sandbox: {id}...", end=" ", flush=True)
                try:
                    await cleanup_sandbox_with_vms(sandbox_path)
                    print("done")
                except Exception as e:
                    print(f"FAILED: {e}")
                    cls.logger.error(f"Failed to clean up {sandbox_path}: {e}")
            else:
                print(f"Sandbox not found: {id}")
                print(f"Available sandboxes in {cache_dir}:")
                for path in list_sandbox_directories():
                    print(f"  - {path.name}")

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
        command = shlex.join(cmd)
        if env:
            # `&&`, not `;`: a failing `cd` below must abort the command rather
            # than let it run in the wrong directory
            exports = " ".join(
                f"export {key}={shlex.quote(value)} &&" for key, value in env.items()
            )
            command = f"{exports} {command}"
        if cwd is not None:
            command = f"cd {shlex.quote(cwd)} && {command}"
        if user is not None:
            # Vagrant's base box guidelines require passwordless sudo for the
            # SSH user, and mainstream boxes comply. Wrap the command in `sh -c`
            # so the cwd/env handling above also runs as the target user. `-n`
            # makes a non-compliant box fail fast ("sudo: a password is
            # required" on stderr) instead of hanging on a password prompt.
            command = f"sudo -H -n -u {shlex.quote(user)} sh -c {shlex.quote(command)}"
        with trace_action(
            self.logger,
            self.TRACE_NAME,
            # f"exec_command {self.vm_id=} {exec_response_pid=}",
            "exec_command ",
        ):
            result = await self.vagrant.ssh(
                vm_name=self.vm_name,
                command=f"echo {self.STDERR_MARKER} >&2; {command}",
                input=input,
                timeout=timeout,
            )

            # No marker means ssh failed before the command ran: keep all of
            # stderr so the failure stays debuggable.
            _, marker, guest_stderr = result["stderr"].partition(
                f"{self.STDERR_MARKER}\n"
            )
            exec_result = ExecResult(
                success=result["returncode"] == 0,
                returncode=result["returncode"],
                stdout=result["stdout"],
                stderr=guest_stderr if marker else result["stderr"],
            )
            # Raise only if the shell could not execute cmd[0] itself (bash:
            # "bash: line 1: /etc/passwd: Permission denied", dash under
            # `user`: "sh: 1: /etc/passwd: Permission denied"). The same error
            # from inside the command, e.g. `bash -c ./script.sh`, is the
            # command's own result.
            if (
                exec_result.returncode == 126
                and cmd
                and exec_result.stderr.rstrip().endswith(
                    f": {cmd[0]}: Permission denied"
                )
            ):
                raise PermissionError(errno.EACCES, "Permission denied", cmd[0])
            self._verify_exec_output_size(exec_result)
            return exec_result

    # _verify_exec_output_size() and _truncate_middle() are a port of
    # inspect_ai 0.3.123's private verify_exec_result_size() and
    # truncate_string_to_bytes(), which can't be imported: 0.3.183 removed the
    # former. From 0.3.183 on, Inspect runs this check itself for every
    # provider, so these can go once we require inspect_ai >= 0.3.183.

    @classmethod
    def _verify_exec_output_size(cls, exec_result: ExecResult[str]) -> None:
        """Raise OutputLimitExceededError if stdout or stderr is over Inspect's limit."""
        limit = SandboxEnvironmentLimits.MAX_EXEC_OUTPUT_SIZE
        truncated_stdout = cls._truncate_middle(exec_result.stdout, limit)
        truncated_stderr = cls._truncate_middle(exec_result.stderr, limit)
        if truncated_stdout is None and truncated_stderr is None:
            return
        stdout = exec_result.stdout if truncated_stdout is None else truncated_stdout
        stderr = exec_result.stderr if truncated_stderr is None else truncated_stderr
        raise OutputLimitExceededError(
            limit_str=SandboxEnvironmentLimits.MAX_EXEC_OUTPUT_SIZE_STR,
            truncated_output=f"{stdout}{stderr}",
        )

    @staticmethod
    def _truncate_middle(text: str, max_bytes: int) -> str | None:
        """Cut text to max_bytes of UTF-8, keeping its start and end.

        Returns None if text already fits.
        """
        encoded = text.encode("utf-8", errors="replace")
        if len(encoded) <= max_bytes:
            return None
        start = encoded[: max_bytes // 2]
        end = encoded[len(encoded) - (max_bytes - len(start)) :]
        return (start + end).decode("utf-8", errors="replace")

    @staticmethod
    def _raise_file_error(
        file: str, command: str, returncode: int, stdout: str, stderr: str
    ) -> None:
        """Map a failed file operation to the errno-style exceptions Inspect expects."""
        if "No such file or directory" in stderr:
            raise FileNotFoundError(f"No such file or directory: {file}")
        if "Is a directory" in stderr:
            raise IsADirectoryError(f"Is a directory: {file}")
        if "Permission denied" in stderr:
            raise PermissionError(f"Permission denied: {file}")
        raise subprocess.CalledProcessError(returncode, command, stdout, stderr)

    @override
    async def write_file(self, file: str, contents: str | bytes) -> None:
        contents_bytes: bytes
        if isinstance(contents, bytes):
            contents_bytes = contents
        elif isinstance(contents, str):
            contents_bytes = contents.encode("utf-8")
        else:
            assert_never(contents)

        # Transfer the content base64-encoded via stdin: this is binary-safe
        # and avoids shell command-length limits for large files.
        encoded = base64.b64encode(contents_bytes).decode("ascii")
        parent = os.path.dirname(file)
        mkdir_prefix = f"mkdir -p -- {shlex.quote(parent)} && " if parent else ""
        command = f"{mkdir_prefix}base64 -d > {shlex.quote(file)}"
        result = await self.vagrant.ssh(
            vm_name=self.vm_name, command=command, input=encoded
        )
        if result["returncode"] != 0:
            self._raise_file_error(
                file, command, result["returncode"], result["stdout"], result["stderr"]
            )

    @overload
    async def read_file(self, file: str, text: Literal[True] = True) -> str: ...

    @overload
    async def read_file(self, file: str, text: Literal[False]) -> bytes: ...

    @override
    async def read_file(self, file: str, text: bool = True) -> str | bytes:
        quoted_file = shlex.quote(file)
        # Check the file's size against Inspect's read limit before transferring
        # it, then transfer base64-encoded so binary content survives the ssh
        # round-trip. A single remote command keeps this to one (slow) vagrant
        # ssh invocation.
        size_limit = SandboxEnvironmentLimits.MAX_READ_FILE_SIZE
        limit_marker = "inspect read_file size limit exceeded"
        command = (
            f"_size=$(stat -c %s -- {quoted_file}) && "
            f'{{ [ "$_size" -le {size_limit} ] || '
            f"{{ echo {shlex.quote(limit_marker)} >&2; exit 70; }}; }} && "
            f"base64 -- {quoted_file}"
        )
        result = await self.vagrant.ssh(vm_name=self.vm_name, command=command)
        if result["returncode"] != 0:
            if limit_marker in result["stderr"]:
                raise OutputLimitExceededError(
                    limit_str=SandboxEnvironmentLimits.MAX_READ_FILE_SIZE_STR,
                    # The potentially large content is not transferred.
                    truncated_output=None,
                )
            self._raise_file_error(
                file, command, result["returncode"], result["stdout"], result["stderr"]
            )

        contents = base64.b64decode(result["stdout"])
        if text:
            return contents.decode("utf-8")
        return contents

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
        sandbox_path = str(self.sandbox_dir)
        return SandboxConnection(
            type="vagrant",
            command=f"VAGRANT_CWD={sandbox_path} vagrant ssh",
        )
