package dev.jumpstarter.client

import com.google.gson.JsonParser

/** The well-known label carrying a driver instance's logical name. */
private const val NAME_LABEL = "jumpstarter.dev/name"

/** One driver instance from `GetReport`. */
data class DriverInstance(
    val uuid: String,
    val parentUuid: String?,
    val labels: Map<String, String>,
) {
    /** The driver's logical name (the `jumpstarter.dev/name` label), if any. */
    val name: String? get() = labels[NAME_LABEL]
}

/**
 * The exporter's driver tree, parsed from [dev.jumpstarter.core.ClientSession.getReport]'s JSON
 * (an array of `{uuid, parent_uuid, labels, ...}` nodes the Rust core emits).
 */
class DriverReport(val instances: List<DriverInstance>) {
    fun findByName(name: String): DriverInstance? = instances.firstOrNull { it.name == name }

    fun requireByName(name: String): DriverInstance =
        findByName(name)
            ?: throw NoSuchElementException("no driver named '$name' in exporter report")

    companion object {
        fun parse(json: String): DriverReport {
            val instances = JsonParser.parseString(json).asJsonArray.map { element ->
                val obj = element.asJsonObject
                val labels = obj.getAsJsonObject("labels")
                    ?.entrySet()
                    ?.associate { (key, value) -> key to value.asString }
                    ?: emptyMap()
                DriverInstance(
                    uuid = obj.get("uuid").asString,
                    parentUuid = obj.get("parent_uuid")?.takeUnless { it.isJsonNull }?.asString,
                    labels = labels,
                )
            }
            return DriverReport(instances)
        }
    }
}
