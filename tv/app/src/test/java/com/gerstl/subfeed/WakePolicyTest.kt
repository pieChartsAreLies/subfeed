package com.gerstl.subfeed

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WakePolicyTest {
    private val min = 60_000L

    @Test
    fun playing_keeps_the_screen_on() {
        val w = WakePolicy()
        assertTrue(w.update(0, active = true))
        assertTrue(w.update(60 * min, active = true))
    }

    @Test
    fun releases_three_minutes_after_becoming_inactive_and_rearms_on_play() {
        val w = WakePolicy()
        assertTrue(w.update(0, active = true))
        assertTrue(w.update(1_000, active = false))          // just paused
        assertTrue(w.update(1_000 + 3 * min - 1, active = false))
        assertFalse(w.update(1_000 + 3 * min, active = false))  // released
        assertFalse(w.update(10 * min, active = false))
        assertTrue(w.update(10 * min + 1, active = true))       // playing again
        assertTrue(w.update(12 * min, active = false))          // a new pause starts a new 3 min
        assertFalse(w.update(15 * min, active = false))
    }

    @Test
    fun error_or_endless_loading_counts_as_inactive_from_the_start() {
        val w = WakePolicy()
        assertTrue(w.update(0, active = false))
        assertFalse(w.update(3 * min, active = false))
    }
}
