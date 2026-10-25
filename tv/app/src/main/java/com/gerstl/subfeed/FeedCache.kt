package com.gerstl.subfeed

import java.io.File

/** Last good feed JSON on local disk, so the browse screen paints instantly on launch. */
class FeedCache(private val file: File) {
    fun read(): String? = try {
        if (file.isFile) file.readText() else null
    } catch (_: Exception) {
        null
    }

    /** Atomic replace: write a temp file, then rename over the old one. */
    fun write(json: String) {
        file.parentFile?.mkdirs()
        val tmp = File(file.parentFile, file.name + ".tmp")
        tmp.writeText(json)
        if (!tmp.renameTo(file)) {
            file.delete(); tmp.renameTo(file)
        }
    }
}

/**
 * Feed source of truth for the UI: [cached] is synchronous and never touches the network;
 * [refresh] fetches (blocking, call off the main thread), parses, and only then replaces the cache.
 */
class FeedRepository(private val cache: FeedCache, private val fetch: () -> String) {

    fun cached(): Feed? = cache.read()?.let { parse(it) }

    fun refresh(): Feed {
        val json = fetch()
        val feed = parse(json) ?: error("The feed from the service could not be read.")
        cache.write(json)
        return feed
    }

    private fun parse(json: String): Feed? = try {
        SubfeedJson.decodeFromString(Feed.serializer(), json)
    } catch (_: Exception) {
        null
    }
}
