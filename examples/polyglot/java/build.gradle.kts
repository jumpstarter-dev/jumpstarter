// Monorepo build config for the polyglot Java example.
// This file lives OUTSIDE gen/ so it is not overwritten by `jmp codegen`.
//
// The generated build.gradle.kts inside gen/ uses standalone Maven coordinates
// for external users. This file adds monorepo-specific configuration:
//   - Proto source compilation from gen/src/main/proto/
//   - Hand-written test sources from src/test/java/
//   - Project dependency on :java:jumpstarter-client (via substitution)

plugins {
    java
    alias(libs.plugins.protobuf)
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

dependencies {
    // Standalone coordinate — resolved to local project by settings.gradle.kts substitution
    implementation("dev.jumpstarter:jumpstarter-client:0.1.0-SNAPSHOT")
    implementation(libs.grpc.netty.shaded)
    implementation(libs.grpc.protobuf)
    implementation(libs.grpc.stub)
    implementation(libs.protobuf.java)
    implementation(libs.jetbrains.annotations)
    compileOnly("org.apache.tomcat:annotations-api:6.0.53")

    testImplementation(libs.junit.jupiter)
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

sourceSets {
    main {
        // Generated typed wrappers from jmp codegen
        java.srcDir("gen/src/main/java")
        // Proto files for protoc compilation
        proto.srcDir("gen/src/main/proto")
    }
    test {
        // Hand-written tests (outside gen/)
        java.srcDir("src/test/java")
    }
}

protobuf {
    protoc {
        artifact = "com.google.protobuf:protoc:${libs.versions.protobuf.get()}"
    }
    plugins {
        create("grpc") {
            artifact = "io.grpc:protoc-gen-grpc-java:${libs.versions.grpc.get()}"
        }
    }
    generateProtoTasks {
        all().forEach { task ->
            task.plugins {
                create("grpc")
            }
        }
    }
}

tasks.test {
    useJUnitPlatform()
}
