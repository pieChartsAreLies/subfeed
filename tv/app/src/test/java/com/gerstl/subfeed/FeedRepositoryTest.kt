package com.gerstl.subfeed

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Test
import java.io.File
import java.nio.file.Files

class FeedRepositoryTest {

    private val realShape = """
        {"tabs":[{"id":"all","name":"All","kind":"all","new_count":280,"video_ids":["4abjWH9kflY"],"continue_ids":[]},
                 {"id":"saved","name":"Saved","kind":"saved","new_count":0,"video_ids":[],"continue_ids":[]}],
         "videos":{"4abjWH9kflY":{"id":"4abjWH9kflY","channel_id":"UCB51g7B1WMH9Va4M9DM3tHQ","channel_title":"Cabin River Outdoors",
           "category_id":null,"title":"Remote PROPERTY BUILD - Trials & Tribulations","published_at":"2026-10-25T00:36:53Z",
           "duration_s":3740,"thumbnail_url":"https://i.ytimg.com/vi/4abjWH9kflY/maxresdefault.jpg","position_s":0,
           "max_position_s":0,"watched":false,"save_state":null,"unavailable_reason":null,"some_future_field":1}},
         "generated_at":"2026-10-25T07:52:26.370427Z"}
    """.trimIndent()

    private fun tmp(): File = Files.createTempDirectory("feedcache").toFile()

    @Test
    fun cache_paints_before_network() {
        val dir = tmp()
        FeedCache(File(dir, "feed.json")).write(realShape)
        var networkCalls = 0
        val repo = FeedRepository(FeedCache(File(dir, "feed.json"))) { networkCalls++; error("network must not be touched") }
        val feed = repo.cached()
        assertNotNull(feed)
        assertEquals(0, networkCalls)
        assertEquals(listOf("all", "saved"), feed!!.tabs.map { it.id })
        val v = feed.videos.getValue("4abjWH9kflY")
        assertEquals("Cabin River Outdoors", v.channelTitle)
        assertEquals(3740.0, v.durationS!!, 0.0)
        assertEquals(280, feed.tabs[0].newCount)
    }

    @Test
    fun refresh_writes_cache_and_failure_keeps_it() {
        val file = File(tmp(), "feed.json")
        val cache = FeedCache(file)
        assertNull(FeedRepository(cache) { error("offline") }.cached())
        val repo = FeedRepository(cache) { realShape }
        val fresh = repo.refresh()
        assertEquals(1, fresh.videos.size)
        // A failing network afterwards still paints the last good feed.
        val offline = FeedRepository(cache) { error("offline") }
        assertNotNull(offline.cached())
        try {
            offline.refresh(); fail("expected failure")
        } catch (_: Exception) {
        }
        assertNotNull(offline.cached())
    }

    @Test
    fun corrupt_cache_is_ignored() {
        val file = File(tmp(), "feed.json")
        file.writeText("{not json")
        assertNull(FeedRepository(FeedCache(file)) { realShape }.cached())
    }
}
