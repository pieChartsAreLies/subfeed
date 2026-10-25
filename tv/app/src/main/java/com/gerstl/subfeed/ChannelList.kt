package com.gerstl.subfeed

import java.time.Instant

/** Filtering, sorting and letter logic for the Channels screen. Pure so it can be tested. */
object ChannelList {
    enum class Sort { AZ, LEAST_RECENT }

    /** [category]: null = All, "other", "hidden", or "cat-<id>". [letter]: 'A'..'Z' or '#', or null. */
    data class Filter(val category: String?, val letter: Char?)

    val LETTERS: List<Char> = ('A'..'Z').toList() + '#'

    private fun stripThe(name: String): String {
        val t = name.trim()
        return if (t.length > 4 && t.startsWith("the ", ignoreCase = true)) t.substring(4).trimStart() else t
    }

    /** 'A'..'Z' from the first character after a leading "The ", else '#'. */
    fun letter(name: String): Char {
        val c = stripThe(name).firstOrNull()?.uppercaseChar() ?: return '#'
        return if (c in 'A'..'Z') c else '#'
    }

    fun sortKey(name: String): String = stripThe(name).lowercase()

    /** [approved]: ids of the approved categories; a channel in any other category counts as "other".
     *  Null means only uncategorised channels count as "other". */
    fun apply(
        all: List<ChannelInfo>, filter: Filter, sort: Sort, approved: Set<Int>? = null,
    ): List<ChannelInfo> {
        val filtered = all.filter { ch ->
            val catOk = when (val c = filter.category) {
                null -> true
                "hidden" -> ch.hidden
                "other" -> ch.categoryId == null || (approved != null && ch.categoryId !in approved)
                else -> "cat-${ch.categoryId}" == c
            }
            catOk && (filter.letter == null || letter(ch.name) == filter.letter)
        }
        val az = compareBy<ChannelInfo>({ sortKey(it.name) }, { it.name })
        return when (sort) {
            Sort.AZ -> filtered.sortedWith(az)
            Sort.LEAST_RECENT -> filtered.sortedWith(
                compareBy<ChannelInfo> { it.lastWatchedAt != null }
                    .thenBy { it.lastWatchedAt?.let(::epoch) ?: 0L }
                    .then(az)
            )
        }
    }

    private fun epoch(iso: String): Long = try { Instant.parse(iso).toEpochMilli() } catch (_: Exception) { 0L }

    fun showClearFilters(result: List<ChannelInfo>, filter: Filter) =
        result.isEmpty() && (filter.category != null || filter.letter != null)

    /**
     * Focus after the visible list changed from [oldIds] to [newIds]: unchanged while the focused
     * row is still listed; else the row now at the same position (clamped), not row 0.
     */
    fun focusAfter(oldIds: List<String>, newIds: List<String>, focusId: String?): String? {
        if (focusId == null || focusId in newIds) return focusId
        if (newIds.isEmpty()) return null
        val at = oldIds.indexOf(focusId)
        return if (at < 0) newIds.first() else newIds[minOf(at, newIds.lastIndex)]
    }

    fun firstIndexWithLetter(list: List<ChannelInfo>, letter: Char): Int =
        list.indexOfFirst { letter(it.name) == letter }
}
