package com.gerstl.subfeed

import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.tv.material3.Text
import kotlin.math.abs

/**
 * Every video in one tab, [GridFocus.COLUMNS] across, with a strip at the top describing the
 * focused card. Opened by OK on a tab; Back returns to the main screen on the same tab.
 */
@Composable
fun GridScreen(feed: Feed, tab: Tab, focus: Focus, detail: Detail?, snapToken: Int, subtitle: String? = null, hiddenLabel: Boolean = false) {
    val ids = tab.videoIds
    val index = ids.indexOf(focus.videoId)
    val state = rememberLazyGridState()
    LaunchedEffect(tab.id, snapToken) { state.scrollToItem(GridFocus.scrollRow(maxOf(0, index)) * GridFocus.COLUMNS) }
    LaunchedEffect(focus.videoId) {
        if (index < 0) return@LaunchedEffect
        val target = GridFocus.scrollRow(index) * GridFocus.COLUMNS
        if (abs(state.firstVisibleItemIndex - target) > 4 * GridFocus.COLUMNS) state.scrollToItem(target) else state.animateScrollToItem(target)
    }

    Column(Modifier.fillMaxSize()) {
        GridHeader(tab, subtitle, hiddenLabel)
        DetailStrip(feed.video(focus.videoId), detail)
        LazyVerticalGrid(
            columns = GridCells.Fixed(GridFocus.COLUMNS),
            state = state,
            userScrollEnabled = false,
            contentPadding = PaddingValues(start = Dims.gutter, end = Dims.gutter, top = 10.dp, bottom = 120.dp),
            horizontalArrangement = Arrangement.spacedBy(GridDims.gap),
            verticalArrangement = Arrangement.spacedBy(GridDims.rowGap),
            modifier = Modifier.fillMaxWidth().weight(1f),
        ) {
            itemsIndexed(ids, key = { _, id -> id }) { _, id ->
                val v = feed.videos[id] ?: return@itemsIndexed
                GridCard(v, focused = id == focus.videoId)
            }
        }
    }
}

private object GridDims {
    val gap = 16.dp
    val rowGap = 18.dp
}

private val gridTitle = Type.cardTitle.copy(fontSize = 13.sp, lineHeight = 17.sp)
private val gridHeading = Type.big.copy(fontSize = 20.sp, lineHeight = 26.sp)
private val gridMeta = Type.heroMeta.copy(fontSize = 15.sp, lineHeight = 20.sp)
private val gridBody = Type.heroBody.copy(fontSize = 14.sp, lineHeight = 19.sp)

@Composable
private fun GridHeader(tab: Tab, subtitle: String?, hiddenLabel: Boolean) {
    Row(
        Modifier.fillMaxWidth().height(56.dp).padding(start = Dims.gutter, end = Dims.gutter, top = 16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(tab.name, style = gridHeading, color = Palette.text, maxLines = 1)
        if (tab.newCount > 0 && tab.kind != "saved") {
            Spacer(Modifier.width(12.dp))
            Box(Modifier.offset(y = (-2).dp).clip(RoundedCornerShape(50)).background(Palette.accent).padding(horizontal = 8.dp, vertical = 1.dp)) {
                Text("${tab.newCount} new", style = Type.badge, color = Palette.onAccent, maxLines = 1, softWrap = false)
            }
        }
        Spacer(Modifier.width(14.dp))
        val n = tab.videoIds.size
        Text(subtitle ?: if (n == 1) "1 video" else "$n videos", style = gridMeta, color = Palette.textFaint, maxLines = 1)
        if (hiddenLabel) {
            Spacer(Modifier.width(12.dp))
            Text("Hidden", style = gridMeta, color = Palette.danger, maxLines = 1)
        }
        Spacer(Modifier.weight(1f))
        Clock()
    }
}

@Composable
private fun DetailStrip(video: Video?, detail: Detail?) {
    // Fixed height so the grid below never jumps as focus moves between long and short titles.
    Column(Modifier.fillMaxWidth().height(74.dp).padding(start = Dims.gutter, end = Dims.gutter, top = 6.dp)) {
        if (video == null) return@Column
        Text(video.title, style = gridHeading, color = Palette.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Spacer(Modifier.height(4.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(video.channelTitle, style = gridMeta, color = Palette.text, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 260.dp))
            MetaDot()
            Text(Format.age(video.publishedAt), style = gridMeta, color = Palette.textDim, maxLines = 1)
            if (video.durationS != null) {
                MetaDot()
                Text(Format.lengthWords(video.durationS), style = gridMeta, color = Palette.textDim, maxLines = 1)
            }
            StateChip(video)
        }
        Spacer(Modifier.height(4.dp))
        val desc = Format.firstLines(detail?.description, 1)
        Text(desc, style = gridBody, color = Palette.textFaint, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
private fun GridCard(v: Video, focused: Boolean) {
    val scale by animateFloatAsState(if (focused) 1.07f else 1f, spring(dampingRatio = 0.75f, stiffness = Spring.StiffnessMediumLow), label = "gscale")
    val glow by animateDpAsState(if (focused) 18.dp else 0.dp, tween(180), label = "gglow")
    val ring by animateFloatAsState(if (focused) 1f else 0f, tween(140), label = "gring")
    val shape = RoundedCornerShape(10.dp)
    Column(Modifier.graphicsLayer { scaleX = scale; scaleY = scale; transformOrigin = androidx.compose.ui.graphics.TransformOrigin(0.5f, 0.4f) }) {
        Box(
            Modifier
                .fillMaxWidth()
                .aspectRatio(16f / 9f)
                .shadow(glow, shape, ambientColor = Color.White, spotColor = Color.White)
                .clip(shape)
                .background(Palette.surface)
        ) {
            Thumb(v, Modifier.fillMaxSize().alpha(if (v.watched || focused) 1f else 0.78f))
            if (v.watched) {
                Box(Modifier.fillMaxSize().background(Palette.watchedScrim))
                CheckBadge(Modifier.align(Alignment.TopEnd).padding(6.dp), size = 22.dp)
            }
            if (v.unavailableReason != null) {
                Box(Modifier.fillMaxSize().background(Color(0xCC000000)), contentAlignment = Alignment.Center) {
                    Text("Unavailable", style = Type.chip, color = Palette.textDim)
                }
            }
            if (v.durationS != null) {
                Box(
                    Modifier.align(Alignment.BottomEnd).padding(6.dp).clip(RoundedCornerShape(4.dp))
                        .background(Color(0xD9000000)).padding(horizontal = 5.dp, vertical = 1.dp)
                ) { Text(Format.length(v.durationS), style = Type.badge, color = Palette.text) }
            }
            if (v.progress > 0f) ProgressBar(v.progress, Modifier.align(Alignment.BottomStart))
            Box(Modifier.fillMaxSize().border(3.dp, Palette.focusRing.copy(alpha = ring), shape))
        }
        Spacer(Modifier.height(6.dp))
        Text(
            v.title, style = gridTitle, maxLines = 1, overflow = TextOverflow.Ellipsis,
            color = when {
                v.watched -> Palette.textFaint
                focused -> Palette.text
                else -> Palette.textDim
            },
        )
    }
}
