package com.gerstl.subfeed

import android.content.Context
import android.media.MediaCodecList
import android.os.Build
import android.os.SystemClock
import android.util.Log
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.datasource.HttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.MediaSource
import androidx.media3.exoplayer.source.MergingMediaSource
import androidx.media3.exoplayer.source.ProgressiveMediaSource
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.concurrent.atomic.AtomicBoolean
import android.os.Looper
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.Renderer
import androidx.media3.exoplayer.text.TextOutput
import androidx.media3.exoplayer.text.TextRenderer
import android.net.Uri
import androidx.compose.runtime.mutableIntStateOf
import androidx.media3.common.MimeTypes
import androidx.media3.exoplayer.source.SingleSampleMediaSource

/** Hardware decoding capability, sent to `/play` so the service picks a stream this TV can play. */
object Decoders {
    data class Caps(val decoders: List<String>, val maxHeight: Int)

    val caps: Caps by lazy { probe() }

    private fun probe(): Caps {
        var vp9_4k = false
        var vp9 = false
        var avc = false
        try {
            for (info in MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos) {
                if (info.isEncoder) continue
                val hw = if (Build.VERSION.SDK_INT >= 29) info.isHardwareAccelerated else !info.name.startsWith("OMX.google.") && !info.name.startsWith("c2.android.")
                if (!hw) continue
                for (t in info.supportedTypes) {
                    when (t.lowercase()) {
                        "video/x-vnd.on2.vp9" -> {
                            vp9 = true
                            val vc = info.getCapabilitiesForType(t).videoCapabilities
                            if (vc != null && vc.isSizeSupported(3840, 2160)) vp9_4k = true
                        }
                        "video/avc" -> avc = true
                    }
                }
            }
        } catch (e: Exception) {
            Log.w("subfeed", "codec probe failed: $e")
        }
        // AV1 is deliberately not offered: only VP9 (to 2160p) and H.264 were verified on this TV (Task 1).
        val list = buildList {
            if (vp9) add("vp9")
            if (avc || isEmpty()) add("avc1")
        }
        return Caps(list, if (vp9_4k) 2160 else 1080)
    }
}

sealed interface Phase {
    data object Loading : Phase
    data object Playing : Phase
    data class Error(val sentence: String) : Phase
    data object UpNext : Phase
}

data class UpNextState(val video: Video, val ready: Boolean, val secondsLeft: Int)

/**
 * One playback session. Owns the ExoPlayer, the frozen autoplay queue, SponsorBlock skipping,
 * progress reporting and the wake-lock rule. Created and released by [PlayerScreen].
 */
class PlayerViewModel(
    private val ctx: Context,
    private val api: Api,
    private val args: PlayerArgs,
    private val onExit: (lastId: String) -> Unit,
) {
    private val scope = CoroutineScope(Dispatchers.Main.immediate + Job())
    private val app = ctx as App
    /** App-wide single thread for progress posts; never shut down, so nothing queued is dropped or replayed late. */
    private val io = app.io
    private val tickInFlight = AtomicBoolean(false)
    private val wake = WakePolicy()

    // Side-loaded WebVTT arrives as raw text/vtt samples (SingleSampleMediaSource), which Media3 1.4's
    // TextRenderer only decodes with legacy decoding enabled.
    private val renderers = object : DefaultRenderersFactory(ctx) {
        override fun buildTextRenderers(
            context: Context, output: TextOutput, outputLooper: Looper,
            extensionRendererMode: Int, out: ArrayList<Renderer>,
        ) {
            super.buildTextRenderers(context, output, outputLooper, extensionRendererMode, out)
            out.filterIsInstance<TextRenderer>().forEach { it.experimentalSetLegacyDecodingEnabled(true) }
        }
    }

    val player: ExoPlayer = ExoPlayer.Builder(ctx, renderers).build().apply {
        setAudioAttributes(AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MOVIE).build(), true)
        setHandleAudioBecomingNoisy(true)
        // Captions are available but off until the user turns them on.
        trackSelectionParameters = trackSelectionParameters.buildUpon().setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true).build()
    }

    var currentId by mutableStateOf(args.startId)
        private set
    var phase by mutableStateOf<Phase>(Phase.Loading)
        private set
    var controlsVisible by mutableStateOf(false)
        private set
    var sponsorNote by mutableStateOf(false)
        private set
    var stopAfter by mutableStateOf(false)
        private set
    var isPlaying by mutableStateOf(false)
        private set
    /** The user's intent (false = paused); unlike isPlaying it stays true through a rebuffer. */
    var playWhenReady by mutableStateOf(true)
        private set
    var captionsOn by mutableStateOf(false)
        private set
    var captionTracks by mutableStateOf<List<CaptionTrack>>(emptyList())
        private set
    /** Controls' action row (Up from the controls): captions, chapters, stop-after. */
    var actionRow by mutableStateOf(false)
        private set
    var actionIndex by mutableIntStateOf(0)
        private set
    var keepScreenOn by mutableStateOf(true)
        private set
    var positionMs by mutableLongStateOf(0L)
        private set
    var durationMs by mutableLongStateOf(0L)
        private set
    var bufferedMs by mutableLongStateOf(0L)
        private set
    var chapters by mutableStateOf<List<Chapter>>(emptyList())
        private set
    var upNext by mutableStateOf<UpNextState?>(null)
        private set
    var toast by mutableStateOf<String?>(null)
        private set

    val currentVideo: Video? get() = args.videos[currentId]

    private val queue = PlaybackQueue(args.snapshot, args.startId)
    private val watchedLocal = HashSet<String>()
    private val failed = HashSet<String>()
    private var reporter: WatchReporter? = null
    private var skipper = SponsorSkipper(emptyList())
    private var response: PlayResponse? = null
    private var prefetched: Triple<String, PlayResponse, Long>? = null
    private var prefetchJob: Job? = null
    private var retriedExpiry = false
    private var endedReported = false
    private var hideJob: Job? = null
    private var noteJob: Job? = null
    private var toastJob: Job? = null
    private var upNextJob: Job? = null
    private var released = false
    /** fromStart applies only to the first load of the first video (not expiry re-fetches or later items). */
    private var startedOnce = false

    init {
        player.addListener(object : Player.Listener {
            override fun onIsPlayingChanged(playing: Boolean) {
                isPlaying = playing
            }

            private var loggedCue = false
            override fun onCues(cueGroup: androidx.media3.common.text.CueGroup) {
                if (!loggedCue && cueGroup.cues.isNotEmpty()) {
                    loggedCue = true
                    Log.i("subfeed", "captions first cue shown")
                }
            }

            override fun onPlayWhenReadyChanged(pwr: Boolean, reason: Int) {
                playWhenReady = pwr
                if (!pwr) report(flush = true)   // paused: post now
            }

            override fun onPlaybackStateChanged(state: Int) {
                when (state) {
                    Player.STATE_READY -> if (phase == Phase.Loading) {
                        phase = Phase.Playing
                        // A skip applied before the first frame: give the note its full 3 s on the picture.
                        if (sponsorNote) showSponsorNote()
                        prefetchNext()
                    }
                    Player.STATE_ENDED -> onEnded()
                    else -> {}
                }
            }

            override fun onPlayerError(error: PlaybackException) {
                Log.w("subfeed", "player error ${error.errorCodeName}: ${error.cause}")
                val status = (error.cause as? HttpDataSource.InvalidResponseCodeException)?.responseCode
                if (!retriedExpiry && (status == 403 || status == 410)) {
                    // Stream link expired or was refused: ask the service once for a fresh one.
                    retriedExpiry = true
                    val at = player.currentPosition
                    scope.launch { load(currentId, fresh = true, startAtMs = at) }
                } else if (captionsOn) {
                    // A caption track that won't load must never stop the video: drop captions and carry on.
                    captionsOn = false
                    player.trackSelectionParameters = player.trackSelectionParameters.buildUpon()
                        .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true).build()
                    showToast("Captions couldn't load")
                    player.prepare()
                } else {
                    phase = Phase.Error("This video stopped playing.")
                }
            }
        })
        scope.launch { load(args.startId, fresh = false, startAtMs = null) }
        scope.launch { tickLoop() }
    }

    // ------------------------------------------------------------ loading

    private suspend fun load(id: String, fresh: Boolean, startAtMs: Long?) {
        val sameVideo = currentId == id
        phase = Phase.Loading
        currentId = id
        flushOutbox()
        queue.moveTo(id)
        endedReported = false
        val pre = prefetched?.takeIf { it.first == id && !fresh && SystemClock.elapsedRealtime() - it.third < 4 * 3600_000L }?.second
        prefetched = null
        val resp = pre ?: try {
            withContext(Dispatchers.IO) { api.play(id, PlayRequest(Decoders.caps.decoders, Decoders.caps.maxHeight, fromStart = args.fromStart && id == args.startId && !startedOnce)) }
        } catch (e: ApiError) {
            phase = Phase.Error(e.sentence)
            return
        }
        if (released) return
        response = resp
        chapters = resp.chapters.filter { it.title.isNotBlank() }.sortedBy { it.at }
        // A re-fetch of the same video (expired link) keeps the segments the user chose to watch.
        skipper = SponsorSkipper(resp.sponsor, if (fresh && sameVideo) skipper.suppressedStarts() else emptySet())
        reporter = WatchReporter(id, ::postProgress, app.outbox)
        captionTracks = resp.captions.filter { it.url.isNotBlank() }
        val durMs = ((resp.durationS ?: args.videos[id]?.durationS ?: 0.0) * 1000).toLong()
        var start = startAtMs ?: (resp.resumeS * 1000).toLong()
        if (startAtMs == null && durMs > 0 && start > durMs - 15_000) start = 0   // finished last time: start over
        val skipped = skipper.startPosition(start)
        if (skipped != start) { Log.i("subfeed", "sponsor skip at start ${start}ms -> ${skipped}ms"); showSponsorNote() }
        Log.i("subfeed", "play $id source=${resp.source} height=${resp.height} mime=${resp.videoMime} start=${skipped}ms sponsor=${resp.sponsor.size} chapters=${chapters.size}")
        startedOnce = true
        player.setMediaSource(buildSource(resp), skipped)
        player.prepare()
        player.playWhenReady = true
    }

    private fun buildSource(r: PlayResponse): MediaSource {
        val headers = HashMap(r.headers)
        if ((r.videoUrl.startsWith(api.base)) && headers.keys.none { it.equals("Authorization", true) }) {
            headers[api.authHeader.first] = api.authHeader.second
        }
        val ua = headers.remove("User-Agent")
        val http = DefaultHttpDataSource.Factory()
            .setDefaultRequestProperties(headers)
            .setAllowCrossProtocolRedirects(true)
            .setConnectTimeoutMs(15_000)
            .setReadTimeoutMs(20_000)
        if (ua != null) http.setUserAgent(ua)
        // Bounded 10 MB ranges: googlevideo throttles open-ended ranges (Task 1 finding).
        val chunked = DataSource.Factory { ChunkedHttpDataSource(http.createDataSource()) }
        val msf = ProgressiveMediaSource.Factory(chunked)
        val video = msf.createMediaSource(MediaItem.fromUri(r.videoUrl))
        val sources = mutableListOf<MediaSource>(video)
        r.audioUrl?.let { sources += msf.createMediaSource(MediaItem.fromUri(it)) }
        sources += captionSources(r)
        return if (sources.size == 1) video else MergingMediaSource(*sources.toTypedArray())
    }

    /** WebVTT side tracks. Saved-video captions live on the service and need the bearer; YouTube's don't. */
    private fun captionSources(r: PlayResponse): List<MediaSource> = r.captions.filter { it.url.isNotBlank() }.map { c ->
        val f = DefaultHttpDataSource.Factory().setAllowCrossProtocolRedirects(true).setConnectTimeoutMs(10_000).setReadTimeoutMs(15_000)
        if (c.url.startsWith(api.base)) f.setDefaultRequestProperties(mapOf(api.authHeader))
        val sub = MediaItem.SubtitleConfiguration.Builder(Uri.parse(c.url))
            .setMimeType(MimeTypes.TEXT_VTT)
            .setLanguage(c.lang)
            .setLabel(c.label)
            .build()
        SingleSampleMediaSource.Factory(f).createMediaSource(sub, C.TIME_UNSET)
    }

    private fun postProgress(vid: String, body: ProgressBody): Boolean = try {
        if (api.progress(vid, body)) synchronized(watchedLocal) { watchedLocal += vid }
        true
    } catch (e: ApiError) {
        e.status == 404   // video gone from the feed: nothing to retry
    }

    /** Deliver progress left over from earlier sessions (or an earlier launch). */
    private fun flushOutbox() {
        if (app.outbox.size == 0) return
        io.execute { app.outbox.flush(::postProgress) }
    }

    private fun isWatched(id: String) = synchronized(watchedLocal) { id in watchedLocal } || args.videos[id]?.watched == true
    private fun isPlayable(id: String) = id !in failed && args.videos[id]?.playable != false

    private fun prefetchNext() {
        if (!args.autoplay) return
        val next = queue.next(::isPlayable, ::isWatched) ?: return
        if (prefetched?.first == next || prefetchJob?.isActive == true) return
        prefetchJob = scope.launch {
            try {
                val r = withContext(Dispatchers.IO) { api.play(next, PlayRequest(Decoders.caps.decoders, Decoders.caps.maxHeight)) }
                prefetched = Triple(next, r, SystemClock.elapsedRealtime())
            } catch (e: ApiError) {
                // status 0 = network blip: try again later; anything else means this one won't play
                if (e.status != 0) failed += next
            }
        }
    }

    // ------------------------------------------------------------ ticking: sponsor skip, progress, controls clock

    private suspend fun tickLoop() {
        while (scope.isActive) {
            val state = player.playbackState
            val active = phase == Phase.Playing && player.playWhenReady &&
                state != Player.STATE_IDLE && state != Player.STATE_ENDED
            keepScreenOn = wake.update(SystemClock.elapsedRealtime(), active)
            if (phase == Phase.Playing) {
                val pos = player.currentPosition
                positionMs = pos
                durationMs = player.duration.takeIf { it > 0 } ?: durationMs
                bufferedMs = player.bufferedPosition
                if (player.isPlaying) {
                    skipper.onPosition(pos)?.let {
                        Log.i("subfeed", "sponsor skip ${pos}ms -> ${it.seekToMs}ms")
                        player.seekTo(it.seekToMs); showSponsorNote()
                    }
                    report(flush = false)
                }
            }
            delay(250)
        }
    }

    private fun body(ended: Boolean = false): ProgressBody? {
        val d = if (durationMs > 0) durationMs else ((response?.durationS ?: 0.0) * 1000).toLong()
        if (d <= 0) return null
        val pos = if (ended) d else player.currentPosition
        return ProgressBody(pos / 1000.0, d / 1000.0, skipper.totalSeconds(), ended, skipper.contentPosition(pos / 1000.0))
    }

    private fun report(flush: Boolean, ended: Boolean = false) {
        val r = reporter ?: return
        val b = body(ended) ?: return
        val now = SystemClock.elapsedRealtime()
        if (flush) {
            io.execute { r.flush(b) }
        } else if (tickInFlight.compareAndSet(false, true)) {
            // at most one tick queued or running; a slow service never builds a backlog
            io.execute { try { r.onTick(now, b) } finally { tickInFlight.set(false) } }
        }
    }

    // ------------------------------------------------------------ end of stream and Up next

    private fun onEnded() {
        if (endedReported) return
        Log.i("subfeed", "ended $currentId")
        endedReported = true
        report(flush = true, ended = true)
        synchronized(watchedLocal) { watchedLocal += currentId }
        if (stopAfter || !args.autoplay) return exit()
        val next = queue.next(::isPlayable, ::isWatched) ?: return exit()
        val v = args.videos[next] ?: return exit()
        phase = Phase.UpNext
        controlsVisible = false
        upNextJob = scope.launch {
            var target = next
            var video = v
            // Wait for the prefetched stream; never leave the card up indefinitely.
            var waited = 0
            upNext = UpNextState(video, ready = prefetched?.first == target, secondsLeft = 5)
            if (prefetched?.first != target && prefetchJob?.isActive != true) prefetchNext()
            while (prefetched?.first != target) {
                if (target in failed) {
                    val alt = queue.next(::isPlayable, ::isWatched)
                    val altVideo = alt?.let { args.videos[it] }
                    if (alt == null || altVideo == null) return@launch exit()
                    target = alt; video = altVideo
                    upNext = UpNextState(video, ready = false, secondsLeft = 5)
                    prefetchNext()
                }
                delay(250); waited += 250
                // a transient failure left nothing in flight: ask again every 3 s
                if (waited % 3000 == 0 && prefetchJob?.isActive != true && prefetched?.first != target) prefetchNext()
                if (waited > 20_000) return@launch exit()
            }
            for (s in 5 downTo 1) {
                upNext = UpNextState(video, ready = true, secondsLeft = s)
                delay(1000)
            }
            playUpNext()
        }
    }

    private fun playUpNext() {
        val u = upNext ?: return
        if (prefetched?.first != u.video.id) return
        upNextJob?.cancel()
        upNext = null
        retriedExpiry = false
        scope.launch { load(u.video.id, fresh = false, startAtMs = null) }
    }

    // ------------------------------------------------------------ remote keys

    /** Any key summons the controls; they fade 3 s after the last key. */
    private fun poke() {
        controlsVisible = true
        hideJob?.cancel()
        hideJob = scope.launch { delay(3000); controlsVisible = false; actionRow = false }
    }

    fun hideControls() {
        hideJob?.cancel(); controlsVisible = false; actionRow = false
    }

    fun toggleCaptions() {
        if (captionTracks.isEmpty()) {
            showToast("This video has no captions"); poke(); return
        }
        captionsOn = !captionsOn
        val first = captionTracks.first()
        player.trackSelectionParameters = player.trackSelectionParameters.buildUpon()
            .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, !captionsOn)
            .setPreferredTextLanguage(first.lang)
            .build()
        showToast(if (captionsOn) "Captions on (${first.label})" else "Captions off")
        Log.i("subfeed", "captions ${if (captionsOn) "on" else "off"} lang=${first.lang}")
        poke()
    }

    // ---- save to the NAS (same call as the browse long-press menu) ----
    /** Save states set during this session; the args snapshot doesn't change while playing. */
    private val saveLocal = mutableStateMapOf<String, String>()

    private fun saveState(id: String): String? = saveLocal[id] ?: args.videos[id]?.saveState

    private fun save() {
        val id = currentId
        when (saveState(id)) {
            "ready" -> { showToast("Already saved to the NAS"); return }
            null, "failed" -> {}
            else -> { showToast("Saving in the background"); return }
        }
        saveLocal[id] = "queued"
        showToast("Saving in the background")
        scope.launch {
            try {
                withContext(Dispatchers.IO) { api.save(id) }
            } catch (e: ApiError) {
                saveLocal[id] = "failed"
                showToast(e.sentence)
            }
        }
    }

    // ---- the controls' action row ----
    val actions: List<String>
        get() = buildList {
            add(when (saveState(currentId)) {
                "ready" -> "Saved"
                null, "failed" -> "Save to NAS"
                else -> "Saving"
            })
            add(if (captionsOn) "Captions: On" else "Captions: Off")
            if (chapters.size >= 2) { add("Previous chapter"); add("Next chapter") }
            add(if (stopAfter) "Stop after this video: On" else "Stop after this video: Off")
        }

    fun openActions() { actionRow = true; actionIndex = 0; poke() }
    fun closeActions() { actionRow = false; poke() }
    fun moveAction(delta: Int) { actionIndex = (actionIndex + delta).coerceIn(0, actions.lastIndex); poke() }
    fun runAction() {
        when (actions.getOrNull(actionIndex)?.substringBefore(":")) {
            "Save to NAS", "Saving", "Saved" -> save()
            "Captions" -> toggleCaptions()
            "Previous chapter" -> chapter(next = false)
            "Next chapter" -> chapter(next = true)
            "Stop after this video" -> toggleStopAfter()
        }
        poke()
    }

    private fun showSponsorNote() {
        sponsorNote = true
        noteJob?.cancel()
        noteJob = scope.launch { delay(3000); sponsorNote = false }
    }

    private fun showToast(t: String) {
        toast = t
        toastJob?.cancel()
        toastJob = scope.launch { delay(2500); toast = null }
    }

    fun togglePlay() {
        if (phase != Phase.Playing) return
        if (player.playWhenReady) player.pause() else player.play()
        poke()
    }

    fun playOrPause(play: Boolean) {
        if (phase != Phase.Playing) return
        if (play) player.play() else player.pause()
        poke()
    }

    fun seekBy(deltaMs: Long) {
        if (phase != Phase.Playing) return
        val d = player.duration.takeIf { it > 0 } ?: Long.MAX_VALUE
        val to = (player.currentPosition + deltaMs).coerceIn(0, maxOf(0, d - 1000))
        player.seekTo(to)
        positionMs = to
        poke()
    }

    fun toggleStopAfter() {
        stopAfter = !stopAfter
        showToast(if (stopAfter) "Stops after this video" else "Autoplay is back on")
        poke()
    }

    /** Down while the sponsor note is up: jump back to just before the segment and stop skipping it. */
    fun undoSponsor(): Boolean {
        if (!sponsorNote) return false
        val to = skipper.undoLast() ?: return false
        Log.i("subfeed", "sponsor undo -> ${to}ms")
        player.seekTo(to)
        sponsorNote = false
        noteJob?.cancel()
        showToast("Sponsor segment restored")
        return true
    }

    fun chapter(next: Boolean) {
        if (phase != Phase.Playing || chapters.size < 2) { poke(); return }
        val pos = player.currentPosition
        val starts = chapters.map { (it.at * 1000).toLong() }
        val target = if (next) starts.firstOrNull { it > pos + 1000 }
        else {
            val curIdx = starts.indexOfLast { it <= pos }
            if (curIdx >= 0 && pos - starts[curIdx] > 5000) starts[curIdx] else starts.getOrNull(curIdx - 1) ?: 0L
        }
        if (target != null) {
            Log.i("subfeed", "chapter next=$next ${pos}ms -> ${target}ms")
            player.seekTo(target); positionMs = target
        }
        poke()
    }

    fun showControls() = poke()

    fun currentChapterTitle(): String? {
        val pos = positionMs
        return chapters.lastOrNull { it.at * 1000 <= pos }?.title
    }

    fun onUpNextSelect() = playUpNext()

    fun exit() {
        if (released) return
        if (!endedReported && phase == Phase.Playing) report(flush = true)
        val last = currentId
        release()
        onExit(last)
    }

    fun onStop() {
        if (player.isPlaying) player.pause() else report(flush = true)
    }

    fun release() {
        if (released) return
        released = true
        upNextJob?.cancel()
        scope.cancel()
        player.release()
        // io is the app's progress thread: anything unsent is already in the outbox
    }
}
