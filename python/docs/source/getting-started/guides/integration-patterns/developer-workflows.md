# Developer Workflows

## Traditional Developer Workflow

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
   {term}`controller`
4. The same test code works in both environments

See [Setup Local Mode](../setup/local-mode.md) for more information on configuring
your local environment.

## Cloud Native Developer Workflow

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
   - The local Jumpstarter {term}`exporter` manages the {term}`device` directly
4. For access to shared lab resources:
   - The same test code can request access to remote {term}`device`s through the
     {term}`controller`
   - The {term}`controller` manages {term}`lease`s and routes connections through the standard
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

See [Setup Distributed Mode](../setup/distributed-mode.md) for more details on
configuring your distributed environment.
