package dev.jumpstarter.driver

import dev.jumpstarter.core.DriverHost
import dev.jumpstarter.core.DriverHostFactory
import io.grpc.BindableService
import io.grpc.protobuf.ProtoFileDescriptorSupplier
import org.yaml.snakeyaml.Yaml
import java.io.File

/**
 * A [DriverHostFactory] whose driver implementation is chosen by an EXPORTER CONFIG `type:` — the
 * JVM analog of how Python selects a driver via a dotted import path and Rust via `rust:<crate>`.
 * For the single `export:` entry, the `type:` names a JVM gRPC service class (grpc-java `…ImplBase`
 * or grpc-kotlin `…CoroutineImplBase`), optionally prefixed `jvm:` (the prefix the exporter hub uses
 * to route the entry to the JVM runtime); this factory reflectively instantiates it, derives its
 * descriptor from the stock service, and serves it through the generic [GrpcServiceDriverHost].
 *
 * The polyglot hub streams a SINGLE-entry config to each per-driver host on stdin, so
 * [fromYaml] is the entrypoint the `jumpstarter-exporter-host` binary uses; [fromFile] supports a
 * standalone invocation against a config path.
 */
class ConfigDrivenHostFactory private constructor(
    private val config: Map<String, Any?>,
) : DriverHostFactory {
    @Suppress("UNCHECKED_CAST")
    override fun newHost(): DriverHost {
        val export = config["export"] as? Map<String, Map<String, Any?>>
            ?: error("exporter config has no export: tree")
        val (name, entry) = export.entries.firstOrNull() ?: error("export: is empty")
        // Strip the `jvm:` runtime prefix (present when the hub selected this runtime by type) to
        // recover the bare service FQN for reflection.
        val type = (entry["type"] as? String)?.removePrefix("jvm:") ?: error("export.$name has no type:")
        val clientClass = entry["client"] as? String ?: defaultClientClass(name)

        val service = Class.forName(type).getDeclaredConstructor().newInstance() as BindableService
        return GrpcServiceDriverHost(service, descriptorOf(service), name, clientClass)
    }

    /** The interface's self-contained descriptor, taken from the stock service's proto file. */
    private fun descriptorOf(service: BindableService): ByteArray {
        val schema = service.bindService().serviceDescriptor.schemaDescriptor
        val file = (schema as? ProtoFileDescriptorSupplier)?.fileDescriptor
            ?: error("service ${service::class.java.name} exposes no proto FileDescriptor")
        return DescriptorSets.selfContained(file)
    }

    /** Convention fallback when the entry omits `client:` — `jumpstarter_driver_<name>.client.<Name>Client`. */
    private fun defaultClientClass(name: String): String {
        val pascal = name.replaceFirstChar { it.uppercase() }
        return "jumpstarter_driver_$name.client.${pascal}Client"
    }

    companion object {
        /** Build from exporter-config YAML text (the single-entry config the hub streams on stdin). */
        @JvmStatic
        fun fromYaml(yamlText: String): ConfigDrivenHostFactory =
            ConfigDrivenHostFactory(Yaml().load(yamlText) as Map<String, Any?>)

        /** Build from an exporter-config file path (standalone invocation). */
        @JvmStatic
        fun fromFile(path: String): ConfigDrivenHostFactory = fromYaml(File(path).readText())
    }
}
