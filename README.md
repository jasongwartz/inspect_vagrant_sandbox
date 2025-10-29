# Inspect Vagrant Sandbox

This plugin for [Inspect](https://inspect.aisi.org.uk/) allows you to use virtual machines, running with [Hashicorp Vagrant](https://developer.hashicorp.com/vagrant), as [sandboxes](https://inspect.aisi.org.uk/sandboxing.html). Vagrant can use multiple VM hypervisors as a "backend", making it especially portable across host operating systems and architectures. Before using `inspect_vagrant_sandbox`, you should familiarise yourself with [the official Vagrant docs from Hashicorp](https://developer.hashicorp.com/vagrant/docs).

## Installing

Add this using [Poetry](https://python-poetry.org/)

```
poetry add git+ssh://git@github.com/jasongwartz/inspect_vagrant_sandbox.git
```

or in [uv](https://github.com/astral-sh/uv),

```
uv add git+ssh://git@github.com/jasongwartz/inspect_vagrant_sandbox.git
```

## Getting Started

You'll need to create a `Vagrantfile` in the directory from which you'll invoke Inspect (e.g. the root of your repository). You can find some example Vagrantfiles in the [tests directory](./test/). For example, [a basic Ubuntu VM](./test/Vagrantfile.basic) which runs with QEMU on arm64 macOS could be:

```ruby
Vagrant.configure("2") do |config|
    # Ubuntu base image ("box") for arm64 macOS devices
    config.vm.box = "perk/ubuntu-2204-arm64"

    config.vm.provider "qemu" do |qe|
        # Default is:
        # qe.ssh_port = "50022"
        # Default is:
        # qe.machine = "virt,accel=hvf,highmem=off"
        qe.ssh_auto_correct = true
  end

  # Speed up SSH
  config.ssh.insert_key = false

  # Disable folder sync if not needed
  config.vm.synced_folder ".", "/vagrant", disabled: true
end
```

Then configure the sandbox provider to `"vagrant"` in your task. For example, if your Vagrantfile is called exactly `"Vagrantfile"` and is in the directory from which you'll run `inspect eval`, you can configure your task as follows:

```python
@task
def vagrant_example() -> Task:
    return Task(
        ...
        sandbox="vagrant",
```

If you want to customise the location of the `Vagrantfile` (for example, if you have multiple Vagrantfiles for different samples), you can instead provide an Inspect `SandboxEnvironmentSpec`, containing a `VagrantSandboxEnvironmentConfig` with key `vagrantfile_path`. This can be a relative or absolute path to the `Vagrantfile` for the given task:

```python
@task
def vagrant_example() -> Task:
    return Task(
        ...
        sandbox=SandboxEnvironmentSpec(
            "vagrant",
            VagrantSandboxEnvironmentConfig(
                vagrantfile_path="./test/Vagrantfile.basic"
            ),
        ),
    )
```

### Multi-Machine

Vagrant has support for ["multi-machine" setups](https://developer.hashicorp.com/vagrant/docs/multi-machine) (i.e. multiple guest VM configurations in a single `Vagrantfile`), which can be useful for writing evals that have complex multi-VM setups (e.g. an "attacker" and "victim" VM). If you're using a multi-machine `Vagrantfile`, you should ensure each "machine" is given a name:

```ruby
Vagrant.configure("2") do |config|

  config.vm.define "attacker" do |attacker|
    ...
  end

  config.vm.define "victim" do |victim|
    ...
  end
end
```

You must also set the "primary" VM's name (which VM should be the "entrypoint" for the model's sandbox commands) in the task configuration with the `primary_vm_name` argument:

```python
@task
def vagrant_example() -> Task:
    return Task(
        ...
        sandbox=SandboxEnvironmentSpec(
            "vagrant",
            VagrantSandboxEnvironmentConfig(
                primary_vm_name="attacker"
            ),
        ),
    )
```

## Future Work

This release should be considered **pre-alpha**! There are several bug fixes and missing features that would be required before a production release. In particular, users should take note of the following:

- This plugin was primarily tested using [the QEMU extension for Vagrant](https://github.com/ppggff/vagrant-qemu) on an Apple Silicon (arm64) macOS device
- Sandbox VMs may not be completely cleaned up automatically, and you may need to clean up stray VMs manually - you can use `vagrant global-status --prune` to identify VMs where the temporary directory no longer exists
- Some commands might hang indefinitely (`cat` commands seem to be a common culprit in testing) - if you experience this, you might want to manually time-out the tool call in the Inspect TUI using the `Timeout Tool` button
