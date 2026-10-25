package com.gerstl.subfeed

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PlaybackQueueTest {
    private val always = { _: String -> true }
    private val never = { _: String -> false }

    @Test
    fun queue_is_stable_when_feed_changes() {
        val row = mutableListOf("a", "b", "c", "d")
        val q = PlaybackQueue(row, "b")
        // The feed refreshes mid-session: new uploads arrive at the front, c disappears.
        row.clear(); row.addAll(listOf("n1", "n2", "a", "b", "d"))
        assertEquals("c", q.next(always, never))
        q.moveTo("c")
        assertEquals("d", q.next(always, never))
    }

    @Test
    fun queue_skips_unplayable_max_5_and_stops_at_end() {
        val row = listOf("a", "u1", "u2", "u3", "u4", "u5", "z", "w1")
        val unplayable = setOf("u1", "u2", "u3", "u4", "u5", "u6")
        // exactly 5 skipped -> z
        assertEquals("z", PlaybackQueue(row, "a").next({ it !in unplayable }, never))
        // 6 in a row -> give up
        val six = listOf("a", "u1", "u2", "u3", "u4", "u5", "u6", "z")
        assertNull(PlaybackQueue(six, "a").next({ it !in unplayable }, never))
        // watched items are skipped too, and the row never wraps
        assertNull(PlaybackQueue(row, "z").next(always) { it == "w1" })
        assertNull(PlaybackQueue(row, "w1").next(always, never))
        // the start item not in the snapshot -> nothing to play next
        assertNull(PlaybackQueue(row, "missing").next(always, never))
    }
}
