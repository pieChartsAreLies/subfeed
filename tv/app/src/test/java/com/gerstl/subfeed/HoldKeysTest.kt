package com.gerstl.subfeed

import org.junit.Assert.assertEquals
import org.junit.Test

class HoldKeysTest {

    /** Feed a full press-and-hold of [repeats] repeat events and collect what the browse row receives. */
    private fun hold(right: Boolean, repeats: Int): List<Key> =
        (0..repeats).mapNotNull { rc -> HoldKeys.rowKey(right, rc) }

    @Test
    fun one_long_press_is_exactly_one_channel_jump() {
        // A typical long-press releases somewhere between the first repeat and ~0.4 s of repeats.
        for (repeats in 1..9) {
            assertEquals(listOf(Key.Right, Key.LongRight), hold(right = true, repeats = repeats))
            assertEquals(listOf(Key.Left, Key.LongLeft), hold(right = false, repeats = repeats))
        }
    }

    @Test
    fun tap_is_one_item() {
        assertEquals(listOf(Key.Right), hold(right = true, repeats = 0))
    }

    @Test
    fun keeping_it_held_accelerates_item_by_item_only_after_a_clear_delay() {
        val keys = hold(right = true, repeats = 14)
        assertEquals(listOf(Key.Right, Key.LongRight, Key.Right, Key.Right, Key.Right, Key.Right, Key.Right), keys)
    }

    @Test
    fun one_long_press_moves_focus_exactly_one_boundary_in_the_model() {
        val feed = feedOf(tab("all", "a1:A", "a2:A", "a3:A", "b1:B", "b2:B", "c1:C"))
        var f = Focus("all", "a1", inTabs = false)
        for (rc in 0..6) HoldKeys.rowKey(true, rc)?.let { f = FocusModel.onKey(it, feed, f) }
        // Right moves a1 -> a2, then the long-press jumps to the next channel's first item; no overshoot to c1.
        assertEquals("b1", f.videoId)
    }
}
