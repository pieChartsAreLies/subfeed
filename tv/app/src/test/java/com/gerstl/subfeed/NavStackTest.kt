package com.gerstl.subfeed

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NavStackTest {
    @Test
    fun push_and_pop_return_to_previous() {
        val s = NavStack("root")
        assertEquals("root", s.top)
        s.push("a"); s.push("b")
        assertEquals("b", s.top)
        assertTrue(s.pop())
        assertEquals("a", s.top)
        assertTrue(s.pop())
        assertEquals("root", s.top)
    }

    @Test
    fun pop_at_root_returns_false_and_keeps_root() {
        val s = NavStack("root")
        assertFalse(s.pop())
        assertEquals("root", s.top)
    }

    @Test
    fun replace_top_swaps_only_the_top() {
        val s = NavStack("root")
        s.push("a")
        s.replaceTop("a2")
        assertEquals("a2", s.top)
        assertTrue(s.pop())
        assertEquals("root", s.top)
        s.replaceTop("root2")
        assertEquals("root2", s.top)
        assertFalse(s.pop())
    }
}
