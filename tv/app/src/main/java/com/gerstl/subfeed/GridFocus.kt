package com.gerstl.subfeed

/**
 * Pure focus rules for the full-screen category grid (OK on a tab opens it). Same order as the
 * tab's row, [COLUMNS] across. Edges stop rather than wrap; long-presses are plain moves here
 * (channel jumps belong to the row).
 */
object GridFocus {
    const val COLUMNS = 5

    fun entry(ids: List<String>): String? = ids.firstOrNull()

    /**
     * Focus after the list was re-fetched: [wanted] if it is still there; else the nearest surviving
     * neighbour in the old list (the one after it first, then before); else the first card.
     */
    fun afterReload(oldIds: List<String>, newIds: List<String>, wanted: String?): String? {
        if (wanted != null && wanted in newIds) return wanted
        val at = oldIds.indexOf(wanted)
        if (at >= 0) {
            val present = newIds.toHashSet()
            (at + 1..oldIds.lastIndex).firstOrNull { oldIds[it] in present }?.let { return oldIds[it] }
            (at - 1 downTo 0).firstOrNull { oldIds[it] in present }?.let { return oldIds[it] }
        }
        return entry(newIds)
    }

    fun move(key: Key, ids: List<String>, current: String?, feed: Feed): String? {
        val i = ids.indexOf(current)
        if (i < 0) return entry(ids)
        val col = i % COLUMNS
        val target = when (key) {
            Key.Up -> if (i >= COLUMNS) i - COLUMNS else i
            Key.Down -> when {
                i + COLUMNS <= ids.lastIndex -> i + COLUMNS
                i / COLUMNS < ids.lastIndex / COLUMNS -> ids.lastIndex
                else -> i
            }
            Key.Left, Key.LongLeft -> if (col == 0) i else i - 1
            Key.Right, Key.LongRight -> if (col == COLUMNS - 1 || i == ids.lastIndex) i else i + 1
            Key.NextUnwatched -> (i + 1..ids.lastIndex).firstOrNull { feed.videos[ids[it]]?.watched == false } ?: i
        }
        return ids[target]
    }

    /** The row to scroll to so one row of context stays above the focused one. */
    fun scrollRow(index: Int): Int = maxOf(0, index / COLUMNS - 1)
}
