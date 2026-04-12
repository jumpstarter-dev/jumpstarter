rootProject.name = "jumpstarter"

include("java:jumpstarter-client")

include("examples-polyglot-java")
project(":examples-polyglot-java").projectDir = file("examples/polyglot/java/gen")
