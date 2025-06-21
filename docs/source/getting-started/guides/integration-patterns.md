# Integration Patterns

This document outlines common integration patterns for Jumpstarter, helping you
incorporate it into your development and testing workflows.

Jumpstarter integrates with various tools and platforms across the hardware
development lifecycle:

- **Infrastructure**: Kubernetes, Prometheus, Grafana
- **Developer Environments**: IDE, scripts, GitHub Actions, GitLab CI, Tekton
- **Testing Frameworks**: pytest, unittest, Robot Framework

## Infrastructure

### Continuous Integration with System Testing

```{mermaid}
:config: {"theme":"base","themeVariables":{"primaryColor":"#f8f8f8","primaryTextColor":"#000","primaryBorderColor":"#e5e5e5","lineColor":"#3d94ff","secondaryColor":"#f8f8f8","tertiaryColor":"#fff"}}
flowchart TB
    subgraph "Version Control"
        GitRepo["Git Repository"]
        Actions["GitHub/GitLab CI"]
    end

    subgraph "Jumpstarter Infrastructure"
        Controller["Controller"]
        Exporters["Exporter"]
        DUTs["Device Under Test"]
    end

    GitRepo -- "Code changes" --> Actions
    Actions -- "Request access" --> Controller
    Controller -- "Assign lease" --> Actions
    Controller -- "Connect to" --> Exporters
    Exporters -- "Control" --> DUTs
    Actions -- "Update status" --> GitRepo
```

This architecture integrates Jumpstarter with CI/CD pipelines to enable
automated testing on real systems:

1. Code changes trigger the CI pipeline
2. The pipeline runs tests that use Jumpstarter to access systems
3. Jumpstarter's controller manages device access and leases
4. Test results are reported back to the CI system

**CI Configuration Examples:**

````{tab} GitHub
```yaml
# .github/workflows/hardware-test.yml
jobs:
  hardware-test:
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v3
      - name: Request hardware lease
        run: |
          jmp config client use ci-client
          jmp create lease --selector project=myproject --wait 300
      - name: Run tests
        run: pytest tests/hardware_tests/
      - name: Release hardware lease
        if: always()
        run: jmp delete lease
```
````

````{tab} GitLab
```yaml
# .gitlab-ci.yml
hardware-test:
  tags:
    - self-hosted
  script:
    - jmp config client use ci-client
    - jmp create lease --selector project=myproject --wait 300
    - pytest tests/hardware_tests/
  after_script:
    - jmp delete lease
```
````

### Self-Hosted CI Runner with Attached System

```{mermaid}
:config: {"theme":"base","themeVariables":{"primaryColor":"#f8f8f8","primaryTextColor":"#000","primaryBorderColor":"#e5e5e5","lineColor":"#3d94ff","secondaryColor":"#f8f8f8","tertiaryColor":"#fff"}}
flowchart TB
    subgraph "Version Control"
        GitRepo["Git Repository"]
        Actions["GitHub/GitLab CI"]
    end

    subgraph "Runner"
        Runner1["Self-Hosted Runner"]
        JmpLocal["Local Mode"]
        Devices["Device Under Test"]
    end

    GitRepo -- "Code changes" --> Actions
    Actions -- "Dispatch job" --> Runner1

    Runner1 -- "Execute tests" --> JmpLocal
    JmpLocal -- "Control" --> Devices

    Runner1 -- "Report results" --> Actions
    Actions -- "Update status" --> GitRepo
```

This architecture leverages a self-hosted runner with directly attached system:

1. The self-hosted runner has physical devices connected directly to it
2. Jumpstarter runs in local mode on the runner, controlling the attached system
3. Code changes trigger CI jobs which are dispatched to the runner
4. Tests execute on the runner using Jumpstarter to interface with the system
5. Results are reported back to the CI system

This approach works best when:

- You need to permanently connect systems to a specific test machine
- You want to integrate system testing into existing CI/CD workflows without
  additional infrastructure
- You need a simple setup for initial system-in-the-loop testing

**CI Configuration Examples:**

````{tab} GitHub
```yaml
# .github/workflows/self-hosted-hw-test.yml
jobs:
  hardware-test:
    runs-on: self-hosted-hw-attached
    steps:
      - uses: actions/checkout@v3
      - name: Run Jumpstarter in local mode
        run: jmp local start --config=./.jumpstarter/local-config.yaml
      - name: Run tests
        run: pytest tests/hardware_tests/
      - name: Cleanup
        if: always()
        run: jmp local stop
```
````

````{tab} GitLab
```yaml
# .gitlab-ci.yml
hardware-test:
  tags:
    - hw-attached
  script:
    - jmp local start --config=./.jumpstarter/local-config.yaml
    - pytest tests/hardware_tests/
  after_script:
    - jmp local stop
```
````

### Cost Management and Chargeback

Organizations can implement usage-based billing for teams through a cost
management layer.

```{mermaid}
:config: {"theme":"base","themeVariables":{"primaryColor":"#f8f8f8","primaryTextColor":"#000","primaryBorderColor":"#e5e5e5","lineColor":"#3d94ff","secondaryColor":"#f8f8f8","tertiaryColor":"#fff"}}
flowchart LR
    subgraph "Kubernetes"
        Controller["Controller"]

        subgraph "Telemetry"
            Prometheus["Prometheus"]
            Grafana["Grafana"]
            AlertManager["AlertManager"]
        end

        subgraph "Cost Management"
            UsageTracker["Usage Tracker"]
            OpenCost["OpenCost"]
            Accounting["Chargeback System"]
        end
    end

    subgraph "Lab"
        Rack1["Exporter 1"]
        Rack2["Exporter 2"]
    end

    subgraph "Users"
        Team["Team"]
    end

    Team -- "Request access" --> Controller
    Controller -- "Assign lease" --> Team
    Controller -- "Record lease\nmetadata" --> Prometheus

    Controller -- "Connect to" --> Rack1
    Controller -- "Connect to" --> Rack2

    Rack1 -- "Report usage\nmetrics" --> Prometheus
    Rack2 -- "Report usage\nmetrics" --> Prometheus

    Prometheus -- "Store\nmetrics" --> Grafana
    Prometheus -- "Threshold\nalerts" --> AlertManager
    Prometheus -- "Usage\nmetrics" --> UsageTracker

    UsageTracker -- "Monthly billing\nreport" --> Team

    UsageTracker -- "Team resource\nusage" --> OpenCost
    OpenCost -- "Cost\nallocation" --> Accounting
```

This architecture implements a cost chargeback model for infrastructure
resources:

1. Prometheus collects and stores all resource utilization metrics
2. Teams request resources through the controller, which records team
   identifiers with each lease
3. System resources export detailed utilization metrics to Prometheus:
   - Resource uptime and availability
   - Utilization metrics (CPU, memory, I/O)
   - Team attribution via metadata

## Developer Environments

### Traditional Developer Workflow

```{mermaid}
:config: {"theme":"base","themeVariables":{"primaryColor":"#f8f8f8","primaryTextColor":"#000","primaryBorderColor":"#e5e5e5","lineColor":"#3d94ff","secondaryColor":"#f8f8f8","tertiaryColor":"#fff"}}
flowchart TB
    subgraph "Workstation"
        TestCode["Test Code"]
    end

    subgraph "Local Environment"
        LocalExporter["Local Exporter"]
        DeviceOnDesk["Device Under Test"]
    end

    subgraph "Lab"
        Controller["Controller"]
        RemoteExporters["Exporter"]
        LabDevices["Device Under Test"]
    end

    TestCode --> LocalExporter
    LocalExporter --> DeviceOnDesk

    TestCode -- "Request access" --> Controller
    Controller -- "Assign lease" --> TestCode
    Controller -- "Connect to" --> RemoteExporters
    RemoteExporters --> LabDevices
```

This architecture supports developers working with both local systems and shared
lab resources:

1. Developers write and test code in their IDE
2. For quick tests, they use the test code to access a system on their desk
3. For more complex tests, they connect to remote lab systems through the
   controller
4. The same test code works in both environments

See [Setup Local Mode](setup-local-mode.md) for more information on configuring
your local environment.

### Cloud Native Developer Workflow

```{mermaid}
:config: {"theme":"base","themeVariables":{"primaryColor":"#f8f8f8","primaryTextColor":"#000","primaryBorderColor":"#e5e5e5","lineColor":"#3d94ff","secondaryColor":"#f8f8f8","tertiaryColor":"#fff"}}
flowchart TB
    subgraph "Web Browser"
        Dev["Developer"]
    end

    subgraph "Kubernetes Cluster"
        subgraph "Eclipse Che"
            Workspace["Developer Workspace"]
            TestCode["Test Code"]
            PortFwd["Port Forwarding"]
        end

        Controller["Controller"]
    end

      subgraph "Local Environment"
          LocalExporter["Local Exporter"]
          DeviceOnDesk["Device Under Test"]
      end

      subgraph "Lab"
          RemoteExporters["Exporter"]
          LabDevices["Device Under Test"]
      end

    Dev -- "Access via browser" --> Workspace
    Workspace -- "Contains" --> TestCode

    TestCode -- "Local system access" --> PortFwd
    PortFwd -- "Forward connection" --> LocalExporter
    LocalExporter -- "Control" --> DeviceOnDesk

    TestCode -- "Request access" --> Controller
    Controller -- "Assign lease" --> TestCode
    Controller -- "Connect to" --> RemoteExporters
    RemoteExporters -- "Control" --> LabDevices
```

This architecture provides a cloud-native development experience while
maintaining flexibility to work with both local and remote systems:

1. Developers access a containerized development environment through a web
   browser using Eclipse Che
2. The development workspace contains all necessary tools, dependencies, and
   test code
3. For quick iterations with locally connected systems:
   - Port forwarding enables the cloud workspace to communicate with systems
     connected to the developer's machine
   - The local Jumpstarter exporter manages the device directly
4. For access to shared lab resources:
   - The same test code can request access to remote devices through the
     controller
   - The controller manages leases and routes connections through the standard
     infrastructure

Key benefits of this approach:

- **Consistent Development Environment**: Standardized, reproducible workspaces
  for all team members
- **Flexibility**: Seamless transition between local and remote system testing
- **Collaboration**: Web-based IDE enables real-time collaboration and knowledge
  sharing
- **Scalability**: Easy onboarding of new team members with zero local
  configuration
- **System Flexibility**: Enables a hybrid approach where developers can test
  locally first, then validate on shared lab systems

This workflow eliminates the distinction between local and cloud development
while providing the best of both worlds for system testing.

See [Setup Distributed Mode](setup-distributed-mode.md) for more details on
configuring your distributed environment.

## Testing Frameworks

### pytest Integration

Jumpstarter integrates with pytest through the `jumpstarter-testing` package:

```python
from jumpstarter_testing.pytest import JumpstarterTest

class TestMyDevice(JumpstarterTest):
    # Optional: specify which exporter to use based on labels
    exporter_selector = "vendor=acme,model=widget-v2"

    def test_power_cycle(self):
        # Access the device driver through the provided client
        self.client.power.on()
        assert self.client.serial.read_until("boot complete") is not None
        self.client.power.off()
```

### Robot Framework Integration

For teams using Robot Framework, Jumpstarter drivers can be exposed as keywords:

```robotframework
*** Settings ***
Library    JumpstarterLibrary

*** Test Cases ***
Device Boot Test
    Connect To Exporter    selector=vendor=acme,model=widget-v2
    Power On
    ${output}=    Read Serial Until    boot complete
    Should Not Be Empty    ${output}
    Power Off
```

## Recommended Practices

### Labeling Strategy

Develop a consistent labeling strategy for your exporters to make device
selection straightforward:

- **System Properties**: `arch=arm64`, `cpu=cortex-a53`
- **Organization**: `team=platform`, `project=widget`
- **Capabilities**: `has-video=true`, `has-can=true`
- **Environment**: `env=dev`, `env=production`

### Resource Management

Implement these practices to ensure efficient use of shared systems:

- Set appropriate lease timeouts to prevent orphaned resources
- Use CI systems' concurrency controls to manage test parallelism
- Implement monitoring and alerting for device availability
- Create "pools" of identical devices to improve scalability

### Security Considerations

When deploying Jumpstarter in a multi-user environment:

- Use role-based access control to limit which users can access which devices
- Restrict driver access to prevent untrusted code execution
- Isolate the Jumpstarter network from production systems
- Rotate JWT tokens regularly for enhanced security