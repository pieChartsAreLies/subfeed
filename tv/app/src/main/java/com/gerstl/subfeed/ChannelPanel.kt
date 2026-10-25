package com.gerstl.subfeed

import android.view.KeyEvent as AKey
import androidx.compose.foundation.background
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Text

const val HIDE_CONFIRM = "Hide from your feed? It stays on YouTube and you can still open it here."
const val HIDDEN_HINT = "Hidden from Subfeed. Long-press to undo."

/**
 * Long-press OK on a Channels row: move to a category, make a new one, hide or show the channel.
 * A side panel like [ActionMenu]. Actions go to [onAction] (success sentence, API call); the
 * screen runs them, refreshes and shows the sentence.
 */
@Composable
fun ChannelPanel(
    channel: ChannelInfo, categories: List<Pair<Int, String>>,
    onAction: (String, Api.() -> Unit) -> Unit, onClose: () -> Unit,
) {
    var page by remember { mutableStateOf("main") }   // main | move | new | confirm
    var index by remember { mutableIntStateOf(0) }
    val requester = remember { FocusRequester() }
    var sawDown by remember { mutableStateOf(false) }
    val current = categories.firstOrNull { it.first == channel.categoryId }?.second ?: "Other"

    val main = listOf(
        MenuItem("Move to category", current) { page = "move"; index = 0 },
        MenuItem("New category…") { page = "new" },
        if (channel.hidden) MenuItem("Show in Subfeed") {
            onAction("${channel.name} is back in your feed") { setChannelHidden(channel.id, false) }
        } else MenuItem("Hide from Subfeed") { page = "confirm" },
    )
    val move = categoryMoveItems(categories, channel.categoryId, onNew = { page = "new" }) { id, name ->
        onAction("${channel.name} moved to $name") { setChannelCategory(channel.id, id) }
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
                        AKey.KEYCODE_DPAD_UP -> { if (down && page != "confirm") index = (index - 1).coerceAtLeast(0); true }
                        AKey.KEYCODE_DPAD_DOWN -> { if (down && page != "confirm") index = (index + 1).coerceAtMost(items.lastIndex); true }
                        AKey.KEYCODE_DPAD_CENTER, AKey.KEYCODE_ENTER, AKey.KEYCODE_NUMPAD_ENTER -> {
                            // Only a press that started while the panel is open counts; the key-up of the
                            // long-press that opened it (and its key-repeats) never set sawDown.
                            if (down) { if (ne.repeatCount == 0) sawDown = true }
                            else {
                                if (sawDown) {
                                    if (page == "confirm") onAction(HIDDEN_HINT) { setChannelHidden(channel.id, true) }
                                    else items.getOrNull(index)?.run?.invoke()
                                }
                                sawDown = false
                            }
                            true
                        }
                        AKey.KEYCODE_BACK -> {
                            if (!down) {
                                when (page) {
                                    "main" -> onClose()
                                    "confirm" -> { page = "main"; index = 0 }
                                    else -> { page = "main"; index = 0 }
                                }
                            }
                            true
                        }
                        AKey.KEYCODE_DPAD_LEFT, AKey.KEYCODE_DPAD_RIGHT -> true
                        else -> false
                    }
                }
                .focusable()
        ) {
            Text(channel.name, style = Type.heroMeta.copy(fontSize = Type.cardTitle.fontSize), color = Palette.text, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Spacer(Modifier.height(28.dp))
            when (page) {
                "new" -> NewCategory(onDone = { name ->
                    if (name.isNotBlank()) {
                        onAction("${channel.name} moved to ${name.trim()}") {
                            val c = createCategory(name.trim())
                            setChannelCategory(channel.id, c.id)
                        }
                    } else onClose()
                }, onCancel = { page = "main" })
                "confirm" -> {
                    Text(HIDE_CONFIRM, style = Type.heroMeta, color = Palette.text)
                    Spacer(Modifier.height(16.dp))
                    Text("OK hides it. Back cancels.", style = Type.chip, color = Palette.textFaint)
                }
                else -> {
                    if (page == "move") {
                        Text("Move ${channel.name} to", style = Type.chip, color = Palette.accent)
                        Spacer(Modifier.height(12.dp))
                    }
                    items.forEachIndexed { i, it -> MenuRow(it, i == index) }
                }
            }
        }
    }
    LaunchedEffect(page) { if (page != "new") requester.requestFocus() }
}
