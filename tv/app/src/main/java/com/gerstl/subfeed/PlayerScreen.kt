package com.gerstl.subfeed

import android.view.KeyEvent as AKey
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.blur
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.input.key.KeyEvent
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.animation.core.animateFloat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import androidx.media3.ui.SubtitleView
import androidx.media3.ui.CaptionStyleCompat
import androidx.tv.material3.Text

@Composable
fun PlayerScreen(args: PlayerArgs, api: Api, onExit: (lastId: String) -> Unit) {
    val ctx = LocalContext.current
    val vm = remember(args) { PlayerViewModel(ctx.applicationContext, api, args, onExit) }
    val requester = remember { FocusRequester() }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(vm, lifecycle) {
        val obs = LifecycleEventObserver { _, e -> if (e == Lifecycle.Event.ON_STOP) vm.onStop() }
        lifecycle.addObserver(obs)
        onDispose {
            lifecycle.removeObserver(obs)
            vm.release()
        }
    }
    var longHandled by remember { mutableStateOf(false) }

    fun handle(e: KeyEvent): Boolean {
        val ne = e.nativeKeyEvent
        val down = ne.action == AKey.ACTION_DOWN
        val rc = ne.repeatCount
        val code = ne.keyCode
        if (vm.phase == Phase.UpNext) {
            when (code) {
                AKey.KEYCODE_BACK -> { if (!down) vm.exit(); return true }
                AKey.KEYCODE_DPAD_CENTER, AKey.KEYCODE_ENTER, AKey.KEYCODE_MEDIA_PLAY, AKey.KEYCODE_MEDIA_PLAY_PAUSE -> { if (!down) vm.onUpNextSelect(); return true }
            }
            return true
        }
        if (vm.phase is Phase.Error || vm.phase == Phase.Loading) {
            if (code == AKey.KEYCODE_BACK) { if (!down) vm.exit(); return true }
            return true
        }
        if (vm.actionRow && vm.controlsVisible) {
            // The controls' action row: Left/Right choose, OK runs, Down or Back return to the scrubber.
            when (code) {
                AKey.KEYCODE_DPAD_LEFT -> { if (down) vm.moveAction(-1); return true }
                AKey.KEYCODE_DPAD_RIGHT -> { if (down) vm.moveAction(1); return true }
                AKey.KEYCODE_DPAD_CENTER, AKey.KEYCODE_ENTER, AKey.KEYCODE_NUMPAD_ENTER -> { if (!down) vm.runAction(); return true }
                AKey.KEYCODE_DPAD_DOWN, AKey.KEYCODE_BACK -> { if (!down) vm.closeActions(); return true }
                AKey.KEYCODE_DPAD_UP -> { if (down) vm.showControls(); return true }
            }
        }
        when (code) {
            AKey.KEYCODE_CAPTIONS -> { if (down && rc == 0) vm.toggleCaptions(); return true }
            AKey.KEYCODE_BACK -> {
                // Back dismisses the controls first, then leaves the player.
                if (!down) { if (vm.controlsVisible) vm.hideControls() else vm.exit() }
                return true
            }
            AKey.KEYCODE_DPAD_CENTER, AKey.KEYCODE_ENTER, AKey.KEYCODE_NUMPAD_ENTER, AKey.KEYCODE_MEDIA_PLAY_PAUSE -> {
                if (down) {
                    if (rc >= 1 && !longHandled) { longHandled = true; vm.toggleStopAfter() }
                } else {
                    if (!longHandled) vm.togglePlay()
                    longHandled = false
                }
                return true
            }
            AKey.KEYCODE_MEDIA_PLAY -> { if (down && rc == 0) vm.playOrPause(true); return true }
            AKey.KEYCODE_MEDIA_PAUSE -> { if (down && rc == 0) vm.playOrPause(false); return true }
            AKey.KEYCODE_DPAD_LEFT, AKey.KEYCODE_DPAD_RIGHT -> {
                if (!down) return true
                val sign = if (code == AKey.KEYCODE_DPAD_RIGHT) 1 else -1
                // tap = 10 s; hold = 30 s steps
                if (rc == 0) vm.seekBy(sign * 10_000L) else if (rc % 4 == 0) vm.seekBy(sign * 30_000L)
                return true
            }
            AKey.KEYCODE_MEDIA_FAST_FORWARD -> { if (down) vm.seekBy(30_000); return true }
            AKey.KEYCODE_MEDIA_REWIND -> { if (down) vm.seekBy(-30_000); return true }
            AKey.KEYCODE_DPAD_DOWN -> {
                if (down && rc == 0) { if (!vm.undoSponsor()) vm.showControls() }
                return true
            }
            AKey.KEYCODE_DPAD_UP -> {
                // first Up summons the controls; Up again opens their action row (captions, chapters, stop after)
                if (down && rc == 0) { if (vm.controlsVisible) vm.openActions() else vm.showControls() }
                return true
            }
            AKey.KEYCODE_CHANNEL_UP, AKey.KEYCODE_MEDIA_NEXT -> { if (down && rc == 0) vm.chapter(next = true); return true }
            AKey.KEYCODE_CHANNEL_DOWN, AKey.KEYCODE_MEDIA_PREVIOUS -> { if (down && rc == 0) vm.chapter(next = false); return true }
        }
        if (down) vm.showControls()
        return false
    }

    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black)
            .focusRequester(requester)
            .onKeyEvent { handle(it) }
            .focusable()
    ) {
        AndroidView(
            factory = { c ->
                PlayerView(c).apply {
                    useController = false
                    setShutterBackgroundColor(android.graphics.Color.BLACK)
                    resizeMode = AspectRatioFrameLayout.RESIZE_MODE_FIT
                    isFocusable = false
                    player = vm.player
                    // Couch-sized captions on a translucent box (off until turned on from the controls).
                    subtitleView?.apply {
                        setApplyEmbeddedStyles(false)
                        setFractionalTextSize(SubtitleView.DEFAULT_TEXT_SIZE_FRACTION * 1.15f)
                        setBottomPaddingFraction(0.06f)
                        setStyle(
                            CaptionStyleCompat(
                                android.graphics.Color.WHITE, 0xB3000000.toInt(), android.graphics.Color.TRANSPARENT,
                                CaptionStyleCompat.EDGE_TYPE_NONE, android.graphics.Color.BLACK, null,
                            )
                        )
                    }
                }
            },
            update = { it.keepScreenOn = vm.keepScreenOn },
            modifier = Modifier.fillMaxSize(),
        )

        val video = vm.currentVideo
        when (val p = vm.phase) {
            Phase.Loading -> LoadingCard(video)
            is Phase.Error -> ErrorCard(video, p.sentence)
            Phase.UpNext -> vm.upNext?.let { UpNextCard(it) }
            Phase.Playing -> {}
        }

        // paused by the user (not a rebuffer)
        if (vm.phase == Phase.Playing && !vm.playWhenReady && !vm.controlsVisible) {
            Box(Modifier.align(Alignment.TopStart).padding(Dims.gutter, 36.dp).size(54.dp).clip(CircleShape).background(Color(0x99000000)), contentAlignment = Alignment.Center) {
                PlayPauseGlyph(playing = false, Modifier.size(22.dp))
            }
        }

        AnimatedVisibility(vm.controlsVisible && vm.phase == Phase.Playing, enter = fadeIn(tween(150)), exit = fadeOut(tween(400)), modifier = Modifier.align(Alignment.BottomStart)) {
            Controls(vm, video)
        }

        AnimatedVisibility(vm.sponsorNote, enter = fadeIn(tween(150)), exit = fadeOut(tween(400)), modifier = Modifier.align(Alignment.TopEnd).padding(top = 36.dp, end = Dims.gutter)) {
            Pill("Skipped sponsor  ·  press Down to undo")
        }
        AnimatedVisibility(vm.toast != null && !vm.sponsorNote, enter = fadeIn(tween(150)), exit = fadeOut(tween(400)), modifier = Modifier.align(Alignment.TopEnd).padding(top = 36.dp, end = Dims.gutter)) {
            Pill(vm.toast ?: "")
        }
    }

    LaunchedEffect(Unit) { requester.requestFocus() }
}

@Composable
private fun Pill(text: String) {
    Box(Modifier.clip(RoundedCornerShape(50)).background(Color(0xE6202228)).padding(horizontal = 20.dp, vertical = 10.dp)) {
        Text(text, style = Type.heroMeta, color = Palette.text)
    }
}

@Composable
private fun LoadingCard(video: Video?) {
    Box(Modifier.fillMaxSize().background(Palette.bg)) {
        if (video != null) {
            Thumb(video, Modifier.fillMaxSize().blur(40.dp), big = true)
            Box(Modifier.fillMaxSize().background(Color(0xB30D0E12)))
            Column(Modifier.align(Alignment.CenterStart).padding(start = 96.dp).widthIn(max = 640.dp)) {
                Text(video.channelTitle, style = Type.heroMeta, color = Palette.textDim)
                Spacer(Modifier.height(8.dp))
                Text(video.title, style = Type.heroTitle, color = Palette.text, maxLines = 3, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.height(20.dp))
                LoadingDots()
            }
        }
    }
}

@Composable
private fun LoadingDots() {
    val t by androidx.compose.animation.core.rememberInfiniteTransition(label = "dots").animateFloat(
        0f, 3f, androidx.compose.animation.core.infiniteRepeatable(tween(1200)), label = "t"
    )
    Row {
        for (i in 0 until 3) {
            val on = t.toInt() == i
            Box(Modifier.padding(end = 8.dp).size(10.dp).clip(CircleShape).background(if (on) Palette.accent else Palette.surfaceHi))
        }
    }
}

@Composable
private fun ErrorCard(video: Video?, sentence: String) {
    Box(Modifier.fillMaxSize().background(Palette.bg)) {
        Column(Modifier.align(Alignment.CenterStart).padding(start = 96.dp).widthIn(max = 700.dp)) {
            if (video != null) {
                Text(video.title, style = Type.heroMeta, color = Palette.textFaint, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.height(16.dp))
            }
            Text(sentence, style = Type.big, color = Palette.text)
            Spacer(Modifier.height(20.dp))
            Text("Press Back to return.", style = Type.heroMeta, color = Palette.textDim)
        }
    }
}

@Composable
private fun UpNextCard(u: UpNextState) {
    Box(Modifier.fillMaxSize().background(Palette.bg)) {
        Thumb(u.video, Modifier.fillMaxSize().blur(50.dp), big = true)
        Box(Modifier.fillMaxSize().background(Color(0xD90D0E12)))
        Row(Modifier.fillMaxSize().padding(horizontal = 96.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f).padding(end = 48.dp)) {
                Text("Up next", style = Type.heroMeta.copy(fontSize = 20.sp), color = Palette.accent)
                Spacer(Modifier.height(12.dp))
                Text(u.video.title, style = Type.heroTitle, color = Palette.text, maxLines = 3, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.height(12.dp))
                Text(
                    listOf(u.video.channelTitle, Format.lengthWords(u.video.durationS)).filter { it.isNotEmpty() }.joinToString("  ·  "),
                    style = Type.heroMeta, color = Palette.textDim,
                )
                Spacer(Modifier.height(32.dp))
                Text(if (u.ready) "Playing in ${u.secondsLeft}" else "Loading next", style = Type.big, color = Palette.text)
                Spacer(Modifier.height(10.dp))
                Text("OK  play now        Back  stop here", style = Type.heroMeta, color = Palette.textFaint)
            }
            Column(Modifier.width(400.dp)) {
                Box(Modifier.fillMaxWidth().aspectRatio(16f / 9f).clip(RoundedCornerShape(Dims.radius)).background(Palette.surface)) {
                    Thumb(u.video, Modifier.fillMaxSize(), big = true)
                }
                Spacer(Modifier.height(14.dp))
                val frac = if (u.ready) (5 - u.secondsLeft + 1) / 5f else 0f
                Box(Modifier.fillMaxWidth().height(5.dp).clip(RoundedCornerShape(3.dp)).background(Palette.surfaceHi)) {
                    Box(Modifier.fillMaxHeight().fillMaxWidth(frac).background(Palette.accent))
                }
            }
        }
    }
}

@Composable
private fun Controls(vm: PlayerViewModel, video: Video?) {
    Box(
        Modifier.fillMaxWidth().background(Brush.verticalGradient(0f to Color.Transparent, 0.35f to Color(0xB3000000), 1f to Color(0xF2000000)))
    ) {
        Column(Modifier.fillMaxWidth().padding(start = Dims.gutter, end = Dims.gutter, top = 90.dp, bottom = 34.dp)) {
            if (video != null) {
                Text(video.channelTitle, style = Type.heroMeta, color = Palette.textDim, maxLines = 1)
                Spacer(Modifier.height(4.dp))
                Text(video.title, style = Type.big, color = Palette.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            Spacer(Modifier.height(18.dp))
            Scrubber(vm)
            Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                PlayPauseGlyph(playing = vm.playWhenReady, Modifier.size(18.dp))
                Spacer(Modifier.width(14.dp))
                Text(Format.clock(vm.positionMs), style = Type.heroMeta, color = Palette.text)
                vm.currentChapterTitle()?.let {
                    Spacer(Modifier.width(18.dp))
                    Text(it, style = Type.heroMeta, color = Palette.textDim, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 420.dp))
                }
                Spacer(Modifier.weight(1f))
                if (vm.stopAfter) {
                    Text("Stops after this video", style = Type.chip, color = Palette.accent)
                    Spacer(Modifier.width(18.dp))
                }
                Text("-" + Format.clock(maxOf(0, vm.durationMs - vm.positionMs)), style = Type.heroMeta, color = Palette.textDim)
            }
            Spacer(Modifier.height(14.dp))
            if (vm.actionRow) {
                // Up from the controls: a row of actions; Left/Right choose, OK runs it.
                Row(verticalAlignment = Alignment.CenterVertically) {
                    vm.actions.forEachIndexed { i, label ->
                        val f = i == vm.actionIndex
                        Box(
                            Modifier.padding(end = 10.dp).clip(RoundedCornerShape(50))
                                .background(if (f) Palette.text else Color(0x33FFFFFF))
                                .padding(horizontal = 16.dp, vertical = 7.dp)
                        ) { Text(label, style = Type.chip.copy(fontSize = 16.sp), color = if (f) Palette.bg else Palette.text, maxLines = 1) }
                    }
                }
            } else {
                val cc = if (vm.captionTracks.isEmpty()) "" else "  ·  captions"
                Text(
                    "Left / Right  10 s, hold for 30 s      OK  pause      Up  more (chapters$cc, stop after this video)",
                    style = Type.chip, color = Palette.textFaint,
                )
            }
        }
    }
}

@Composable
private fun Scrubber(vm: PlayerViewModel) {
    val dur = vm.durationMs.coerceAtLeast(1)
    val played = (vm.positionMs.toFloat() / dur).coerceIn(0f, 1f)
    val buffered = (vm.bufferedMs.toFloat() / dur).coerceIn(0f, 1f)
    val marks = vm.chapters.map { (it.at * 1000 / dur).toFloat() }.filter { it > 0.002f && it < 0.998f }
    Canvas(Modifier.fillMaxWidth().height(16.dp)) {
        val h = 6.dp.toPx()
        val y = (size.height - h) / 2
        val r = CornerRadius(h / 2)
        drawRoundRect(Color(0x40FFFFFF), Offset(0f, y), Size(size.width, h), r)
        drawRoundRect(Color(0x66FFFFFF), Offset(0f, y), Size(size.width * buffered, h), r)
        drawRoundRect(Palette.accent, Offset(0f, y), Size(size.width * played, h), r)
        marks.forEach { m -> drawRect(Color.Black, Offset(size.width * m - 1.5f, y), Size(3f, h)) }
        drawCircle(Color.White, radius = 8.dp.toPx(), center = Offset(size.width * played, size.height / 2))
    }
}

@Composable
fun PlayPauseGlyph(playing: Boolean, modifier: Modifier) {
    Canvas(modifier) {
        if (playing) {
            val w = size.width * 0.3f
            drawRoundRect(Color.White, Offset(size.width * 0.12f, 0f), Size(w, size.height), CornerRadius(2f))
            drawRoundRect(Color.White, Offset(size.width * 0.58f, 0f), Size(w, size.height), CornerRadius(2f))
        } else {
            val p = Path().apply {
                moveTo(size.width * 0.15f, 0f); lineTo(size.width * 0.95f, size.height / 2); lineTo(size.width * 0.15f, size.height); close()
            }
            drawPath(p, Color.White)
        }
    }
}
