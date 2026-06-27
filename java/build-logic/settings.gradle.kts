// The `build-logic` included build: shared Gradle convention plugins for the jumpstarter JVM
// monorepo. Registered from the root settings via `pluginManagement { includeBuild("build-logic") }`.
dependencyResolutionManagement {
    repositories {
        gradlePluginPortal()
        mavenCentral()
    }
}

rootProject.name = "build-logic"
