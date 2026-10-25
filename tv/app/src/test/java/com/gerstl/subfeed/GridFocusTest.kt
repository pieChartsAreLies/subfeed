package com.gerstl.subfeed

import org.junit.Assert.assertEquals
import org.junit.Test

class GridFocusTest {

    // 12 items = rows of 5, 5, 2. v7 watched, v8 watched.
    private val feed = feedOf(
        tab("all", *(0 until 12).map { if (it == 7 || it == 8) "v$it:c:w" else "v$it:c" }.toTypedArray()),
        tab("saved"),
    )
    private val ids = feed.tab("all")!!.videoIds

    private fun move(key: Key, from: String) = GridFocus.move(key, ids, from, feed)

    @Test
    fun entry_is_top_left() {
        assertEquals("v0", GridFocus.entry(ids))
        assertEquals(null, GridFocus.entry(emptyList()))
    }

    @Test
    fun up_and_down_move_one_row_and_stop_at_the_edges() {
        assertEquals("v6", move(Key.Down, "v1"))
        assertEquals("v1", move(Key.Up, "v6"))
        assertEquals("v2", move(Key.Up, "v2"))          // top row: Up does nothing
        assertEquals("v11", move(Key.Down, "v11"))      // last row: Down does nothing
    }

    @Test
    fun down_into_a_short_last_row_lands_on_the_last_item() {
        assertEquals("v10", move(Key.Down, "v5"))
        assertEquals("v11", move(Key.Down, "v8"))       // column 3 has nothing below; go to the last item
    }

    @Test
    fun left_and_right_stay_within_the_row() {
        assertEquals("v2", move(Key.Right, "v1"))
        assertEquals("v4", move(Key.Right, "v4"))       // right edge
        assertEquals("v5", move(Key.Left, "v5"))        // left edge
        assertEquals("v11", move(Key.Right, "v11"))     // last item
        assertEquals("v3", move(Key.LongLeft, "v4"))    // long-press is just a move in the grid
        assertEquals("v4", move(Key.LongRight, "v3"))
        assertEquals("v4", move(Key.LongRight, "v4"))
    }

    @Test
    fun fast_forward_jumps_to_the_next_unwatched() {
        assertEquals("v9", move(Key.NextUnwatched, "v6"))
        assertEquals("v11", move(Key.NextUnwatched, "v11"))
    }

    @Test
    fun unknown_focus_falls_back_to_entry() {
        assertEquals("v0", move(Key.Down, "gone"))
        assertEquals(null, GridFocus.move(Key.Down, emptyList(), null, feed))
    }

    @Test
    fun scroll_row_keeps_one_row_above_the_focus() {
        assertEquals(0, GridFocus.scrollRow(ids.indexOf("v3")))
        assertEquals(0, GridFocus.scrollRow(ids.indexOf("v6")))
        assertEquals(1, GridFocus.scrollRow(ids.indexOf("v11")))
    }

    @Test
    fun afterReload_keeps_wanted_else_neighbour_else_first() {
        val old = listOf("a", "b", "c", "d")
        assertEquals("b", GridFocus.afterReload(old, listOf("a", "b", "c"), "b"))
        assertEquals("c", GridFocus.afterReload(old, listOf("a", "c", "d"), "b"))   // b vanished: next survivor
        assertEquals("c", GridFocus.afterReload(old, listOf("a", "c"), "d"))          // last vanished: previous
        assertEquals("x", GridFocus.afterReload(old, listOf("x", "y"), "b"))          // no survivors of old: first new
        assertEquals("a", GridFocus.afterReload(emptyList(), listOf("a", "b"), null))
        assertEquals(null, GridFocus.afterReload(old, emptyList(), "b"))
    }
}
