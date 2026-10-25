package com.gerstl.subfeed

import java.time.Duration
import java.time.Instant

/** Couch-readable strings. */
object Format {

    fun age(iso: String?, now: Instant = Instant.now()): String {
        val t = try {
            Instant.parse(iso ?: return "")
        } catch (_: Exception) {
            return ""
        }
        val s = Duration.between(t, now).seconds
        fun plural(n: Long, unit: String) = if (n == 1L) "1 $unit ago" else "$n ${unit}s ago"
        return when {
            s < 60 -> "Just now"
            s < 3600 -> plural(s / 60, "minute")
            s < 86_400 -> plural(s / 3600, "hour")
            s < 2 * 86_400 -> "Yesterday"
            s < 14 * 86_400 -> plural(s / 86_400, "day")
            s < 60 * 86_400 -> plural(s / (7 * 86_400), "week")
            s < 365 * 86_400 -> plural(s / (30 * 86_400), "month")
            else -> plural(s / (365 * 86_400), "year")
        }
    }

    fun length(seconds: Double?): String {
        val total = seconds?.toLong() ?: return ""
        val h = total / 3600
        val m = (total % 3600) / 60
        val s = total % 60
        return if (h > 0) "%d:%02d:%02d".format(h, m, s) else "%d:%02d".format(m, s)
    }

    fun lengthWords(seconds: Double?): String {
        val total = seconds?.toLong() ?: return ""
        val h = total / 3600
        val m = (total % 3600) / 60
        return when {
            h > 0 && m > 0 -> "$h h $m min"
            h > 0 -> "$h h"
            m > 0 -> "$m min"
            else -> "${total} s"
        }
    }

    /** First [n] meaningful lines of a description: blank lines, bare links and hashtag lines dropped. */
    fun firstLines(text: String?, n: Int): String {
        if (text.isNullOrBlank()) return ""
        return text.lineSequence()
            .map { it.trim() }
            .filter { it.isNotEmpty() && !it.startsWith("http") && !it.startsWith("#") }
            .take(n)
            .joinToString("\n")
    }

    fun clock(ms: Long): String = length(ms / 1000.0)
}
