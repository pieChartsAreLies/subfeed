package com.gerstl.subfeed

/**
 * The autoplay queue for one playback session: the tab's row as it was when playback started.
 * Feed refreshes never change it. "Next" = the first later item that is playable and unwatched,
 * skipping at most [MAX_SKIPS] items; it never wraps to the start of the row.
 */
class PlaybackQueue(tabSnapshot: List<String>, startId: String) {
    private val ids: List<String> = tabSnapshot.toList()
    private var current: Int = ids.indexOf(startId)

    val currentId: String? get() = ids.getOrNull(current)

    fun next(isPlayable: (String) -> Boolean, isWatched: (String) -> Boolean): String? {
        if (current < 0) return null
        var skipped = 0
        for (i in current + 1..ids.lastIndex) {
            val id = ids[i]
            if (isPlayable(id) && !isWatched(id)) return id
            if (++skipped > MAX_SKIPS) return null
        }
        return null
    }

    fun moveTo(id: String) {
        val i = ids.indexOf(id)
        if (i >= 0) current = i
    }

    companion object {
        const val MAX_SKIPS = 5
    }
}
