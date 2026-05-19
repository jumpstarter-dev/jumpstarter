# Cost Management

## Cost Management and Chargeback

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
