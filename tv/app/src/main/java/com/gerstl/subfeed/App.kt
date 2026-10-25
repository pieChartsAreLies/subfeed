package com.gerstl.subfeed

import android.app.Application
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import coil.ImageLoader
import coil.ImageLoaderFactory
import coil.disk.DiskCache
import coil.memory.MemoryCache
import okhttp3.OkHttpClient
import java.io.File
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class App : Application(), ImageLoaderFactory {
    lateinit var api: Api
        private set
    lateinit var repo: FeedRepository
        private set
    /** Progress the service hasn't acknowledged yet; survives player sessions and restarts. */
    lateinit var outbox: ProgressOutbox
        private set
    /** The one thread that posts progress (shared by every player session, never shut down). */
    val io: ExecutorService = Executors.newSingleThreadExecutor()

    override fun onCreate() {
        super.onCreate()
        api = Api()
        repo = FeedRepository(FeedCache(File(filesDir, "feed.json"))) { api.feedJson() }
        outbox = ProgressOutbox(File(filesDir, "progress-outbox.json"))
        // Deliver anything a previous session couldn't send (e.g. the end of a video while offline).
        io.execute {
            outbox.flush { id, body ->
                try { api.progress(id, body); true } catch (e: ApiError) { e.status == 404 }
            }
        }
    }

    /** Thumbnails come from public image hosts: plain system trust, generous caches. */
    override fun newImageLoader(): ImageLoader = ImageLoader.Builder(this)
        .okHttpClient { OkHttpClient.Builder().connectTimeout(8, TimeUnit.SECONDS).readTimeout(15, TimeUnit.SECONDS).build() }
        .memoryCache { MemoryCache.Builder(this).maxSizePercent(0.25).build() }
        .diskCache { DiskCache.Builder().directory(cacheDir.resolve("thumbs")).maxSizeBytes(250L * 1024 * 1024).build() }
        .respectCacheHeaders(false)
        .build()
}

sealed interface Screen {
    data object Browse : Screen
    data class Player(val args: PlayerArgs) : Screen
    data object Settings : Screen
    data object Channels : Screen
    data class Channel(val channelId: String, val focusId: String?) : Screen
    /** Action menu over [under] (Browse or a Channel grid); closing it returns there. */
    data class Menu(val video: Video, val under: Screen) : Screen
}

class MainActivity : ComponentActivity() {
    private val browse: BrowseViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val app = application as App
        setContent {
            SubfeedTheme { Root(browse, app.api) }
        }
    }

    override fun onStart() {
        super.onStart()
        browse.refresh()
    }
}

@Composable
private fun Root(browse: BrowseViewModel, api: Api) {
    val nav = remember { NavStack<Screen>(Screen.Browse) }
    // NavStack is plain Kotlin; this counter is what makes Compose re-read nav.top after a change.
    var version by remember { mutableIntStateOf(0) }
    val channels = remember { mutableMapOf<String, ChannelGridState>() }
    val channelsState = remember { arrayOfNulls<ChannelsState>(1) }
    fun changed() { version++ }
    fun push(s: Screen) { nav.push(s); changed() }
    fun pop() {
        val leaving = nav.top
        if (nav.pop()) {
            if (leaving is Screen.Channel) channels.remove(leaving.channelId)
            if (leaving is Screen.Channels) channelsState[0] = null
            changed()
        }
    }

    @Composable
    fun channelGrid(c: Screen.Channel, active: Boolean) {
        val st = channels.getOrPut(c.channelId) { ChannelGridState(c.channelId) }
        ChannelGridScreen(
            st, api, browse, c.focusId, active,
            onPlay = { push(Screen.Player(it)) },
            onMenu = { push(Screen.Menu(it, under = nav.top)) },
            onBack = { pop() },
        )
    }

    Box(Modifier.fillMaxSize().background(Palette.bg)) {
        version.let { }
        val s = nav.top
        // A menu draws the screen it was opened over, with the action list on top.
        val shown = if (s is Screen.Menu) s.under else s
        when (shown) {
            Screen.Browse -> BrowseScreen(
                browse,
                active = s is Screen.Browse,
                onPlay = { push(Screen.Player(it)) },
                onMenu = { push(Screen.Menu(it, under = Screen.Browse)) },
                onSettings = { push(Screen.Settings) },
            )
            is Screen.Channel -> channelGrid(shown, active = s is Screen.Channel)
            is Screen.Player -> PlayerScreen(shown.args, api) { lastId ->
                pop()
                val top = nav.top
                if (top is Screen.Channel) {
                    nav.replaceTop(top.copy(focusId = lastId))
                    changed()
                } else {
                    browse.returnFrom(shown.args.tabId, lastId)
                }
            }
            Screen.Settings -> SettingsScreen(browse, api, onChannels = { push(Screen.Channels) }, onClose = { pop() })
            Screen.Channels -> ChannelsScreen(channelsState[0] ?: ChannelsState().also { channelsState[0] = it }, browse, api, onOpen = { push(Screen.Channel(it, null)) }, onBack = { pop() })
            is Screen.Menu -> Unit
        }
        if (s is Screen.Menu) {
            val under = s.under
            ActionMenu(
                s.video, browse, api,
                onClose = { pop() },
                onGoToChannel = { v ->
                    pop()
                    // Already looking at this channel's grid: closing the menu is the whole job.
                    if (!(under is Screen.Channel && under.channelId == v.channelId)) push(Screen.Channel(v.channelId, null))
                },
                onPlayFromStart = { v ->
                    val base = if (under is Screen.Channel) channels[under.channelId]?.playArgsFor(v.id) else browse.playArgsFor(v.id)
                    pop()
                    if (base != null) push(Screen.Player(base.copy(fromStart = true)))
                },
            )
        }
    }
}
