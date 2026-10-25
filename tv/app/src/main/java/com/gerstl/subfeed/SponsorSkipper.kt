package com.gerstl.subfeed

/** A sponsor skip that just happened: where the player should jump, and the segment it came from. */
data class SkipAction(val seekToMs: Long, val segmentStartMs: Long)

/**
 * SponsorBlock auto-skip for one playback session (sponsor category only, from `/play`).
 * - Entering a segment produces exactly one [SkipAction] (to the segment's end).
 * - [undoLast] returns a position 2 s before the last skipped segment and disables that segment
 *   for the rest of the session.
 * - [startPosition] applies the skip before the first frame when playback would start inside a segment.
 */
class SponsorSkipper(segments: List<Segment>, inheritedSuppressed: Set<Long> = emptySet()) {
    /** Segments undone this session (by start ms), carried into a new skipper when the stream is re-fetched. */
    fun suppressedStarts(): Set<Long> = suppressed.toSet()
    private data class Seg(val startMs: Long, val endMs: Long)

    private val segs: List<Seg> = segments
        .map { Seg((it.start * 1000).toLong(), (it.end * 1000).toLong()) }
        .filter { it.endMs - it.startMs >= MIN_SEGMENT_MS }
        .sortedBy { it.startMs }
    private val suppressed = HashSet<Long>(inheritedSuppressed)
    private var inFlight: Seg? = null

    var lastSkip: SkipAction? = null
        private set

    fun totalSeconds(): Double = segs.sumOf { it.endMs - it.startMs } / 1000.0

    /** Position with the sponsor seconds before it removed; inside a segment maps to the segment start. */
    fun contentPosition(positionS: Double): Double {
        val posMs = positionS * 1000
        var removed = 0.0
        for (s in segs) {
            if (s.startMs >= posMs) break
            removed += minOf(s.endMs.toDouble(), posMs) - s.startMs
        }
        return maxOf(0.0, positionS - removed / 1000.0)
    }

    fun startPosition(resumeMs: Long): Long {
        val s = active(resumeMs) ?: return resumeMs
        lastSkip = SkipAction(s.endMs, s.startMs)
        return s.endMs
    }

    fun onPosition(ms: Long): SkipAction? {
        val s = active(ms)
        if (s == null) {
            inFlight = null
            return null
        }
        if (s == inFlight) return null   // already skipping this one; the seek has not landed yet
        inFlight = s
        return SkipAction(s.endMs, s.startMs).also { lastSkip = it }
    }

    fun undoLast(): Long? {
        val skip = lastSkip ?: return null
        lastSkip = null
        suppressed += skip.segmentStartMs
        inFlight = null
        return maxOf(0L, skip.segmentStartMs - UNDO_LEAD_MS)
    }

    private fun active(ms: Long): Seg? =
        segs.firstOrNull { ms >= it.startMs && ms < it.endMs - END_SLACK_MS && it.startMs !in suppressed }

    companion object {
        const val UNDO_LEAD_MS = 2_000L
        const val MIN_SEGMENT_MS = 1_000L
        const val END_SLACK_MS = 300L
    }
}
