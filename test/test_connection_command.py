"""
Tests that EXECUTE the shell command returned by connection().

connection() returns a command that Inspect's human-agent solver prints
for a human to paste into a fresh terminal. These tests run that command
in a subprocess (with only the environment the command itself sets up)
and assert on where — and as whom — it actually lands.

Run with: pytest test/test_connection_command.py -v -s -m vm_required
"""

import os
import pty
import select
import subprocess
import time

import pytest

from vagrantsandbox.vagrant_sandbox_provider import (
    VagrantSandboxEnvironment,
    VagrantSandboxEnvironmentConfig,
)


def get_vagrantfile(name: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


def run_connection_command(
    command: str, input_text: str, timeout: float = 120.0
) -> subprocess.CompletedProcess[str]:
    """Run a connection() command the way a human's shell would.

    The command string itself must carry everything needed (VAGRANT_CWD,
    INSPECT_VM_SUFFIX, ...): a human pastes it into a fresh terminal that
    has none of the provider's internal environment.
    """
    env = {k: v for k, v in os.environ.items() if k != "INSPECT_VM_SUFFIX"}
    return subprocess.run(
        ["bash", "-c", command],
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def run_connection_command_in_pty(
    command: str, send_lines: list[str], timeout: float = 120.0
) -> str:
    """Run a connection() command under a real PTY, like a human terminal.

    Waits for a shell prompt, sends `send_lines` then `exit`, and returns
    everything the session printed.
    """
    env = {k: v for k, v in os.environ.items() if k != "INSPECT_VM_SUFFIX"}
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        ["bash", "-c", command],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        close_fds=True,
    )
    os.close(slave)

    output = b""
    sent = False
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            readable, _, _ = select.select([master], [], [], 1.0)
            if readable:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
                # A '$' or '#' is a shell prompt: the session is interactive
                if not sent and (b"$" in output or b"#" in output):
                    for line in send_lines:
                        os.write(master, (line + "\n").encode())
                    os.write(master, b"exit\n")
                    sent = True
            elif proc.poll() is not None:
                break
    finally:
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        os.close(master)

    return output.decode(errors="replace")


def output_lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line.strip() for line in result.stdout.splitlines()]


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_connection_command_single_vm():
    """Single-VM sandbox: run the command connection() returns."""
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "conn_single",
        VagrantSandboxEnvironmentConfig(
            vagrantfile_path=get_vagrantfile("Vagrantfile.basic")
        ),
        {"sample_id": "conn_single"},
    )
    sandbox = sandboxes["default"]
    assert isinstance(sandbox, VagrantSandboxEnvironment)

    try:
        # No user requested: lands as the box's ssh user
        connection = await sandbox.connection()
        result = run_connection_command(connection.command, "whoami\n")
        assert result.returncode == 0, result.stderr
        assert "vagrant" in output_lines(result)

        # user="root": must land as root, not silently stay the ssh user
        connection = await sandbox.connection(user="root")
        result = run_connection_command(connection.command, "whoami\n")
        assert result.returncode == 0, result.stderr
        assert "root" in output_lines(result)
        assert "vagrant" not in output_lines(result)

        # vm_name=None: plain `vagrant ssh` must work in a single-VM env
        unnamed = VagrantSandboxEnvironment(
            sandbox.sandbox_dir, sandbox.vagrant, vm_name=None
        )
        connection = await unnamed.connection(user="root")
        result = run_connection_command(connection.command, "whoami\n")
        assert result.returncode == 0, result.stderr
        assert "root" in output_lines(result)

        # Nonexistent user: fails loudly, never falls back to the ssh user
        connection = await sandbox.connection(user="nosuchuser")
        result = run_connection_command(connection.command, "whoami\n")
        assert result.returncode != 0
        assert "unknown user" in (result.stdout + result.stderr)
        assert "vagrant" not in output_lines(result)

        # Under a real PTY (what a human gets), user="root" must produce an
        # interactive login shell: a root prompt on a pseudo-terminal
        connection = await sandbox.connection(user="root")
        session = run_connection_command_in_pty(connection.command, ["whoami", "tty"])
        assert "root" in session
        assert "/dev/pts/" in session

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "conn_single",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )


@pytest.mark.vm_required
@pytest.mark.asyncio
async def test_connection_command_multi_vm():
    """Multi-VM sandbox: the command must land on the VM it names."""
    sandboxes = await VagrantSandboxEnvironment.sample_init(
        "conn_multi",
        VagrantSandboxEnvironmentConfig(
            vagrantfile_path=get_vagrantfile("Vagrantfile.multi"),
            primary_vm_name="attacker",
        ),
        {"sample_id": "conn_multi"},
    )

    try:
        named = {
            name: sandbox
            for name, sandbox in sandboxes.items()
            if name != "default" and isinstance(sandbox, VagrantSandboxEnvironment)
        }
        assert len(named) == 2, f"expected 2 named VMs, got {list(sandboxes)}"

        # The VMs share a hostname, so tag each one through the provider's
        # own exec() (which routes by vm_name), then check the connection
        # command reads back the tag of the VM it was asked for.
        for name, sandbox in named.items():
            tag = await sandbox.exec(["bash", "-c", f"echo {name} > /tmp/conn_marker"])
            assert tag.success

        for name, sandbox in named.items():
            connection = await sandbox.connection()
            result = run_connection_command(
                connection.command, "cat /tmp/conn_marker\n"
            )
            assert result.returncode == 0, result.stderr
            assert name in output_lines(result), (
                f"connection() for {name!r} landed on the wrong VM: {result.stdout!r}"
            )

        # user= works on a named VM too
        name, sandbox = next(iter(named.items()))
        connection = await sandbox.connection(user="root")
        result = run_connection_command(
            connection.command, "whoami; cat /tmp/conn_marker\n"
        )
        assert result.returncode == 0, result.stderr
        assert "root" in output_lines(result)
        assert name in output_lines(result)

        # vm_name=None in a multi-VM env: `vagrant ssh` without a machine
        # name falls back to the machine marked `primary: true` in the
        # Vagrantfile (attacker) — it does not land on an arbitrary VM.
        # (Without a primary machine, vagrant refuses with a clear error.)
        default = sandboxes["default"]
        assert isinstance(default, VagrantSandboxEnvironment)
        assert default.vm_name is not None
        assert default.vm_name.startswith("attacker")
        unnamed = VagrantSandboxEnvironment(
            default.sandbox_dir, default.vagrant, vm_name=None
        )
        connection = await unnamed.connection()
        result = run_connection_command(connection.command, "cat /tmp/conn_marker\n")
        assert result.returncode == 0, result.stderr
        assert default.vm_name in output_lines(result)

    finally:
        await VagrantSandboxEnvironment.sample_cleanup(
            "conn_multi",
            VagrantSandboxEnvironmentConfig(),
            sandboxes,
            interrupted=False,
        )
