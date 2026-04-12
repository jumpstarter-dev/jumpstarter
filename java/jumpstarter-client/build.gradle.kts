plugins {
    java
    alias(libs.plugins.protobuf)
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

dependencies {
    implementation(libs.grpc.netty.shaded)
    implementation(libs.grpc.protobuf)
    implementation(libs.grpc.stub)
    implementation(libs.protobuf.java)
    implementation(libs.protobuf.java.util)
    implementation(libs.jetbrains.annotations)
    implementation(libs.googleapis.common.protos)
    implementation(libs.grpc.googleapis)

    // Required for javax.annotation.Generated used by grpc-java codegen
    compileOnly("org.apache.tomcat:annotations-api:6.0.53")

    // JUnit 5 API — needed at compile time for JumpstarterExtension and @JumpstarterDevice
    compileOnly(libs.junit.jupiter)

    testImplementation(libs.junit.jupiter)
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
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

sourceSets {
    main {
        proto {
            // Use Java-specific proto overlay directory exclusively.
            // This replaces jumpstarter.proto with a version that excludes
            // DriverInterfaceInfo (its "descriptor" field conflicts with
            // protobuf-java's reserved getDescriptor() method).
            // common.proto and router.proto are copied from protocol/proto.
            srcDir("proto-overlay")
        }
    }
}

tasks.test {
    useJUnitPlatform {
        excludeTags("integration")
    }
    testLogging {
        events("passed", "skipped", "failed")
        showStandardStreams = true
    }
}

// Integration tests — run with: ./gradlew integrationTest
// Requires JUMPSTARTER_HOST (run inside jmp shell)
tasks.register<Test>("integrationTest") {
    description = "Run integration tests inside a jmp shell session"
    group = "verification"
    testClassesDirs = sourceSets.test.get().output.classesDirs
    classpath = sourceSets.test.get().runtimeClasspath
    useJUnitPlatform {
        includeTags("integration")
    }
    testLogging {
        events("passed", "skipped", "failed")
        showStandardStreams = true
    }
    // Pass through Jumpstarter environment variables
    environment("JUMPSTARTER_HOST", System.getenv("JUMPSTARTER_HOST") ?: "")
    environment("JMP_GRPC_INSECURE", System.getenv("JMP_GRPC_INSECURE") ?: "")
    environment("JMP_GRPC_PASSPHRASE", System.getenv("JMP_GRPC_PASSPHRASE") ?: "")
}
