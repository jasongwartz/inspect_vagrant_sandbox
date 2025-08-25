import asyncio


class AsyncVagrant:
    def __init__(self, cwd=None):
        self.cwd = cwd

    async def _run_vagrant_command(self, *args):
        process = await asyncio.create_subprocess_exec(
            "vagrant",
            *args,
            cwd=self.cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise Exception(f"Vagrant command failed: {stderr.decode()}")

        print("StdOut!", stdout.decode())

        return process.returncode, stdout.decode().strip(), stderr.decode().strip()

        # return stdout.decode().strip()

    async def up(self):
        return await self._run_vagrant_command("up")  # --provider qemu

    async def ssh_command(self, command):
        return await self._run_vagrant_command("ssh", "-c", command)

    async def halt(self):
        return await self._run_vagrant_command("halt")

    async def destroy(self, force=True):
        args = ["destroy"]
        if force:
            args.append("-f")
        return await self._run_vagrant_command(*args)

    async def status_string(self):
        """Returns raw status output as string"""
        return await self._run_vagrant_command("status")
