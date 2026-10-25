package com.gerstl.subfeed

import android.view.KeyEvent as AKey
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.key.KeyEvent
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Text
import coil.compose.AsyncImage
import coil.request.ImageRequest
import kotlinx.coroutines.delay
import java.text.DateFormat
import java.util.Date

@Composable
fun BrowseScreen(vm: BrowseViewModel, active: Boolean, onPlay: (PlayerArgs) -> Unit, onMenu: (Video) -> Unit, onSettings: () -> Unit) {
    val feed = vm.feed
    val focus = vm.focus
    val requester = remember { FocusRequester() }
    var centerLong by remember { mutableStateOf(false) }

    fun handleGrid(e: KeyEvent): Boolean {
        val ne = e.nativeKeyEvent
        val down = ne.action == AKey.ACTION_DOWN
        val rc = ne.repeatCount
        when (ne.keyCode) {
            AKey.KEYCODE_DPAD_LEFT, AKey.KEYCODE_DPAD_RIGHT -> {
                if (down) HoldKeys.rowKey(ne.keyCode == AKey.KEYCODE_DPAD_RIGHT, rc)?.let { vm.onGridKey(it) }
                return true
            }
            // Up/Down repeat while held so a long category scrolls without tapping every row.
            AKey.KEYCODE_DPAD_UP -> { if (down) vm.onGridKey(Key.Up); return true }
            AKey.KEYCODE_DPAD_DOWN -> { if (down) vm.onGridKey(Key.Down); return true }
            AKey.KEYCODE_MEDIA_FAST_FORWARD -> { if (down && rc == 0) vm.onGridKey(Key.NextUnwatched); return true }
            AKey.KEYCODE_DPAD_CENTER, AKey.KEYCODE_ENTER, AKey.KEYCODE_NUMPAD_ENTER -> {
                if (down) {
                    if (rc == 0) centerLong = false
                    if (rc >= 1 && !centerLong) {
                        centerLong = true
                        feed?.video(focus.videoId)?.let(onMenu)
                    }
                } else {
                    if (!centerLong) vm.playArgs()?.let(onPlay)
                    centerLong = false
                }
                return true
            }
            AKey.KEYCODE_MEDIA_PLAY_PAUSE, AKey.KEYCODE_MEDIA_PLAY -> {
                if (down && rc == 0) vm.playArgs()?.let(onPlay)
                return true
            }
            AKey.KEYCODE_MENU, AKey.KEYCODE_SETTINGS -> { if (!down) onSettings(); return true }
            AKey.KEYCODE_BACK -> { if (!down) vm.closeGrid(); return true }
        }
        return false
    }

    fun handle(e: KeyEvent): Boolean {
        if (vm.gridOpen) return handleGrid(e)
        val ne = e.nativeKeyEvent
        val down = ne.action == AKey.ACTION_DOWN
        val rc = ne.repeatCount
        when (ne.keyCode) {
            AKey.KEYCODE_DPAD_LEFT, AKey.KEYCODE_DPAD_RIGHT -> {
                if (!down) return true
                val right = ne.keyCode == AKey.KEYCODE_DPAD_RIGHT
                // tap = one item; long-press = exactly one channel boundary; held ~1 s = fast item scroll
                HoldKeys.rowKey(right, rc)?.let { vm.onKey(it) }
                return true
            }
            AKey.KEYCODE_DPAD_UP -> { if (down && rc == 0) vm.onKey(Key.Up); return true }
            AKey.KEYCODE_DPAD_DOWN -> { if (down && rc == 0) vm.onKey(Key.Down); return true }
            AKey.KEYCODE_MEDIA_FAST_FORWARD -> { if (down && rc == 0) vm.onKey(Key.NextUnwatched); return true }
            AKey.KEYCODE_DPAD_CENTER, AKey.KEYCODE_ENTER, AKey.KEYCODE_NUMPAD_ENTER -> {
                if (down) {
                    if (rc == 0) centerLong = false
                    if (rc >= 1 && !centerLong) {
                        centerLong = true
                        if (!focus.inTabs) feed?.video(focus.videoId)?.let(onMenu)
                    }
                } else {
                    if (!centerLong) {
                        if (vm.settingsFocused) onSettings()
                        else if (focus.inTabs) vm.openGrid() else vm.playArgs()?.let(onPlay)
                    }
                    centerLong = false
                }
                return true
            }
            AKey.KEYCODE_MEDIA_PLAY_PAUSE, AKey.KEYCODE_MEDIA_PLAY -> {
                if (down && rc == 0 && !focus.inTabs) vm.playArgs()?.let(onPlay)
                return true
            }
            AKey.KEYCODE_MENU, AKey.KEYCODE_SETTINGS -> { if (!down) onSettings(); return true }
            AKey.KEYCODE_BACK -> {
                // Back in the row goes up to the tabs; Back on the tabs leaves the app (not consumed).
                if (focus.inTabs || feed == null) return false
                if (!down) vm.onKey(Key.Up)
                return true
            }
        }
        return false
    }

    Box(
        Modifier
            .fillMaxSize()
            .background(Palette.bg)
            .focusRequester(requester)
            .onKeyEvent { handle(it) }
            .focusable()
    ) {
        if (feed == null) {
            Splash(vm.status)
        } else if (vm.gridOpen) {
            feed.tab(focus.tabId)?.let { tab ->
                GridScreen(feed, tab, focus, feed.video(focus.videoId)?.let { vm.details[it.id] }, vm.snapToken)
            }
            HintChip(vm.hint, Modifier.align(Alignment.BottomCenter).padding(bottom = 40.dp))
        } else {
            val tab = feed.tab(focus.tabId)
            val video = feed.video(focus.videoId)
            Hero(video, video?.let { vm.details[it.id] })
            TopBar(feed, focus, vm.status, vm.settingsFocused)
            if (tab != null) VideoRow(feed, tab, focus, vm.snapToken, Modifier.align(Alignment.BottomStart))
            HintChip(vm.hint, Modifier.align(Alignment.BottomCenter).padding(bottom = 250.dp))
        }
    }

    LaunchedEffect(active) { if (active) requester.requestFocus() }
    // Description and chapters for the featured area, once focus has settled on a video.
    LaunchedEffect(focus.videoId) {
        val id = focus.videoId ?: return@LaunchedEffect
        delay(450)
        vm.loadDetail(id)
    }
    // Keep the feed fresh while the screen is up.
    LaunchedEffect(Unit) {
        while (true) {
            delay(if (vm.feed == null) 8_000 else 5 * 60_000L)
            vm.refresh()
        }
    }
}

@Composable
private fun Splash(status: String?) {
    Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
        Wordmark()
        Spacer(Modifier.height(24.dp))
        Text(status ?: "Loading your subscriptions", style = Type.heroMeta, color = if (status == null) Palette.textDim else Palette.danger)
        if (status != null) {
            Spacer(Modifier.height(8.dp))
            Text("Trying again in a few seconds.", style = Type.heroMeta, color = Palette.textFaint)
        }
    }
}

@Composable
fun Wordmark(scale: Float = 1f) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Canvas(Modifier.size((30 * scale).dp)) {
            val s = size.minDimension
            val r = androidx.compose.ui.geometry.CornerRadius(s * 0.12f)
            drawRoundRect(Palette.accent, Offset(0f, 0f), androidx.compose.ui.geometry.Size(s, s * 0.26f), r)
            drawRoundRect(Palette.text, Offset(0f, s * 0.37f), androidx.compose.ui.geometry.Size(s * 0.78f, s * 0.26f), r)
            drawRoundRect(Palette.textFaint, Offset(0f, s * 0.74f), androidx.compose.ui.geometry.Size(s * 0.56f, s * 0.26f), r)
        }
        Spacer(Modifier.width((12 * scale).dp))
        Text("Subfeed", style = Type.big.copy(fontSize = Type.big.fontSize * scale), color = Palette.text)
    }
}

// ---------------------------------------------------------------- top bar

@Composable
private fun TopBar(feed: Feed, focus: Focus, status: String?, settingsFocused: Boolean) {
    val items = feed.tabs + Tab("settings", "Settings", kind = "settings")
    val state = rememberLazyListState()
    val current = if (settingsFocused) items.lastIndex else feed.tabs.indexOfFirst { it.id == focus.tabId }
    // Keep the current tab in view when there are more tabs than fit; earlier tabs scroll off the left.
    LaunchedEffect(current) { if (current >= 0) state.animateScrollToItem(maxOf(0, current - 3)) }
    Row(
        Modifier.fillMaxWidth().height(64.dp).padding(start = Dims.gutter - 14.dp, end = Dims.gutter, top = 18.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        LazyRow(state = state, modifier = Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically) {
            itemsIndexed(items, key = { _, t -> t.id }) { _, t ->
                if (t.kind == "settings") TabPill(t, current = false, focused = settingsFocused, quiet = true)
                else TabPill(t, current = t.id == focus.tabId, focused = focus.inTabs && !settingsFocused && t.id == focus.tabId)
            }
        }
        Spacer(Modifier.width(16.dp))
        if (status != null) {
            Text(status, style = Type.chip, color = Palette.textFaint, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 260.dp))
            Spacer(Modifier.width(16.dp))
        }
        Clock()
    }
}

@Composable
private fun TabPill(t: Tab, current: Boolean, focused: Boolean, quiet: Boolean = false) {
    val bg by animateFloatAsState(if (focused) 1f else 0f, tween(160), label = "tabbg")
    val scale by animateFloatAsState(if (focused) 1.06f else 1f, spring(dampingRatio = 0.7f), label = "tabscale")
    val textColor = when {
        focused -> Palette.bg
        current -> Palette.text
        quiet -> Palette.textFaint.copy(alpha = 0.7f)
        else -> Palette.textFaint
    }
    Box(
        Modifier
            .padding(end = 4.dp)
            .graphicsLayer { scaleX = scale; scaleY = scale }
            .clip(RoundedCornerShape(50))
            .background(Palette.text.copy(alpha = bg))
            .padding(horizontal = 14.dp, vertical = 6.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(t.name, style = Type.tab, color = textColor, maxLines = 1, softWrap = false)
                Box(
                    Modifier.padding(top = 3.dp).height(3.dp).width(18.dp).clip(RoundedCornerShape(2.dp))
                        .background(if (current && !focused) Palette.accent else Color.Transparent)
                )
            }
            if (t.newCount > 0 && t.kind != "saved" && t.kind != "continue" && t.kind != "history") {
                Spacer(Modifier.width(8.dp))
                Box(
                    Modifier.offset(y = (-3).dp).clip(RoundedCornerShape(50)).background(Palette.accent)
                        .padding(horizontal = 7.dp, vertical = 1.dp)
                ) {
                    Text(if (t.newCount > 99) "99+" else t.newCount.toString(), style = Type.badge, color = Palette.onAccent, maxLines = 1, softWrap = false)
                }
            }
        }
    }
}

@Composable
internal fun Clock() {
    val ctx = LocalContext.current
    val now by produceState(Date()) {
        while (true) { value = Date(); delay(15_000) }
    }
    val fmt = remember { android.text.format.DateFormat.getTimeFormat(ctx) ?: DateFormat.getTimeInstance(DateFormat.SHORT) }
    Text(fmt.format(now), style = Type.tab, color = Palette.textDim)
}

// ---------------------------------------------------------------- featured area

@Composable
private fun Hero(video: Video?, detail: Detail?) {
    Box(Modifier.fillMaxWidth().height(372.dp)) {
        // Backdrop: the focused video's thumbnail, faded into the background on the left and bottom.
        Crossfade(targetState = video?.id, animationSpec = tween(350), label = "hero", modifier = Modifier.align(Alignment.TopEnd)) { id ->
            val v = video?.takeIf { it.id == id }
            Box(Modifier.width(620.dp).fillMaxHeight()) {
                if (v != null) Thumb(v, Modifier.fillMaxSize(), big = true)
                Box(Modifier.fillMaxSize().background(Brush.horizontalGradient(0f to Palette.bg, 0.45f to Palette.bg.copy(alpha = 0.55f), 1f to Color.Transparent)))
                Box(Modifier.fillMaxSize().background(Brush.verticalGradient(0f to Palette.bg.copy(alpha = 0.9f), 0.12f to Palette.bg.copy(alpha = 0.6f), 0.3f to Color.Transparent, 0.6f to Color.Transparent, 1f to Palette.bg)))
            }
        }
        if (video != null) {
            Column(Modifier.padding(start = Dims.gutter, top = 84.dp).width(500.dp)) {
                Text(video.title, style = Type.heroTitle, color = Palette.text, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.height(10.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(video.channelTitle, style = Type.heroMeta, color = Palette.text, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 230.dp))
                    MetaDot()
                    Text(Format.age(video.publishedAt), style = Type.heroMeta, color = Palette.textDim, maxLines = 1)
                    if (video.durationS != null) {
                        MetaDot()
                        Text(Format.lengthWords(video.durationS), style = Type.heroMeta, color = Palette.textDim, maxLines = 1)
                    }
                    StateChip(video)
                }
                Spacer(Modifier.height(12.dp))
                val desc = Format.firstLines(detail?.description, 2)
                Text(
                    desc.ifEmpty { " \n " }, style = Type.heroBody, color = Palette.textDim, maxLines = 2, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.alpha(if (desc.isEmpty()) 0f else 1f)
                )
                val chapters = detail?.chapters.orEmpty()
                if (chapters.size >= 2) {
                    Spacer(Modifier.height(12.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("${chapters.size} chapters", style = Type.chip, color = Palette.accent)
                        chapters.take(2).forEach { c ->
                            Spacer(Modifier.width(10.dp))
                            ChapterChip(c)
                        }
                    }
                }
            }
        }
    }
}

@Composable
internal fun MetaDot() {
    Box(Modifier.padding(horizontal = 10.dp).size(4.dp).clip(CircleShape).background(Palette.textFaint))
}

@Composable
internal fun StateChip(v: Video) {
    val (text, color) = when {
        v.unavailableReason != null -> "Unavailable" to Palette.danger
        v.watched -> "Watched" to Palette.textDim
        v.positionS > 30 && v.durationS != null -> "${Format.lengthWords(v.durationS - v.positionS)} left" to Palette.accent
        v.saveState == "ready" -> "Saved" to Palette.accent
        else -> return
    }
    Spacer(Modifier.width(14.dp))
    Box(Modifier.clip(RoundedCornerShape(6.dp)).border(1.5.dp, color.copy(alpha = 0.7f), RoundedCornerShape(6.dp)).padding(horizontal = 8.dp, vertical = 2.dp)) {
        Text(text, style = Type.chip, color = color, maxLines = 1)
    }
}

@Composable
private fun ChapterChip(c: Chapter) {
    Box(Modifier.clip(RoundedCornerShape(6.dp)).background(Palette.surfaceHi.copy(alpha = 0.85f)).padding(horizontal = 8.dp, vertical = 3.dp)) {
        Row {
            Text(Format.length(c.at), style = Type.chip, color = Palette.textFaint, maxLines = 1, softWrap = false)
            Spacer(Modifier.width(6.dp))
            Text(c.title, style = Type.chip, color = Palette.textDim, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 130.dp))
        }
    }
}

// ---------------------------------------------------------------- row

@Composable
private fun VideoRow(feed: Feed, tab: Tab, focus: Focus, snapToken: Int, modifier: Modifier) {
    val ids = tab.rowIds
    val state = rememberLazyListState()
    val index = ids.indexOf(focus.videoId)
    // The focused card stays pinned at the left edge; the row slides under it.
    LaunchedEffect(tab.id, snapToken) { if (index >= 0) state.scrollToItem(index) else state.scrollToItem(0) }
    LaunchedEffect(focus.videoId, index) {
        if (index >= 0) {
            if (kotlin.math.abs(state.firstVisibleItemIndex - index) > 12) state.scrollToItem(index) else state.animateScrollToItem(index)
        }
    }
    Box(modifier.fillMaxWidth().padding(bottom = 26.dp)) {
        if (ids.isEmpty()) {
            Text(
                when (tab.kind) {
                    "saved" -> "Videos you save show up here."
                    "continue" -> "Videos you start show up here."
                    "history" -> "Videos you play show up here."
                    else -> "Nothing here yet."
                },
                style = Type.heroMeta, color = Palette.textFaint, modifier = Modifier.padding(start = Dims.gutter, bottom = 80.dp)
            )
        } else {
            LazyRow(
                state = state,
                contentPadding = PaddingValues(start = Dims.gutter, end = 600.dp, top = 14.dp),
                horizontalArrangement = Arrangement.spacedBy(Dims.cardGap),
            ) {
                itemsIndexed(ids, key = { _, id -> id }) { i, id ->
                    val v = feed.videos[id] ?: return@itemsIndexed
                    val boundary = FocusModel.isChannelBoundary(tab, feed, i)
                    VideoCard(
                        v,
                        focused = !focus.inTabs && id == focus.videoId,
                        rowActive = !focus.inTabs,
                        boundary = boundary,
                    )
                }
            }
        }
    }
}

@Composable
private fun VideoCard(v: Video, focused: Boolean, rowActive: Boolean, boundary: Boolean) {
    val scale by animateFloatAsState(if (focused) 1.09f else 1f, spring(dampingRatio = 0.75f, stiffness = Spring.StiffnessMediumLow), label = "scale")
    val glow by animateDpAsState(if (focused) 22.dp else 0.dp, tween(180), label = "glow")
    val ring by animateFloatAsState(if (focused) 1f else 0f, tween(140), label = "ring")
    val shape = RoundedCornerShape(Dims.radius)
    val dim = if (rowActive && !focused) 0.72f else 1f
    Row {
        if (boundary) BoundaryMark()
        Column(Modifier.width(Dims.cardWidth).graphicsLayer { scaleX = scale; scaleY = scale; transformOrigin = androidx.compose.ui.graphics.TransformOrigin(0.5f, 0.35f) }) {
            Box(
                Modifier
                    .fillMaxWidth()
                    .aspectRatio(16f / 9f)
                    .shadow(glow, shape, ambientColor = Color.White, spotColor = Color.White)
                    .clip(shape)
                    .background(Palette.surface)
            ) {
                Thumb(v, Modifier.fillMaxSize().alpha(if (v.watched) 1f else dim))
                if (v.watched) {
                    Box(Modifier.fillMaxSize().background(Palette.watchedScrim))
                    CheckBadge(Modifier.align(Alignment.TopEnd).padding(8.dp))
                }
                if (v.unavailableReason != null) {
                    Box(Modifier.fillMaxSize().background(Color(0xCC000000)), contentAlignment = Alignment.Center) {
                        Text("Unavailable", style = Type.chip, color = Palette.textDim)
                    }
                }
                if (v.durationS != null) {
                    Box(
                        Modifier.align(Alignment.BottomEnd).padding(8.dp).clip(RoundedCornerShape(5.dp))
                            .background(Color(0xD9000000)).padding(horizontal = 6.dp, vertical = 1.dp)
                    ) { Text(Format.length(v.durationS), style = Type.badge, color = Palette.text) }
                }
                if (v.progress > 0f) ProgressBar(v.progress, Modifier.align(Alignment.BottomStart))
                Box(Modifier.fillMaxSize().border(3.dp, Palette.focusRing.copy(alpha = ring), shape))
            }
            Spacer(Modifier.height(10.dp))
            Text(
                v.title, style = Type.cardTitle, maxLines = 2, overflow = TextOverflow.Ellipsis,
                color = when {
                    v.watched -> Palette.textFaint
                    focused -> Palette.text
                    else -> Palette.textDim
                },
                modifier = Modifier.height(52.dp),
            )
        }
    }
}

/** Where one channel's run ends and the next begins (long-press Left/Right jumps between these). */
@Composable
private fun BoundaryMark() {
    Box(Modifier.width(2.dp).height(110.dp).padding(top = 12.dp).clip(RoundedCornerShape(1.dp)).background(Palette.surfaceHi))
    Spacer(Modifier.width(Dims.cardGap))
}

@Composable
internal fun ProgressBar(p: Float, modifier: Modifier) {
    Box(modifier.fillMaxWidth().height(5.dp).background(Color(0x99000000))) {
        Box(Modifier.fillMaxHeight().fillMaxWidth(p).background(Palette.accent))
    }
}

@Composable
fun CheckBadge(modifier: Modifier = Modifier, size: Dp = 30.dp) {
    Box(modifier.size(size).clip(CircleShape).background(Palette.text), contentAlignment = Alignment.Center) {
        Canvas(Modifier.size(size * 0.5f)) {
            val w = this.size.width
            val h = this.size.height
            val p = Path().apply {
                moveTo(0f, h * 0.52f)
                lineTo(w * 0.38f, h * 0.88f)
                lineTo(w, h * 0.12f)
            }
            drawPath(p, Palette.bg, style = Stroke(width = w * 0.2f, cap = StrokeCap.Round, join = StrokeJoin.Round))
        }
    }
}

@Composable
internal fun HintChip(text: String?, modifier: Modifier) {
    AnimatedVisibility(text != null, modifier = modifier, enter = fadeIn(tween(120)), exit = fadeOut(tween(300))) {
        Box(Modifier.clip(RoundedCornerShape(50)).background(Palette.surfaceHi).padding(horizontal = 18.dp, vertical = 8.dp)) {
            Text(text ?: "", style = Type.heroMeta, color = Palette.text)
        }
    }
}

// ---------------------------------------------------------------- thumbnails

/** maxres → sd → hq: never assume maxres exists. */
fun thumbCandidates(v: Video): List<String> {
    val base = "https://i.ytimg.com/vi/${v.id}/"
    val all = listOfNotNull(v.thumbnailUrl, base + "maxresdefault.jpg", base + "sddefault.jpg", base + "hqdefault.jpg")
    return all.distinct()
}

@Composable
fun Thumb(v: Video, modifier: Modifier, big: Boolean = false) {
    val ctx = LocalContext.current
    val candidates = remember(v.id, v.thumbnailUrl) { thumbCandidates(v) }
    var i by remember(v.id) { mutableIntStateOf(0) }
    AsyncImage(
        model = ImageRequest.Builder(ctx).data(candidates[i]).crossfade(if (big) 300 else 150)
            .apply { if (!big) size(480, 270) }
            .build(),
        contentDescription = null,
        contentScale = ContentScale.Crop,
        modifier = modifier,
        onError = { if (i < candidates.lastIndex) i++ },
    )
}
