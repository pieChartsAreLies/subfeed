package com.gerstl.subfeed

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FeedInContinueTest {
    @Test
    fun only_videos_listed_in_the_continue_tab_count() {
        val (all, vids) = tab("all", "a:c:p", "b:c")
        val cont = Tab(id = "continue", name = "Continue", kind = "continue", videoIds = listOf("a"))
        val feed = Feed(tabs = listOf(all, cont), videos = vids.associateBy { it.id })
        assertTrue(feed.inContinue("a"))
        assertFalse(feed.inContinue("b"))
        assertFalse(Feed(tabs = listOf(all)).inContinue("a"))
    }
}
