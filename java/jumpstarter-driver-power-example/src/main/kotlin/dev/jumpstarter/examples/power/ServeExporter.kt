package dev.jumpstarter.examples.power

import dev.jumpstarter.core.DriverHost
import dev.jumpstarter.core.DriverHostFactory
import dev.jumpstarter.core.runExporter
import dev.jumpstarter.driver.DescriptorSets
import dev.jumpstarter.driver.GrpcServiceDriverHost
import io.grpc.BindableService
import io.grpc.protobuf.ProtoFileDescriptorSupplier
import kotlinx.coroutines.runBlocking
import org.yaml.snakeyaml.Yaml
import java.io.File

/**
 * A controller-mediated exporter whose driver implementation is chosen by the EXPORTER CONFIG — the
 * JVM analog of how Python selects a driver via `type:`. For each `export:` entry, the `type:` names
 * a JVM gRPC service class (grpc-java `…ImplBase` or grpc-kotlin `…CoroutineImplBase`); this exporter
 * reflectively instantiates it, derives its descriptor from the stock service, and serves it through
 * the generic [GrpcServiceDriverHost]. The whole controller-mediated lifecycle (register, lease,
 * serve) runs in-process via the Rust core's `run_exporter`.
 *
 * Usage: `ServeExporter <exporter-config.yaml>` — e.g. a config whose power entry is
 * `type: dev.jumpstarter.examples.power.KotlinPowerDriver`.
 */
fun main(args: Array<String>) {
    val configPath = args.firstOrNull() ?: error("usage: ServeExporter <exporter-config.yaml>")
    runBlocking {
        runExporter(configPath, ConfigDrivenHostFactory(configPath))
    }
}

/**
 * Builds a [DriverHost] serving the JVM gRPC service class named by the `export:` entry's `type:`.
 * Single-entry (the common case); a multi-driver exporter would compose one host per entry.
 */
class ConfigDrivenHostFactory(private val configPath: String) : DriverHostFactory {
    @Suppress("UNCHECKED_CAST")
    override fun newHost(): DriverHost {
        val config = Yaml().load<Map<String, Any?>>(File(configPath).readText())
        val export = config["export"] as? Map<String, Map<String, Any?>>
            ?: error("exporter config has no export: tree")
        val (name, entry) = export.entries.firstOrNull() ?: error("export: is empty")
        val type = entry["type"] as? String ?: error("export.$name has no type:")
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
}
