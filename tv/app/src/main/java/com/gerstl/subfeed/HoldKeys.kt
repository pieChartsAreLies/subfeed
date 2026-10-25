package com.gerstl.subfeed

/**
 * Left/Right on the browse row, decoded from the key's repeat count (Android repeats about every
 * 50 ms after a ~0.5 s long-press delay):
 * - the press itself (rc 0) moves one item;
 * - the first repeat (rc 1, i.e. a long-press) jumps exactly one channel boundary;
 * - nothing else until the key has been held well past a long-press (rc 10, about 0.9 s), after
 *   which every repeat moves one item, so holding scrolls fast without a short long-press overshooting.
 */
object HoldKeys {
    const val ACCELERATE_AFTER = 10

    fun rowKey(right: Boolean, repeatCount: Int): Key? = when {
        repeatCount == 0 -> if (right) Key.Right else Key.Left
        repeatCount == 1 -> if (right) Key.LongRight else Key.LongLeft
        repeatCount >= ACCELERATE_AFTER -> if (right) Key.Right else Key.Left
        else -> null
    }
}

/**
 * The player's screen-on rule: keep the screen on while playback is active; once it has been
 * inactive (paused, errored, idle, ended, or stuck loading) for [releaseAfterMs], let it sleep so
 * the screensaver can start and a still frame can't burn into the OLED.
 */
class WakePolicy(private val releaseAfterMs: Long = 3 * 60_000L) {
    private var inactiveSince: Long? = null

    fun update(nowMs: Long, active: Boolean): Boolean {
        if (active) {
            inactiveSince = null
            return true
        }
        val since = inactiveSince ?: nowMs.also { inactiveSince = it }
        return nowMs - since < releaseAfterMs
    }
}
