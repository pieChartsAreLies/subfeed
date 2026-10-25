package com.gerstl.subfeed

import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class HealthSentencesTest {
    private val now = Instant.parse("2026-10-25T08:30:00Z")

    @Test
    fun real_health_shape_reads_as_plain_sentences() {
        val j = SubfeedJson.parseToJsonElement(
            """{"ok":true,"last_sub_sync":null,"last_poll":"2026-10-25T07:58:39.061546Z","ytdlp_version":"2026.10.21",
               "last_canary":"2026-10-25T08:10:34.641642Z","canary_ok":true,"saved_bytes":0,"saved_quota_bytes":268435456000,
               "nas_mounted":true,"jobs":[],"youtube_limited":false,"message":null}"""
        ).jsonObject
        val lines = HealthSentences.from(j, now)
        assertEquals(true to "YouTube is playing normally.", lines[0])
        assertTrue(lines.contains(true to "New uploads were last checked 31 minutes ago."))
        assertTrue(lines.contains(true to "Saved videos use 0.0 GB of 250 GB."))
        assertTrue(lines.any { !it.first && it.second.startsWith("Your subscriptions haven't synced") })
    }

    @Test
    fun limited_and_nas_down_say_so() {
        val j = SubfeedJson.parseToJsonElement(
            """{"youtube_limited":true,"message":"YouTube is limiting us right now. Your saved videos still work.","nas_mounted":false,"last_sub_sync":"x"}"""
        ).jsonObject
        val lines = HealthSentences.from(j, now)
        assertEquals(false to "YouTube is limiting us right now. Your saved videos still work.", lines[0])
        assertTrue(lines.contains(false to "Can't reach the NAS, so saving is paused."))
    }
}
