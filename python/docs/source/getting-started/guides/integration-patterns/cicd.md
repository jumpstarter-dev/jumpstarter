# CI/CD

## Continuous Integration with System Testing

```{mermaid}
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
3. Jumpstarter's {term}`controller` manages {term}`device` access and {term}`lease`s
4. Test results are reported back to the CI system

**CI Configuration Examples:**

````{tab} GitHub
```{code-block} yaml
# .github/workflows/hardware-test.yml
jobs:
  hardware-test:
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v3
      - name: Request hardware lease
        run: |
          jmp config client use ci-client
          LEASE_ID=$(jmp create lease --selector project=myproject --wait 300 -o name)
      - name: Run tests
        run: jmp shell --lease ${LEASE_ID} pytest tests/hardware_tests/
      - name: Release hardware lease
        if: always()
        run: jmp delete lease ${LEASE_ID}
```
````

````{tab} GitLab
```{code-block} yaml
# .gitlab-ci.yml
hardware-test:
  tags:
    - self-hosted
  script:
    - jmp config client use ci-client
    - LEASE_ID=$(jmp create lease --selector project=myproject --wait 300 -o name)
    - jmp shell --lease ${LEASE_ID} pytest tests/hardware_tests/
  after_script:
    - jmp delete lease ${LEASE_ID}
```
````

## Self-Hosted CI Runner with Attached System

```{mermaid}
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

1. The self-hosted runner has physical {term}`device`s connected directly to it
2. Jumpstarter runs in {term}`local mode` on the runner, controlling the attached system
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
```{code-block} yaml
# .github/workflows/self-hosted-hw-test.yml
jobs:
  hardware-test:
    runs-on: self-hosted-hw-attached
    steps:
      - uses: actions/checkout@v3
      - name: Run Jumpstarter in local mode
        run: jmp shell --exporter-config=./.jumpstarter/local-config.yaml pytest test/hardware/tests/
```
````

````{tab} GitLab
```{code-block} yaml
# .gitlab-ci.yml
hardware-test:
  tags:
    - hw-attached
  script:
    - jmp shell --exporter-config=./.jumpstarter/local-config.yaml pytest tests/hardware_tests/
```
````
