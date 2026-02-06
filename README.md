# Inspect Vagrant Sandbox

This plugin for [Inspect](https://inspect.aisi.org.uk/) allows you to use virtual machines, running with [Hashicorp Vagrant](https://developer.hashicorp.com/vagrant), as [sandboxes](https://inspect.aisi.org.uk/sandboxing.html). Vagrant can use multiple VM hypervisors as a "backend", making it especially portable across host operating systems and architectures. Before using `inspect_vagrant_sandbox`, you should familiarise yourself with [the official Vagrant docs from Hashicorp](https://developer.hashicorp.com/vagrant/docs).

> [!CAUTION]
> This release should be considered **alpha** and unstable! There are likely many bugs to fix and breaking API changes to make before a production release.

## Installing

Add this using [Poetry](https://python-poetry.org/)

```
poetry add git+ssh://git@github.com/jasongwartz/inspect_vagrant_sandbox.git
```

or in [uv](https://github.com/astral-sh/uv),

```
uv add git+ssh://git@github.com/jasongwartz/inspect_vagrant_sandbox.git
```

You'll also need to have Vagrant installed - follow [the Vagrant documentation](https://developer.hashicorp.com/vagrant/docs/installation).

## Getting Started

### Setting Up Your Machine

Vagrant supports multiple hypervisor "plugins", like VirtualBox, QEMU, libvirt, and others. You'll need to set up your machine for a hypervisor, and install the Vagrant plugin for that hypervisor.

The `inspect_vagrant_sandbox` has been tested with:

- [vagrant-qemu](https://github.com/ppggff/vagrant-qemu) on macOS with Apple Silicon
- [vagrant-libvirt](https://vagrant-libvirt.github.io/vagrant-libvirt/) on an AWS metal instance running Ubuntu

Other Vagrant hypervisor "plugins" may work, though the `Vagrantfile` may need custom configuration for the specific plugin.

### Writing an Eval

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

### Environment Variables for Vagrant

You can pass environment variables to the Vagrant subprocess using `vagrantfile_env_vars`. Unlike global environment variables, these are scoped to each sample's Vagrant process, allowing different samples to use different values during parallel execution. Note that these environment variables are **not** available _inside_ the sandbox; they are available to the Vagrant process (ie. when running `vagrant up`). This can be useful for parameterizing a Vagrantfile, for example to specify which base box to use:

```ruby
# Vagrantfile
Vagrant.configure("2") do |config|
  vm_suffix = ENV['INSPECT_VM_SUFFIX'] || ''
  box_name = ENV['VAGRANT_BOX'] || 'generic/ubuntu2204'

  config.vm.define "default#{vm_suffix}" do |vm|
    vm.vm.box = box_name
  end
end
```

```python
@task
def vagrant_example() -> Task:
    return Task(
        ...
        sandbox=SandboxEnvironmentSpec(
            "vagrant",
            VagrantSandboxEnvironmentConfig(
                vagrantfile_env_vars={"VAGRANT_BOX": "generic/debian12"},
            ),
        ),
    )
```

### Concurrent VM Startup Throttling

Inspect controls sandbox concurrency via the `--max-sandboxes` flag or sample concurrency settings. By default, the vagrant sandbox provider limits concurrent sandboxes to `os.cpu_count()` (since VMs are resource-intensive).

For additional control over `vagrant up` operations specifically, you can set the `INSPECT_MAX_VAGRANT_STARTUPS` environment variable. This is useful if you want to throttle VM startup operations independently from the overall sandbox limit:

```bash
# Allow only 4 concurrent VM startups (useful for memory-constrained systems)
export INSPECT_MAX_VAGRANT_STARTUPS=4
```

If `INSPECT_MAX_VAGRANT_STARTUPS` is not set, Inspect's sandbox concurrency (`--max-sandboxes`) controls the parallelism.

Note that this only throttles the `vagrant up` operation. Once VMs are running, other operations (SSH commands, file transfers) can run in parallel following other Inspect concurrency settings.

### Testing your Vagrantfile and Sandbox

VM setups can be complex and difficult to debug, especially if your Vagrantfile starts up multiple VMs.

When developing the sandbox, you can use `vagrant up` to create the VM(s), and `vagrant destroy` when finished. Review [the Vagrant CLI documentation](https://developer.hashicorp.com/vagrant/docs/cli) for more information.

If you want to test your full eval implementation to make sure it's solvable, you might want to use [Inspect's "Human Agent" solver](https://inspect.aisi.org.uk/human-agent.html). Run your eval as normal (i.e. with `inspect eval ...`), and add `--solver human_cli` to the command; this will bootstrap the sandbox as defined in the eval, and then print out a `vagrant ssh` command you can use to connect into the sandbox.

### Cleaning Up Stray Sandboxes

Some VMs may fail to clean up automatically.

You can attempt to clean up sandboxes with:

```bash
inspect sandbox cleanup vagrant
```

This will also print the "sandbox cache directory". The Vagrant Sandbox provider makes copies of an eval's `Vagrantfile`, to create an isolated "environment" for running sandboxes for multiple Inspect samples in parallel.

You can review which Vagrant VMs are running with:

```bash
vagrant global-status
```

If the above `inspect` command failes to clean up VMs, you can also run `rm -r` on the "sandbox cache directory" (as printed by `inspect sandbox cleanup vagrant`), then run:

```bash
vagrant global-status --prune
```

## Developing the Sandbox Provider

First, make sure to familiarise yourself with the [Inspect sandbox provider extension API](https://inspect.aisi.org.uk/extensions.html#sec-sandbox-environment-extensions).

This implementation takes much inspiration from the [Inspect Kubernetes sandbox provider](https://k8s-sandbox.aisi.org.uk/) and the [Inspect Proxmox sandbox provider](https://github.com/UKGovernmentBEIS/inspect_proxmox_sandbox), so they are also useful reference points.

### Running Tests

This project uses pytest with custom markers to categorize tests:

- `unit` - Fast unit tests with no external dependencies
- `vm_required` - Tests that require spinning up actual VMs (slow, requires Vagrant/QEMU)
- `inspect_eval` - Tests that use the Inspect AI evaluation framework

A test case could have both of `vm_required` and `inspect_eval`, neither (unit tests), or only one of the two.

#### Run specific test categories:

```bash
# Run only fast unit tests
uv run pytest -m unit

# Run only VM infrastructure tests
uv run pytest -m vm_required

# Run only Inspect AI evaluation tests
uv run pytest -m inspect_eval

# Run all tests
uv run pytest
```

#### Parallel execution:

Tests support parallel execution using pytest-xdist, which significantly speeds up VM tests:

```bash
# Run tests in parallel with auto-detected worker count
uv run pytest -n auto

# Run with specific number of workers
uv run pytest -n 4
```

**Note:** Parallel execution is especially useful for VM tests - each test uses a separate VM, and multiple VM-based tests can run simultaneously.
