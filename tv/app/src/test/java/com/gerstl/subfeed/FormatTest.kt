package com.gerstl.subfeed

import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.Instant

class FormatTest {
    private val now = Instant.parse("2026-10-25T12:00:00Z")

    @Test
    fun ages_read_like_a_person_would_say_them() {
        assertEquals("Just now", Format.age("2026-10-25T11:59:30Z", now))
        assertEquals("25 minutes ago", Format.age("2026-10-25T11:35:00Z", now))
        assertEquals("1 hour ago", Format.age("2026-10-25T10:50:00Z", now))
        assertEquals("5 hours ago", Format.age("2026-10-25T07:00:00Z", now))
        assertEquals("Yesterday", Format.age("2026-10-24T09:00:00Z", now))
        assertEquals("4 days ago", Format.age("2026-10-21T09:00:00Z", now))
        assertEquals("3 weeks ago", Format.age("2026-10-02T09:00:00Z", now))
        assertEquals("5 months ago", Format.age("2026-05-20T09:00:00Z", now))
        assertEquals("", Format.age(null, now))
    }

    @Test
    fun lengths() {
        assertEquals("1:02:20", Format.length(3740.0))
        assertEquals("11:30", Format.length(690.0))
        assertEquals("0:09", Format.length(9.0))
        assertEquals("", Format.length(null))
        assertEquals("1 h 2 min", Format.lengthWords(3740.0))
        assertEquals("11 min", Format.lengthWords(690.0))
    }

    @Test
    fun description_first_two_lines_skip_blank_and_links() {
        val d = "\n\nFirst real line.\n\nhttps://example.com/x\nSecond line here.\nThird."
        assertEquals("First real line.\nSecond line here.", Format.firstLines(d, 2))
    }
}
