package com.gerstl.subfeed

import android.view.KeyEvent as AKey
import androidx.compose.foundation.background
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.input.key.KeyEvent
import androidx.compose.ui.input.key.onKeyEvent
import androidx.tv.material3.Text
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

/**
 * One channel's stored videos. Lives in Root (not in the composable) so the loaded list and the
 * focused card survive the player being pushed on top and popped again.
 */
class ChannelGridState(val channelId: String) {
    var loading by mutableStateOf(true)
    var error by mutableStateOf<String?>(null)
    var info by mutableStateOf<ChannelInfo?>(null)
    var videos by mutableStateOf<List<Video>>(emptyList())
    var focusId by mutableStateOf<String?>(null)

    val ids: List<String> get() = videos.map { it.id }
    val tabId: String get() = "channel-$channelId"

    /**
     * Fetches the channel's videos. The first load shows "Loading videos"; a reload (info already
     * there) keeps the old cards on screen and swaps them when the answer arrives, keeping focus on
     * [wantedFocus] or, if it is gone, its neighbour.
     */
    suspend fun load(api: Api, wantedFocus: String?) {
        val first = info == null
        if (first) { loading = true; error = null }
        try {
            val r = withContext(Dispatchers.IO) { api.channelVideos(channelId, 0, 200) }
            val oldIds = ids
            info = r.channel
            videos = r.videos
            // a reload keeps wherever focus is by the time the answer arrives
            focusId = GridFocus.afterReload(oldIds, r.videos.map { it.id }, if (first) wantedFocus else focusId)
            error = null
        } catch (e: ApiError) {
            if (first) error = e.sentence
        } catch (e: Exception) {
            if (first) error = "Couldn't reach the service."
        }
        loading = false
    }

    fun playArgs(): PlayerArgs? = focusId?.let { playArgsFor(it) }

    fun playArgsFor(id: String): PlayerArgs {
        return PlayerArgs(tabId, id, ids, videos.associateBy { it.id }, autoplay = true)
    }
}

@Composable
fun ChannelGridScreen(
    state: ChannelGridState,
    api: Api,
    browse: BrowseViewModel,
    wantedFocus: String?,
    active: Boolean,
    onPlay: (PlayerArgs) -> Unit,
    onMenu: (Video) -> Unit,
    onBack: () -> Unit,
) {
    val requester = remember { FocusRequester() }
    var centerLong by remember { mutableStateOf(false) }

    fun move(key: Key) {
        val feed = Feed(videos = state.videos.associateBy { it.id })
        state.focusId = GridFocus.move(key, state.ids, state.focusId, feed)
    }

    fun handle(e: KeyEvent): Boolean {
        val ne = e.nativeKeyEvent
        val down = ne.action == AKey.ACTION_DOWN
        val rc = ne.repeatCount
        if (state.loading || state.error != null) {
            if (ne.keyCode == AKey.KEYCODE_BACK) { if (!down) onBack(); return true }
            return false
        }
        when (ne.keyCode) {
            AKey.KEYCODE_DPAD_LEFT, AKey.KEYCODE_DPAD_RIGHT -> {
                if (down) HoldKeys.rowKey(ne.keyCode == AKey.KEYCODE_DPAD_RIGHT, rc)?.let { move(it) }
                return true
            }
            AKey.KEYCODE_DPAD_UP -> { if (down) move(Key.Up); return true }
            AKey.KEYCODE_DPAD_DOWN -> { if (down) move(Key.Down); return true }
            AKey.KEYCODE_MEDIA_FAST_FORWARD -> { if (down && rc == 0) move(Key.NextUnwatched); return true }
            AKey.KEYCODE_DPAD_CENTER, AKey.KEYCODE_ENTER, AKey.KEYCODE_NUMPAD_ENTER -> {
                if (down) {
                    if (rc == 0) centerLong = false
                    if (rc >= 1 && !centerLong) {
                        centerLong = true
                        state.videos.firstOrNull { it.id == state.focusId }?.let(onMenu)
                    }
                } else {
                    if (!centerLong) state.playArgs()?.let(onPlay)
                    centerLong = false
                }
                return true
            }
            AKey.KEYCODE_MEDIA_PLAY_PAUSE, AKey.KEYCODE_MEDIA_PLAY -> {
                if (down && rc == 0) state.playArgs()?.let(onPlay)
                return true
            }
            AKey.KEYCODE_BACK -> { if (!down) onBack(); return true }
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
        val err = state.error
        when {
            err != null -> Text(err, style = Type.heroMeta, color = Palette.danger, modifier = Modifier.align(Alignment.Center))
            state.loading -> Text("Loading videos", style = Type.heroMeta, color = Palette.textDim, modifier = Modifier.align(Alignment.Center))
            else -> {
                val info = state.info
                val tab = Tab(id = state.tabId, name = info?.name.orEmpty(), kind = "channel", videoIds = state.ids)
                val feed = Feed(tabs = listOf(tab), videos = state.videos.associateBy { it.id })
                GridScreen(
                    feed, tab, Focus(tab.id, state.focusId, inTabs = false), state.focusId?.let { browse.details[it] }, snapToken = 0,
                    subtitle = "Newest ${state.videos.size} videos stored",
                    hiddenLabel = info?.hidden == true,
                )
            }
        }
    }

    LaunchedEffect(active) { if (active) requester.requestFocus() }
    // Description for the strip once focus has settled, same as the tab grid.
    LaunchedEffect(state.focusId) {
        val id = state.focusId ?: return@LaunchedEffect
        delay(450)
        browse.loadDetail(id)
    }
    LaunchedEffect(state.channelId) { if (state.info == null) state.load(api, wantedFocus) }
    // Back from the player or a closed menu: refresh check marks, progress, the Hidden label.
    // actionsDone ticks when a menu action's API call has finished, so the reload sees its result.
    LaunchedEffect(active, browse.actionsDone) { if (active && state.info != null) state.load(api, null) }
    // Returning from the player re-focuses the card that was playing.
    LaunchedEffect(wantedFocus) {
        if (wantedFocus != null && state.videos.any { it.id == wantedFocus }) state.focusId = wantedFocus
    }
}
