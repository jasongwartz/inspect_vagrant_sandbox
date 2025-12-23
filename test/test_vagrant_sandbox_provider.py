import asyncio
import subprocess
from unittest.mock import AsyncMock, Mock, patch
import pytest

from pathlib import Path
from vagrantsandbox.vagrant_sandbox_provider import (
    Vagrant,
    VagrantSandboxEnvironment,
    VagrantSandboxEnvironmentConfig,
    SandboxDirectory,
    SandboxUnrecoverableError,
    _run_in_executor,
)


# Shared fixtures and test data
@pytest.fixture
def mock_vagrant():
    """Create a mock Vagrant instance."""
    vagrant = Mock(spec=Vagrant)
    vagrant.ssh = AsyncMock()
    vagrant.up = Mock()
    vagrant.destroy = Mock()
    return vagrant


@pytest.fixture
def mock_sandbox_dir():
    """Create a mock SandboxDirectory instance."""
    mock_dir = Mock(spec=SandboxDirectory)
    # Create a mock path that returns True for exists()
    mock_path = Mock()
    mock_path.exists = Mock(return_value=True)
    mock_dir.path = mock_path
    mock_dir.cleanup = AsyncMock(return_value=None)
    return mock_dir


@pytest.fixture
def sample_config():
    """Create a sample configuration."""
    return VagrantSandboxEnvironmentConfig(vagrantfile_path="/test/Vagrantfile.basic")


@pytest.fixture
def mock_subprocess_patches():
    """Create patches for subprocess methods in vagrant module."""
    with (
        patch("vagrant.subprocess.run") as mock_run,
        patch("vagrant.subprocess.check_output") as mock_check_output,
        patch("vagrant.subprocess.check_call") as mock_check_call,
    ):
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        mock_check_output.return_value = b"vagrant output"
        mock_check_call.return_value = None

        yield {
            "run": mock_run,
            "check_output": mock_check_output,
            "check_call": mock_check_call,
        }


@pytest.fixture
def mock_sandbox_patches():
    """Create patches for SandboxDirectory operations."""
    mock_sandbox = Mock(spec=SandboxDirectory)
    # Create a mock path that returns True for exists()
    mock_path = Mock()
    mock_path.exists = Mock(return_value=True)
    mock_path.__truediv__ = Mock(return_value=mock_path)  # For path / "Vagrantfile"
    mock_path.as_posix = Mock(return_value="/tmp/test_vagrant/Vagrantfile")
    mock_sandbox.path = mock_path
    mock_sandbox.cleanup = AsyncMock(return_value=None)

    with (
        patch(
            "vagrantsandbox.vagrant_sandbox_provider.SandboxDirectory.create",
            new_callable=AsyncMock,
            return_value=mock_sandbox,
        ) as mock_create,
        patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread,
    ):
        yield {
            "create": mock_create,
            "to_thread": mock_to_thread,
            "sandbox": mock_sandbox,
        }


@pytest.fixture(autouse=True)
def mock_vagrant_for_unit_tests(request):
    """Auto-mock vagrant executable for unit tests."""
    if "unit" in [mark.name for mark in request.node.iter_markers()]:
        with patch("vagrant.get_vagrant_executable", return_value="/usr/bin/vagrant"):
            yield
    else:
        yield


class MockAsyncProcess:
    """Helper class to mock async subprocess."""

    def __init__(
        self, returncode=0, stdout="", stderr="", hang_forever=False, resist_kill=False
    ):
        self.returncode = returncode
        self._stdout = stdout.encode() if isinstance(stdout, str) else stdout
        self._stderr = stderr.encode() if isinstance(stderr, str) else stderr
        # resist_kill implies hang_forever (can't resist kill if you complete normally)
        self._hang_forever = hang_forever or resist_kill
        self._resist_kill = resist_kill
        self._killed = False
        self._terminated = False

    async def communicate(self, input=None):
        if self._hang_forever:
            # Simulate a hanging process by waiting indefinitely
            await asyncio.sleep(3600)
        return self._stdout, self._stderr

    def terminate(self):
        self._terminated = True
        self.returncode = -15

    def kill(self):
        self._killed = True
        self.returncode = -9

    async def wait(self):
        if self._resist_kill:
            # Simulate a process that won't die even after kill signal
            await asyncio.sleep(3600)


class TestVagrant:
    """Test the custom Vagrant class that extends python-vagrant."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_run_vagrant_command_async_success(self):
        """Test successful vagrant command execution."""
        mock_process = MockAsyncProcess(returncode=0, stdout="vagrant output")

        with patch(
            "asyncio.create_subprocess_exec", return_value=mock_process
        ) as mock_exec:
            vagrant = Vagrant(root="/tmp/test")
            result = await vagrant._run_vagrant_command_async(["status"])

            assert result["returncode"] == 0
            assert result["stdout"] == "vagrant output"
            assert result["stderr"] == ""

            mock_exec.assert_called_once()
            args, kwargs = mock_exec.call_args
            assert "vagrant" in args[0]
            assert "status" in args
            assert kwargs["stdout"] == asyncio.subprocess.PIPE
            assert kwargs["stderr"] == asyncio.subprocess.PIPE

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_run_vagrant_command_async_failure(self):
        """Test failed vagrant command execution."""
        mock_process = MockAsyncProcess(returncode=1, stderr="error message")

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            vagrant = Vagrant(root="/tmp/test")
            result = await vagrant._run_vagrant_command_async(["up"])

            assert result["returncode"] == 1
            assert result["stdout"] == ""
            assert result["stderr"] == "error message"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_ssh_command(self):
        """Test SSH command construction and execution."""
        mock_process = MockAsyncProcess(returncode=0, stdout="command output")

        with patch(
            "asyncio.create_subprocess_exec", return_value=mock_process
        ) as mock_exec:
            vagrant = Vagrant(root="/tmp/test")
            result = await vagrant.ssh(vm_name="default", command="ls -la")

            assert result["returncode"] == 0
            assert result["stdout"] == "command output"

            # Verify SSH command structure
            mock_exec.assert_called_once()
            args, _ = mock_exec.call_args
            assert "ssh" in args
            assert "default" in args
            assert "--command" in args
            assert "ls -la" in args


class TestVagrantSandboxEnvironment:
    """Test the VagrantSandboxEnvironment class."""

    @pytest.mark.unit
    def test_init(self, mock_sandbox_dir, mock_vagrant):
        """Test VagrantSandboxEnvironment initialization."""
        env = VagrantSandboxEnvironment(mock_sandbox_dir, mock_vagrant)
        assert env.vagrant == mock_vagrant
        assert env.sandbox_dir == mock_sandbox_dir

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_sample_init_success(
        self, sample_config, mock_subprocess_patches, mock_sandbox_patches
    ):
        """Test successful sample initialization."""
        with patch(
            "vagrantsandbox.vagrant_sandbox_provider.Vagrant._run_vagrant_command_async"
        ) as mock_async_vagrant:
            mock_async_vagrant.return_value = {
                "returncode": 0,
                "stdout": "VM started",
                "stderr": "",
            }

            result = await VagrantSandboxEnvironment.sample_init(
                "test_task", sample_config, {}
            )

            assert "default" in result
            assert isinstance(result["default"], VagrantSandboxEnvironment)
            mock_sandbox_patches["to_thread"].assert_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_sample_init_vagrant_up_failure(
        self, sample_config, mock_sandbox_patches
    ):
        """Test sample initialization when vagrant up fails."""
        with patch(
            "vagrantsandbox.vagrant_sandbox_provider.Vagrant._run_vagrant_command_async"
        ) as mock_async_vagrant:
            mock_async_vagrant.return_value = {
                "returncode": 1,
                "stdout": "",
                "stderr": "VM failed to start",
            }

            with pytest.raises(subprocess.CalledProcessError):
                await VagrantSandboxEnvironment.sample_init(
                    "test_task", sample_config, {}
                )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_sample_cleanup_success(
        self, mock_vagrant, mock_sandbox_dir, mock_subprocess_patches
    ):
        """Test successful sample cleanup."""
        # Mock the path.exists() to return True so cleanup proceeds
        mock_sandbox_dir.path.exists = Mock(return_value=True)
        mock_vagrant._run_vagrant_command_async = AsyncMock(
            return_value={"returncode": 0, "stdout": "", "stderr": ""}
        )

        env = VagrantSandboxEnvironment(mock_sandbox_dir, mock_vagrant)
        environments = {"default": env}

        await VagrantSandboxEnvironment.sample_cleanup(
            "test_task", None, environments, interrupted=False
        )

        mock_vagrant._run_vagrant_command_async.assert_called_once_with(
            ["destroy", "-f"]
        )
        mock_sandbox_dir.cleanup.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_sample_cleanup_interrupted(self, mock_vagrant, mock_sandbox_dir):
        """Test cleanup when interrupted (should not destroy VM)."""
        env = VagrantSandboxEnvironment(mock_sandbox_dir, mock_vagrant)
        environments = {"default": env}

        with patch("vagrant.subprocess.run") as mock_subprocess_run:
            await VagrantSandboxEnvironment.sample_cleanup(
                "test_task", None, environments, interrupted=True
            )

            mock_subprocess_run.assert_not_called()
            mock_sandbox_dir.cleanup.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_exec_success(self, mock_vagrant, mock_sandbox_dir):
        """Test successful command execution."""
        env = VagrantSandboxEnvironment(mock_sandbox_dir, mock_vagrant)
        mock_vagrant.ssh.return_value = {
            "returncode": 0,
            "stdout": "command output",
            "stderr": "",
        }

        result = await env.exec(["ls", "-la"])

        assert result.success is True
        assert result.returncode == 0
        assert result.stdout == "command output"
        assert result.stderr == ""
        mock_vagrant.ssh.assert_called_once_with(
            vm_name=None, command="ls -la", input=None, timeout=None
        )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_exec_failure(self, mock_vagrant, mock_sandbox_dir):
        """Test failed command execution."""
        env = VagrantSandboxEnvironment(mock_sandbox_dir, mock_vagrant)
        mock_vagrant.ssh.return_value = {
            "returncode": 1,
            "stdout": "",
            "stderr": "command failed",
        }

        result = await env.exec(["false"])

        assert result.success is False
        assert result.returncode == 1
        assert result.stderr == "command failed"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_exec_escapes_shell_metacharacters(
        self, mock_vagrant, mock_sandbox_dir
    ):
        """Test that shell metacharacters are properly escaped."""
        env = VagrantSandboxEnvironment(mock_sandbox_dir, mock_vagrant)
        mock_vagrant.ssh.return_value = {"returncode": 0, "stdout": "", "stderr": ""}

        await env.exec(["bash", "-c", "ls && cat /etc/passwd"])

        call_args = mock_vagrant.ssh.call_args
        command = call_args[1]["command"]
        # shlex.join should quote the argument containing &&
        assert "&&" not in command.split("'")[0], "Metacharacters should be quoted"
        assert "'ls && cat /etc/passwd'" in command

    @pytest.mark.unit
    @pytest.mark.asyncio
    @pytest.mark.parametrize("metachar", ["&&", "||", ";", "|", "$(cmd)", "`cmd`"])
    async def test_exec_escapes_various_metacharacters(
        self, mock_vagrant, mock_sandbox_dir, metachar
    ):
        """Test various shell metacharacters are escaped."""
        env = VagrantSandboxEnvironment(mock_sandbox_dir, mock_vagrant)
        mock_vagrant.ssh.return_value = {"returncode": 0, "stdout": "", "stderr": ""}

        await env.exec(["echo", f"test {metachar} injection"])

        command = mock_vagrant.ssh.call_args[1]["command"]
        # The metacharacter should appear inside quotes, not bare
        assert command.startswith("echo "), "Command should start with 'echo '"
        assert metachar not in command.split("'")[0], f"{metachar} should be quoted"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_write_file_success(self, mock_vagrant, mock_sandbox_dir):
        """Test successful file writing."""
        env = VagrantSandboxEnvironment(mock_sandbox_dir, mock_vagrant)
        mock_vagrant.ssh.return_value = {"returncode": 0, "stdout": "", "stderr": ""}

        await env.write_file("/tmp/test.txt", "test content")

        mock_vagrant.ssh.assert_called_once()
        call_args = mock_vagrant.ssh.call_args
        assert "printf %s 'test content' > /tmp/test.txt" in call_args[1]["command"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_write_file_failure(self, mock_vagrant, mock_sandbox_dir):
        """Test file writing failure."""
        env = VagrantSandboxEnvironment(mock_sandbox_dir, mock_vagrant)
        mock_vagrant.ssh.return_value = {
            "returncode": 1,
            "stdout": "",
            "stderr": "permission denied",
        }

        with pytest.raises(subprocess.CalledProcessError):
            await env.write_file("/root/test.txt", "test content")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_write_file_bytes_content(self, mock_vagrant, mock_sandbox_dir):
        """Test writing bytes content to file."""
        env = VagrantSandboxEnvironment(mock_sandbox_dir, mock_vagrant)
        mock_vagrant.ssh.return_value = {"returncode": 0, "stdout": "", "stderr": ""}

        await env.write_file("/tmp/test.txt", b"test content")

        mock_vagrant.ssh.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_read_file_success(self, mock_vagrant, mock_sandbox_dir):
        """Test successful file reading."""
        env = VagrantSandboxEnvironment(mock_sandbox_dir, mock_vagrant)
        mock_vagrant.ssh.return_value = {
            "returncode": 0,
            "stdout": "file content",
            "stderr": "",
        }

        result = await env.read_file("/tmp/test.txt")

        assert result == "file content"
        mock_vagrant.ssh.assert_called_once_with(
            vm_name=None, command="cat /tmp/test.txt"
        )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_read_file_failure(self, mock_vagrant, mock_sandbox_dir):
        """Test file reading failure."""
        env = VagrantSandboxEnvironment(mock_sandbox_dir, mock_vagrant)
        mock_vagrant.ssh.return_value = {
            "returncode": 1,
            "stdout": "",
            "stderr": "file not found",
        }

        with pytest.raises(subprocess.CalledProcessError):
            await env.read_file("/missing/file.txt")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_connection(self, mock_vagrant, mock_sandbox_dir):
        """Test connection method."""
        env = VagrantSandboxEnvironment(mock_sandbox_dir, mock_vagrant)
        connection = await env.connection()

        assert connection.type == "vagrant"
        assert connection.command.endswith("vagrant ssh")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_cli_cleanup_no_id(self):
        """Test CLI cleanup without specific ID."""
        # Mock list_sandbox_directories to return some directories
        mock_dirs = [Path("/mock/sandbox1"), Path("/mock/sandbox2")]
        with (
            patch(
                "vagrantsandbox.vagrant_sandbox_provider.list_sandbox_directories",
                return_value=mock_dirs,
            ),
            patch(
                "vagrantsandbox.vagrant_sandbox_provider.cleanup_sandbox_with_vms",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_cleanup,
        ):
            await VagrantSandboxEnvironment.cli_cleanup(None)
            assert mock_cleanup.call_count == 2


class TestRunInExecutor:
    """Test the _run_in_executor utility function."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_run_in_executor(self):
        """Test running sync function in executor."""

        def sync_func(x, y):
            return x + y

        result = await _run_in_executor(sync_func, 1, 2)
        assert result == 3

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_run_in_executor_with_kwargs(self):
        """Test running sync function with kwargs in executor."""

        def sync_func(x, y=10):
            return x * y

        result = await _run_in_executor(sync_func, 5, y=3)
        assert result == 15


class TestVagrantSandboxEnvironmentConfig:
    """Test the configuration class."""

    @pytest.mark.unit
    def test_default_config(self):
        """Test default configuration values."""
        config = VagrantSandboxEnvironmentConfig()
        assert config.vagrantfile_path == "./Vagrantfile"

    @pytest.mark.unit
    def test_custom_config(self):
        """Test custom configuration values."""
        config = VagrantSandboxEnvironmentConfig(vagrantfile_path="/custom/Vagrantfile")
        assert config.vagrantfile_path == "/custom/Vagrantfile"

    @pytest.mark.unit
    def test_config_deserialize(self):
        """Test configuration deserialization."""
        config_dict = {"vagrantfile_path": "/test/Vagrantfile"}
        config = VagrantSandboxEnvironment.config_deserialize(config_dict)

        assert isinstance(config, VagrantSandboxEnvironmentConfig)
        assert config.vagrantfile_path == "/test/Vagrantfile"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sample_lifecycle_unit(
    sample_config, mock_subprocess_patches, mock_sandbox_patches
):
    """Unit test for the complete sample lifecycle with mocking."""
    with patch(
        "vagrantsandbox.vagrant_sandbox_provider.Vagrant._run_vagrant_command_async"
    ) as mock_async_vagrant:
        mock_async_vagrant.return_value = {
            "returncode": 0,
            "stdout": "VM started",
            "stderr": "",
        }

        # Initialize sample
        environments = await VagrantSandboxEnvironment.sample_init(
            "test_task", sample_config, {}
        )

        assert "default" in environments
        env = environments["default"]

        # Test exec with mocked SSH
        async def mock_ssh_return():
            return {"returncode": 0, "stdout": "test output", "stderr": ""}

        with patch.object(
            env.vagrant, "ssh", return_value=mock_ssh_return()
        ) as mock_ssh:
            result = await env.exec(["echo", "test"])
            assert result.success is True
            assert result.stdout == "test output"
            mock_ssh.assert_called_once()

        # Cleanup sample
        await VagrantSandboxEnvironment.sample_cleanup(
            "test_task", sample_config, environments, interrupted=False
        )

        # Verify cleanup was called
        mock_sandbox_patches["sandbox"].cleanup.assert_called_once()


class TestTimeoutHandling:
    """Test timeout handling for command execution."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_run_vagrant_command_async_with_timeout_success(self):
        """Test that timeout is respected when command completes in time."""
        mock_process = MockAsyncProcess(returncode=0, stdout="output")

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            vagrant = Vagrant(root="/tmp/test")
            result = await vagrant._run_vagrant_command_async(["status"], timeout=30)

            assert result["returncode"] == 0
            assert result["stdout"] == "output"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_run_vagrant_command_async_timeout_terminates_process(self):
        """Test that a hanging process is terminated when timeout expires."""
        mock_process = MockAsyncProcess(hang_forever=True)

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            vagrant = Vagrant(root="/tmp/test")

            with pytest.raises(TimeoutError) as exc_info:
                await vagrant._run_vagrant_command_async(
                    ["ssh", "default", "--command", "sleep infinity"],
                    timeout=1,
                )

            assert "timed out" in str(exc_info.value).lower()
            # Process should be terminated (graceful shutdown attempted first)
            assert mock_process._terminated is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_run_vagrant_command_async_no_timeout(self):
        """Test that without timeout, command runs without wait_for wrapper."""
        mock_process = MockAsyncProcess(returncode=0, stdout="done")

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            vagrant = Vagrant(root="/tmp/test")
            result = await vagrant._run_vagrant_command_async(["status"])

            assert result["returncode"] == 0
            assert result["stdout"] == "done"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_ssh_passes_timeout_to_run_command(self):
        """Test that ssh() passes timeout through to _run_vagrant_command_async."""
        mock_process = MockAsyncProcess(returncode=0, stdout="ssh output")

        with patch(
            "asyncio.create_subprocess_exec", return_value=mock_process
        ) as mock_exec:
            vagrant = Vagrant(root="/tmp/test")
            result = await vagrant.ssh(vm_name="default", command="ls", timeout=60)

            assert result["returncode"] == 0
            mock_exec.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_ssh_timeout_terminates_hanging_command(self):
        """Test that ssh command is terminated on timeout."""
        mock_process = MockAsyncProcess(hang_forever=True)

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            vagrant = Vagrant(root="/tmp/test")

            with pytest.raises(TimeoutError):
                await vagrant.ssh(
                    vm_name="default", command="sleep infinity", timeout=1
                )

            assert mock_process._terminated is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_exec_passes_timeout_to_ssh(self, mock_sandbox_dir):
        """Test that exec() passes timeout through to vagrant.ssh()."""
        mock_vagrant = Mock(spec=Vagrant)
        mock_vagrant.ssh = AsyncMock(
            return_value={"returncode": 0, "stdout": "output", "stderr": ""}
        )

        env = VagrantSandboxEnvironment(mock_sandbox_dir, mock_vagrant)
        result = await env.exec(["ls", "-la"], timeout=120)

        assert result.success is True
        mock_vagrant.ssh.assert_called_once_with(
            vm_name=None, command="ls -la", input=None, timeout=120
        )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_exec_timeout_propagates_error(self, mock_sandbox_dir):
        """Test that TimeoutError from ssh is propagated through exec()."""
        mock_vagrant = Mock(spec=Vagrant)
        mock_vagrant.ssh = AsyncMock(
            side_effect=TimeoutError("Command execution timed out after 5 seconds.")
        )

        env = VagrantSandboxEnvironment(mock_sandbox_dir, mock_vagrant)

        with pytest.raises(TimeoutError) as exc_info:
            await env.exec(["sleep", "1000"], timeout=5)

        assert "timed out" in str(exc_info.value).lower()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_unkillable_process_raises_sandbox_unrecoverable_error(self):
        """Test that an unkillable process raises SandboxUnrecoverableError to fail the sample."""
        # resist_kill implies hang_forever (process hangs and can't be killed)
        mock_process = MockAsyncProcess(resist_kill=True)

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            vagrant = Vagrant(root="/tmp/test")

            with pytest.raises(SandboxUnrecoverableError) as exc_info:
                await vagrant._run_vagrant_command_async(
                    ["ssh", "default", "--command", "sleep infinity"],
                    timeout=1,
                )

            assert "could not be terminated" in str(exc_info.value).lower()
            # Both terminate and kill should have been attempted
            assert mock_process._terminated is True
            assert mock_process._killed is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_timeout_zero_raises_value_error(self):
        """Test that timeout=0 raises ValueError."""
        mock_process = MockAsyncProcess(returncode=0, stdout="output")

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            vagrant = Vagrant(root="/tmp/test")

            with pytest.raises(ValueError) as exc_info:
                await vagrant._run_vagrant_command_async(["status"], timeout=0)

            assert "timeout must be positive" in str(exc_info.value)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_timeout_negative_raises_value_error(self):
        """Test that negative timeout raises ValueError."""
        mock_process = MockAsyncProcess(returncode=0, stdout="output")

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            vagrant = Vagrant(root="/tmp/test")

            with pytest.raises(ValueError) as exc_info:
                await vagrant._run_vagrant_command_async(["status"], timeout=-5)

            assert "timeout must be positive" in str(exc_info.value)
