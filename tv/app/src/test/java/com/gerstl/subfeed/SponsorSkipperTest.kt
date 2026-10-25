package com.gerstl.subfeed

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class SponsorSkipperTest {

    @Test
    fun sponsor_skips_once_and_undo_suppresses() {
        val s = SponsorSkipper(listOf(Segment(10.0, 20.0), Segment(100.0, 130.0)))
        assertNull(s.onPosition(5_000))
        val a = s.onPosition(10_400)
        assertNotNull(a)
        assertEquals(20_000L, a!!.seekToMs)
        // still reported inside the segment before the seek lands: no second action
        assertNull(s.onPosition(10_600))
        assertNull(s.onPosition(20_000))
        // undo: jump to 2 s before the segment and never skip it again this session
        assertEquals(8_000L, s.undoLast())
        assertNull(s.onPosition(10_500))
        assertNull(s.onPosition(15_000))
        // other segments still skip
        assertEquals(130_000L, s.onPosition(100_200)!!.seekToMs)
        // undo with nothing pending to undo a second time is a no-op
        s.undoLast()
        assertNull(s.undoLast())
    }

    @Test
    fun sponsor_segment_at_zero_skips_before_first_frame() {
        val s = SponsorSkipper(listOf(Segment(0.0, 8.0)))
        assertEquals(8_000L, s.startPosition(0))
        // and the skip is undoable (the note shows)
        assertNotNull(s.lastSkip)
        assertEquals(0L, s.undoLast())
        // resuming inside a segment also starts after it; resuming outside is untouched
        assertEquals(8_000L, SponsorSkipper(listOf(Segment(0.0, 8.0))).startPosition(3_000))
        assertEquals(50_000L, SponsorSkipper(listOf(Segment(0.0, 8.0))).startPosition(50_000))
    }

    @Test
    fun undone_segment_stays_suppressed_after_a_stream_refetch() {
        val segs = listOf(Segment(10.0, 20.0), Segment(100.0, 130.0))
        val first = SponsorSkipper(segs)
        first.onPosition(10_500)
        first.undoLast()
        // a 403 makes the player ask /play again; the new skipper inherits what was undone
        val second = SponsorSkipper(segs, first.suppressedStarts())
        assertNull(second.onPosition(12_000))
        assertNotNull(second.onPosition(100_500))
    }

    @Test
    fun sponsor_ignores_degenerate_segments() {
        val s = SponsorSkipper(listOf(Segment(30.0, 30.2), Segment(50.0, 40.0)))
        assertNull(s.onPosition(30_100))
        assertNull(s.onPosition(45_000))
        assertEquals(0.0, s.totalSeconds(), 0.001)
        assertEquals(10.0, SponsorSkipper(listOf(Segment(5.0, 15.0))).totalSeconds(), 0.001)
    }

    @Test
    fun content_position_subtracts_known_sponsor_seconds_before_position() {
        val s = SponsorSkipper(listOf(Segment(880.0, 940.0)))
        assertEquals(880.0, s.contentPosition(940.0), 0.001)
        assertEquals(500.0, s.contentPosition(500.0), 0.001)
        assertEquals(940.0, s.contentPosition(1000.0), 0.001)
        // inside the segment maps to the segment start
        assertEquals(880.0, s.contentPosition(900.0), 0.001)
    }
}
