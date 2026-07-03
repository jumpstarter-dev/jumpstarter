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

    /**
     * Resolve a NAME PATH (`findByPath("dut", "power")`) — tree-aware, disambiguating same-named
     * nodes under different parents (which the flat [findByName] cannot). The first segment is a
     * root node or a direct child of an unnamed root (the hub's synthetic composite); each
     * further segment is a child of the previous node. Generated device wrappers resolve every
     * configured node this way.
     */
    fun findByPath(vararg names: String): DriverInstance? {
        if (names.isEmpty()) return null
        val roots = instances.filter { it.parentUuid == null }.map { it.uuid }.toSet()
        val first = names.first()
        var node = instances.firstOrNull { it.parentUuid == null && it.name == first }
            ?: run {
                val candidates = instances.filter { it.name == first && it.parentUuid in roots }
                when {
                    candidates.size > 1 -> throw IllegalStateException(
                        "driver name '$first' is ambiguous at the tree root; qualify the path",
                    )
                    else -> candidates.firstOrNull()
                }
            }
            ?: return null
        for (segment in names.drop(1)) {
            node = instances.firstOrNull { it.parentUuid == node.uuid && it.name == segment }
                ?: return null
        }
        return node
    }

    fun requireByPath(vararg names: String): DriverInstance =
        findByPath(*names)
            ?: throw NoSuchElementException(
                "no driver at path '${names.joinToString("/")}' in exporter report",
            )

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
