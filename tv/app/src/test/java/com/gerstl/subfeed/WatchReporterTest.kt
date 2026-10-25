package com.gerstl.subfeed

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files

class WatchReporterTest {

    private class FakeServer {
        var up = true
        val received = mutableListOf<Pair<String, ProgressBody>>()
        var attempts = 0
        fun post(id: String, b: ProgressBody): Boolean {
            attempts++
            if (!up) return false
            received += id to b; return true
        }
    }

    private fun body(pos: Double, ended: Boolean = false) = ProgressBody(pos, 600.0, 12.0, ended)

    @Test
    fun reporter_posts_every_15s() {
        val srv = FakeServer()
        val r = WatchReporter("v", srv::post, ProgressOutbox(null))
        r.onTick(0, body(0.0))
        r.onTick(10_000, body(10.0))
        r.onTick(15_000, body(15.0))
        r.onTick(29_000, body(29.0))
        r.onTick(30_000, body(30.0))
        assertEquals(listOf(0.0, 15.0, 30.0), srv.received.map { it.second.positionS })
    }

    @Test
    fun reporter_flushes_on_stop_and_retries() {
        val srv = FakeServer()
        val r = WatchReporter("v", srv::post, ProgressOutbox(null))
        r.onTick(0, body(0.0))
        srv.up = false
        r.flush(body(42.0))                 // pause while the service is unreachable
        r.flush(body(600.0, ended = true))  // the video ended, still unreachable
        r.flush(body(300.0))                // a stale position must not undo the end
        assertTrue(r.hasPending)
        srv.up = true
        r.onTick(15_000, body(300.0))       // the next 15 s tick retries
        assertNull(r.pendingBody)
        val last = srv.received.last().second
        assertEquals(600.0, last.positionS, 0.0)
        assertTrue(last.ended)
        // onStop / onDestroy flush posts immediately
        r.flush(body(310.0))
        assertEquals(310.0, srv.received.last().second.positionS, 0.0)
    }

    @Test
    fun reporter_retries_on_the_15s_cadence_not_every_tick() {
        val srv = FakeServer()
        srv.up = false
        val r = WatchReporter("v", srv::post, ProgressOutbox(null))
        var t = 0L
        while (t < 15_000) { r.onTick(t, body(t / 1000.0)); t += 250 }
        assertEquals(1, srv.attempts)       // 60 ticks, one attempt
        r.onTick(15_000, body(15.0))
        assertEquals(2, srv.attempts)
    }

    @Test
    fun unsent_progress_outlives_the_session_and_is_flushed_later() {
        val file = File(Files.createTempDirectory("outbox").toFile(), "progress.json")
        val srv = FakeServer()
        srv.up = false
        val r = WatchReporter("v1", srv::post, ProgressOutbox(file))
        r.flush(body(600.0, ended = true))
        // the player session ends; a later launch reads the outbox from disk
        val later = ProgressOutbox(file)
        assertEquals(true, later.get("v1")?.ended)
        srv.up = true
        assertEquals(1, later.flush(srv::post))
        assertTrue(srv.received.single().second.ended)
        assertNull(ProgressOutbox(file).get("v1"))
    }

    @Test
    fun outbox_merges_per_video_ended_sticky_furthest_position_wins() {
        val o = ProgressOutbox(null)
        o.put("a", body(100.0))
        o.put("a", body(600.0, ended = true))
        o.put("a", body(50.0))
        o.put("b", body(10.0))
        assertEquals(600.0, o.get("a")!!.positionS, 0.0)
        assertTrue(o.get("a")!!.ended)
        assertEquals(10.0, o.get("b")!!.positionS, 0.0)
        val srv = FakeServer()
        srv.up = false
        assertEquals(0, o.flush(srv::post))
        assertEquals(2, o.size)
    }
}
