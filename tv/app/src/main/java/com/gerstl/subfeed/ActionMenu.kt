package com.gerstl.subfeed

import android.view.KeyEvent as AKey
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Text

/**
 * Long-press OK on a card: Save/Unsave, mark watched/unwatched, move the channel, create a
 * category, hide the channel. A side panel over the (not playing) browse screen.
 */
@Composable
fun ActionMenu(
    video: Video, vm: BrowseViewModel, api: Api, onClose: () -> Unit,
    onGoToChannel: (Video) -> Unit, onPlayFromStart: (Video) -> Unit,
) {
    val feed = vm.feed
    var page by remember { mutableStateOf("main") }   // main | move | new
    var index by remember { mutableIntStateOf(0) }
    val requester = remember { FocusRequester() }
    val listState = rememberLazyListState()
    var sawDown by remember { mutableStateOf(false) }
    val categories = approvedCategories(feed)

    val currentCategory = video.categoryId
    val main = buildList {
        add(MenuItem("Go to channel", video.channelTitle) { onGoToChannel(video) })
        when (video.saveState) {
            null, "failed" -> add(MenuItem("Save to the NAS", if (video.saveState == "failed") "The last try failed" else "Keeps it even if YouTube removes it") {
                vm.runAction("Saving in the background") { save(video.id) }; onClose()
            })
            "ready" -> add(MenuItem("Remove from Saved") { vm.runAction("Removed from Saved") { unsave(video.id) }; onClose() })
            else -> add(MenuItem("Cancel saving", "Saving now") { vm.runAction("Save cancelled") { unsave(video.id) }; onClose() })
        }
        add(MenuItem("Play from start") { onPlayFromStart(video) })
        if (feed?.inContinue(video.id) == true) {
            add(MenuItem("Remove from Continue Watching") { vm.runAction("Removed from Continue Watching") { markWatched(video.id, false) }; onClose() })
        }
        if (video.watched || video.positionS > 0) add(MenuItem("Mark as unwatched") { vm.runAction("Marked unwatched") { markWatched(video.id, false) }; onClose() })
        if (!video.watched) add(MenuItem("Mark as watched") { vm.runAction("Marked watched") { markWatched(video.id, true) }; onClose() })
        add(MenuItem("Move channel to…", categories.firstOrNull { it.first == currentCategory }?.second ?: "Other") { page = "move"; index = 0 })
        add(MenuItem("Hide from Subfeed", "Find it later in Settings > Channels") {
            vm.runAction("${video.channelTitle} is hidden from Subfeed") { setChannelHidden(video.channelId, true) }; onClose()
        })
    }
    val move = categoryMoveItems(categories, currentCategory, onNew = { page = "new" }) { id, name ->
        vm.runAction("${video.channelTitle} moved to $name") { setChannelCategory(video.channelId, id) }; onClose()
    }
    val items = if (page == "move") move else main

    Box(
        Modifier.fillMaxSize()
            .background(Brush.horizontalGradient(0f to Color.Transparent, 0.45f to Color(0x99000000), 1f to Color(0xE6000000)))
    ) {
        Column(
            Modifier.align(Alignment.CenterEnd).width(420.dp).fillMaxHeight().background(Palette.surface)
                .padding(horizontal = 32.dp, vertical = 44.dp)
                .focusRequester(requester)
                .onKeyEvent { e ->
                    if (page == "new") return@onKeyEvent false
                    val ne = e.nativeKeyEvent
                    val down = ne.action == AKey.ACTION_DOWN
                    when (ne.keyCode) {
                        AKey.KEYCODE_DPAD_UP -> { if (down) index = (index - 1).coerceAtLeast(0); true }
                        AKey.KEYCODE_DPAD_DOWN -> { if (down) index = (index + 1).coerceAtMost(items.lastIndex); true }
                        AKey.KEYCODE_DPAD_CENTER, AKey.KEYCODE_ENTER -> {
                            // Only a press that started while the menu is open counts; the key-up of the
                            // long-press that opened it (and its key-repeats) never set sawDown.
                            if (down) { if (ne.repeatCount == 0) sawDown = true }
                            else {
                                if (sawDown) items.getOrNull(index)?.run?.invoke()
                                sawDown = false
                            }
                            true
                        }
                        AKey.KEYCODE_BACK -> {
                            if (!down) { if (page == "move") { page = "main"; index = 0 } else onClose() }
                            true
                        }
                        AKey.KEYCODE_DPAD_LEFT, AKey.KEYCODE_DPAD_RIGHT -> true
                        else -> false
                    }
                }
                .focusable()
        ) {
            Text(video.channelTitle, style = Type.heroMeta, color = Palette.textDim, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Spacer(Modifier.height(6.dp))
            Text(video.title, style = Type.heroMeta.copy(fontSize = Type.cardTitle.fontSize), color = Palette.text, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Spacer(Modifier.height(28.dp))
            if (page == "new") {
                NewCategory(onDone = { name ->
                    if (name.isNotBlank()) {
                        vm.runAction("${video.channelTitle} moved to ${name.trim()}") {
                            val c = createCategory(name.trim())
                            setChannelCategory(video.channelId, c.id)
                        }
                    }
                    onClose()
                }, onCancel = { page = "move" })
            } else {
                if (page == "move") {
                    Text("Move ${video.channelTitle} to", style = Type.chip, color = Palette.accent)
                    Spacer(Modifier.height(12.dp))
                }
                // Eight rows do not fit the screen height: a lazy list keeps the focused row in view.
                LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = listState) {
                    itemsIndexed(items) { i, it -> MenuRow(it, i == index) }
                }
            }
        }
    }
    LaunchedEffect(page) { if (page != "new") requester.requestFocus() }
    LaunchedEffect(index, page) { if (page != "new") listState.animateScrollToItem(index) }
}
