package dev.jumpstarter.driver

import com.google.protobuf.DescriptorProtos.FileDescriptorSet
import com.google.protobuf.Descriptors.FileDescriptor

/**
 * Builds the self-contained, **deps-first** serialized `FileDescriptorSet` that the Rust core's
 * descriptor pipeline expects in `DriverNode.descriptorSet`: the interface's `.proto` file plus all
 * its transitive dependency files (e.g. `google/protobuf/empty.proto`), each emitted before the file
 * that imports it. This lets a JVM-authored driver advertise its native gRPC service to the core
 * exactly the way the Python host's `descriptor_builder` does.
 */
object DescriptorSets {
    fun selfContained(root: FileDescriptor): ByteArray {
        val ordered = LinkedHashMap<String, FileDescriptor>()
        fun visit(file: FileDescriptor) {
            if (ordered.containsKey(file.name)) return
            for (dependency in file.dependencies) visit(dependency) // deps first
            ordered[file.name] = file
        }
        visit(root)
        val set = FileDescriptorSet.newBuilder()
        ordered.values.forEach { set.addFile(it.toProto()) }
        return set.build().toByteArray()
    }
}
