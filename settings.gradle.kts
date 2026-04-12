rootProject.name = "jumpstarter"

include("java:jumpstarter-client")
include("java:jumpstarter-driver-network")

include("examples-polyglot-java")
project(":examples-polyglot-java").projectDir = file("examples/polyglot/java")

// Force all subprojects to resolve dev.jumpstarter:jumpstarter-client
// from the local project rather than a remote repository.
// This lets generated build.gradle.kts files use real Maven coordinates
// (for standalone use) while the monorepo resolves them locally.
gradle.allprojects {
    configurations.all {
        resolutionStrategy.dependencySubstitution {
            substitute(module("dev.jumpstarter:jumpstarter-client"))
                .using(project(":java:jumpstarter-client"))
        }
    }
}
