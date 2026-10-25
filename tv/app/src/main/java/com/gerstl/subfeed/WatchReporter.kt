package com.gerstl.subfeed

import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import java.io.File

/**
 * Progress that has not reached the service yet, keyed by video id. It outlives a playback session
 * (and, with a [file], the process): the app flushes it at launch and whenever a video starts.
 * Bodies for the same video merge: the furthest position wins and `ended` is sticky, so a retry
 * never moves progress backwards and never loses the end-of-stream signal. Thread-safe.
 */
class ProgressOutbox(private val file: File?) {
    private val map = LinkedHashMap<String, ProgressBody>()
    private val serializer = MapSerializer(String.serializer(), ProgressBody.serializer())

    init {
        try {
            if (file != null && file.isFile) map.putAll(SubfeedJson.decodeFromString(serializer, file.readText()))
        } catch (_: Exception) {
        }
    }

    val size: Int @Synchronized get() = map.size

    @Synchronized fun get(id: String): ProgressBody? = map[id]

    @Synchronized fun put(id: String, body: ProgressBody) {
        map[id] = merge(map[id], body)
        save()
    }

    @Synchronized fun remove(id: String) {
        if (map.remove(id) != null) save()
    }

    /** Try every queued body once; returns how many were delivered. Call off the main thread. */
    fun flush(post: (String, ProgressBody) -> Boolean): Int {
        val snapshot = synchronized(this) { map.toMap() }
        var sent = 0
        for ((id, body) in snapshot) {
            val ok = try { post(id, body) } catch (_: Exception) { false }
            if (ok) {
                synchronized(this) { if (map[id] == body) map.remove(id) }
                sent++
            }
        }
        if (sent > 0) synchronized(this) { save() }
        return sent
    }

    private fun save() {
        val f = file ?: return
        try {
            f.parentFile?.mkdirs()
            val tmp = File(f.parentFile, f.name + ".tmp")
            tmp.writeText(SubfeedJson.encodeToString(serializer, map))
            if (!tmp.renameTo(f)) { f.delete(); tmp.renameTo(f) }
        } catch (_: Exception) {
        }
    }

    companion object {
        fun merge(a: ProgressBody?, b: ProgressBody): ProgressBody {
            if (a == null) return b
            val base = if (a.ended || a.positionS >= b.positionS) a else b
            return base.copy(ended = a.ended || b.ended, sponsorS = maxOf(a.sponsorS, b.sponsorS))
        }
    }
}

/**
 * Reports playback progress for one video. Synchronous: the player calls it from one background
 * thread and never queues a tick while another is still running.
 * - [onTick] attempts a post at most every [INTERVAL_MS] (failed posts are retried on the same cadence).
 * - [flush] posts now (pause, stop, onStop, exit, end of stream).
 * - Anything unsent lives in the [outbox], so it survives this reporter being replaced or released.
 */
class WatchReporter(
    private val videoId: String,
    private val post: (String, ProgressBody) -> Boolean,
    private val outbox: ProgressOutbox,
) {
    val pendingBody: ProgressBody? get() = outbox.get(videoId)
    val hasPending: Boolean get() = pendingBody != null
    private var lastAttemptAt: Long? = null

    fun onTick(nowMs: Long, body: ProgressBody) {
        val last = lastAttemptAt
        if (last != null && nowMs - last < INTERVAL_MS) return
        lastAttemptAt = nowMs
        send(body)
    }

    fun flush(body: ProgressBody) = send(body)

    private fun send(body: ProgressBody) {
        val merged = ProgressOutbox.merge(outbox.get(videoId), body)
        val ok = try {
            post(videoId, merged)
        } catch (_: Exception) {
            false
        }
        if (ok) outbox.remove(videoId) else outbox.put(videoId, merged)
    }

    companion object {
        const val INTERVAL_MS = 15_000L
    }
}
