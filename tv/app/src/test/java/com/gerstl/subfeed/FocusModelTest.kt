package com.gerstl.subfeed

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FocusModelTest {

    private val longRow = feedOf(
        tab("all", *(0 until 60).map { "v$it:c${it / 4}" }.toTypedArray()),
        tab("other", "o1:x", "o2:x"),
    )

    @Test
    fun up_from_row_goes_to_tabs_from_any_index() {
        for (i in 0 until 60) {
            val f = FocusModel.onKey(Key.Up, longRow, Focus("all", "v$i", inTabs = false))
            assertTrue("index $i should land on tabs", f.inTabs)
            assertEquals("all", f.tabId)
            // Down from tabs returns to the same item, not the start of the row.
            val back = FocusModel.onKey(Key.Down, longRow, f)
            assertFalse(back.inTabs)
            assertEquals("v$i", back.videoId)
        }
    }

    @Test
    fun switching_tab_starts_at_first_item_or_continue() {
        val feed = feedOf(
            tab("all", "a1:x", "a2:x", "a3:x"),
            tab("cat-1", "p1:y:p", "n1:y", "n2:y"),          // in-progress first
            tab("cat-2", "w1:z:w", "u1:z", "u2:z"),           // no continue: newest unwatched
            tab("cat-3", "w2:q:w", "w3:q:w"),                 // all watched: newest
            tab("saved"),                                     // empty
        )
        var f = Focus("all", "a3", inTabs = true)
        f = FocusModel.onKey(Key.Right, feed, f)
        assertEquals(Focus("cat-1", "p1", inTabs = true), f)
        f = FocusModel.onKey(Key.Right, feed, f)
        assertEquals(Focus("cat-2", "u1", inTabs = true), f)
        f = FocusModel.onKey(Key.Right, feed, f)
        assertEquals(Focus("cat-3", "w2", inTabs = true), f)
        f = FocusModel.onKey(Key.Right, feed, f)
        assertEquals(Focus("saved", null, inTabs = true), f)
        // Right on the last tab stays put; Down on an empty tab stays on tabs.
        assertEquals(f, FocusModel.onKey(Key.Right, feed, f))
        assertEquals(f, FocusModel.onKey(Key.Down, feed, f))
        // Coming back left re-enters at the entry item, not where we were before.
        f = FocusModel.onKey(Key.Left, feed, FocusModel.onKey(Key.Left, feed, FocusModel.onKey(Key.Left, feed, FocusModel.onKey(Key.Left, feed, f))))
        assertEquals(Focus("all", "a1", inTabs = true), f)
        // Left on the first tab stays put.
        assertEquals(f, FocusModel.onKey(Key.Left, feed, f))
    }

    @Test
    fun row_left_right_moves_one_item_and_clamps() {
        var f = Focus("all", "v0", inTabs = false)
        f = FocusModel.onKey(Key.Left, longRow, f)
        assertEquals("v0", f.videoId)
        f = FocusModel.onKey(Key.Right, longRow, f)
        assertEquals("v1", f.videoId)
        f = FocusModel.onKey(Key.Right, longRow, Focus("all", "v59", false))
        assertEquals("v59", f.videoId)
    }

    @Test
    fun reconcile_keeps_same_video_id_after_refresh() {
        val old = feedOf(tab("all", "a:x", "b:x", "c:x"))
        val new = feedOf(tab("all", "n1:y", "n2:y", "a:x", "b:x", "c:x"))
        val f = FocusModel.reconcile(old, new, Focus("all", "b", inTabs = false))
        assertEquals(Focus("all", "b", inTabs = false), f)
    }

    @Test
    fun reconcile_moves_to_neighbor_when_video_removed() {
        val old = feedOf(tab("all", "a:x", "b:x", "c:x", "d:x"))
        // b removed -> the next surviving item after it (c)
        assertEquals("c", FocusModel.reconcile(old, feedOf(tab("all", "a:x", "c:x", "d:x")), Focus("all", "b", false)).videoId)
        // b and c removed -> d
        assertEquals("d", FocusModel.reconcile(old, feedOf(tab("all", "a:x", "d:x")), Focus("all", "b", false)).videoId)
        // last item removed -> the previous one
        assertEquals("c", FocusModel.reconcile(old, feedOf(tab("all", "a:x", "b:x", "c:x")), Focus("all", "d", false)).videoId)
        // the whole tab vanished -> first tab's entry, on the tab strip
        val gone = FocusModel.reconcile(feedOf(tab("all", "a:x"), tab("cat-9", "z:z")), feedOf(tab("all", "a:x")), Focus("cat-9", "z", false))
        assertEquals(Focus("all", "a", inTabs = true), gone)
        // tab emptied -> no video
        assertEquals(null, FocusModel.reconcile(old, feedOf(tab("all")), Focus("all", "b", false)).videoId)
    }

    @Test
    fun fast_jump_to_next_channel_and_next_unwatched() {
        val feed = feedOf(tab("all", "a1:A", "a2:A:w", "a3:A", "b1:B:w", "b2:B:w", "c1:C", "c2:C"))
        fun key(k: Key, id: String) = FocusModel.onKey(k, feed, Focus("all", id, false)).videoId
        assertEquals("b1", key(Key.LongRight, "a1"))
        assertEquals("b1", key(Key.LongRight, "a2"))
        assertEquals("c1", key(Key.LongRight, "b1"))
        assertEquals("c2", key(Key.LongRight, "c1"))   // no further channel: end of row
        assertEquals("b1", key(Key.LongLeft, "b2"))    // mid-run: start of this channel
        assertEquals("a1", key(Key.LongLeft, "b1"))    // at a run start: previous channel
        assertEquals("a1", key(Key.LongLeft, "a1"))
        assertEquals("a3", key(Key.NextUnwatched, "a1"))
        assertEquals("c1", key(Key.NextUnwatched, "a3"))
        assertEquals("c2", key(Key.NextUnwatched, "c2"))  // none left: stay
        assertTrue(FocusModel.isChannelBoundary(feed.tab("all")!!, feed, 3))
        assertFalse(FocusModel.isChannelBoundary(feed.tab("all")!!, feed, 4))
    }

    @Test
    fun initial_focus_prefers_remembered_tab_and_falls_back() {
        val feed = feedOf(tab("all", "a:x"), tab("cat-1", "b:y"))
        assertEquals(Focus("cat-1", "b", inTabs = false), FocusModel.initial(feed, "cat-1"))
        assertEquals(Focus("all", "a", inTabs = false), FocusModel.initial(feed, "cat-77"))
        assertEquals(Focus("all", "a", inTabs = false), FocusModel.initial(feed, null))
    }

    @Test
    fun hiding_focused_channel_moves_to_neighbour() {
        val old = feedOf(tab("all", "a:x", "b:y", "c:z"))
        val new = feedOf(tab("all", "a:x", "c:z"))   // b's channel is hidden
        assertEquals(Focus("all", "c", inTabs = false), FocusModel.reconcile(old, new, Focus("all", "b", inTabs = false)))
    }

    @Test
    fun in_progress_leaving_row_moves_to_neighbour() {
        val old = feedOf(tab("continue", "a:x:p", "b:y:p", "c:z:p"))
        val new = feedOf(tab("continue", "a:x:p", "c:z:p"))   // b finished, left Continue Watching
        assertEquals(Focus("continue", "c", inTabs = false), FocusModel.reconcile(old, new, Focus("continue", "b", inTabs = false)))
    }

    @Test
    fun history_row_is_capped_at_20_but_the_tab_keeps_all() {
        val hist = tab("history", *(0 until 30).map { "h$it:c$it" }.toTypedArray())
        val feed = feedOf(hist.copy(first = hist.first.copy(kind = "history")))
        val t = feed.tabs.first()
        assertEquals(30, t.videoIds.size)
        assertEquals(20, t.rowIds.size)
        // Right from the last row card stays put instead of reaching h20
        assertEquals("h19", FocusModel.onKey(Key.Right, feed, Focus("history", "h19", false)).videoId)
        // while the grid (all 200) is open, a refresh keeps focus beyond the cap
        val f = Focus("history", "h25", false)
        assertEquals(f, FocusModel.reconcile(feed, feed, f, gridOpen = true))
        assertEquals("h0", FocusModel.reconcile(feed, feed, f).videoId)
    }

    @Test
    fun reconcile_keeps_card_past_row_cap_when_grid_open() {
        val (t, vids) = tab("history", *(0 until 60).map { "h$it:c$it" }.toTypedArray())
        val feed = feedOf(t.copy(kind = "history") to vids)
        val f = Focus("history", "h30", inTabs = false)
        assertEquals(f, FocusModel.reconcile(feed, feed, f, gridOpen = true))
        // without the grid the row is capped at 20, so the card snaps to the entry item
        assertTrue(FocusModel.reconcile(feed, feed, f).videoId != "h30")
    }
}
