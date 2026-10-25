package com.gerstl.subfeed

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** What the player needs from the browse screen: the session's frozen queue and the titles for Up next. */
data class PlayerArgs(val tabId: String, val startId: String, val snapshot: List<String>, val videos: Map<String, Video>,
    val fromStart: Boolean = false, val autoplay: Boolean = true)

class BrowseViewModel(app: Application) : AndroidViewModel(app) {
    private val a = app as App
    private val prefs = app.getSharedPreferences("browse", 0)

    var feed by mutableStateOf<Feed?>(null)
        private set
    var focus by mutableStateOf(Focus("all", null, inTabs = true))
        private set
    /** One plain sentence when the service can't be reached (null when all is well). */
    var status by mutableStateOf<String?>(null)
        private set
    var refreshing by mutableStateOf(false)
        private set
    /** A short-lived hint, e.g. after a channel jump. */
    var hint by mutableStateOf<String?>(null)
        private set
    /** Bumped when focus should snap (no scroll animation): tab change, return from the player, refresh. */
    var snapToken by mutableStateOf(0)
        private set

    val details = mutableStateMapOf<String, Detail>()
    private val detailMisses = HashSet<String>()
    private var seenJob: Job? = null
    private var touched = false
    private var hintJob: Job? = null

    init {
        val cached = a.repo.cached()
        if (cached != null) {
            feed = cached
            focus = FocusModel.initial(cached, prefs.getString("last_tab", null))
        }
        refresh()
    }

    fun refresh() {
        if (refreshing) return
        refreshing = true
        viewModelScope.launch {
            try {
                val fresh = withContext(Dispatchers.IO) { a.repo.refresh() }
                apply(fresh)
                status = null
            } catch (e: ApiError) {
                status = e.sentence
            } catch (e: Exception) {
                status = "The feed from Subfeed couldn't be read."
            } finally {
                refreshing = false
            }
        }
    }

    private fun apply(fresh: Feed) {
        val old = feed
        // Until the user has moved, a fresh feed re-applies the entry rule (first in-progress, else
        // newest unwatched); after that, focus follows the same video id.
        focus = if (old == null || !touched) FocusModel.initial(fresh, prefs.getString("last_tab", null))
        else FocusModel.reconcile(old, fresh, focus, gridOpen)
        if (!touched) snapToken++
        feed = fresh
        // The grid's tab vanished in the refresh (reconcile fell back to the tabs): close it.
        if (gridOpen && (focus.inTabs || focus.tabId != gridReturn?.tabId)) closeGrid()
        markSeenSoon()
    }

    // ---- full-screen category grid (OK on a tab) ----

    /** True while the grid for [focus]'s tab is on screen; focus.videoId is the grid's focused card. */
    var gridOpen by mutableStateOf(false)
        private set
    /** Where Back from the grid lands: the tab, with the row item it had before. */
    private var gridReturn: Focus? = null

    fun openGrid() {
        val f = feed ?: return
        val tab = f.tab(focus.tabId)?.takeIf { it.videoIds.isNotEmpty() } ?: return
        touched = true
        gridReturn = focus.copy(inTabs = true)
        gridOpen = true
        focus = Focus(tab.id, GridFocus.entry(tab.videoIds), inTabs = false)
        snapToken++
    }

    fun closeGrid() {
        gridOpen = false
        focus = gridReturn?.takeIf { feed?.tab(it.tabId) != null } ?: focus.copy(inTabs = true)
        gridReturn = null
        snapToken++
    }

    fun onGridKey(key: Key) {
        val f = feed ?: return
        val tab = f.tab(focus.tabId) ?: return
        val id = GridFocus.move(key, tab.videoIds, focus.videoId, f)
        if (id != focus.videoId) focus = focus.copy(videoId = id)
        if (key == Key.NextUnwatched) showHint("Next unwatched")
    }

    /** The Settings item at the right end of the tab strip (UI-only; not part of FocusModel). */
    var settingsFocused by mutableStateOf(false)
        private set

    fun onKey(key: Key) {
        val f = feed ?: return
        touched = true
        if (settingsFocused) {
            if (key == Key.Left || key == Key.LongLeft || key == Key.Down) settingsFocused = false
            return
        }
        if (focus.inTabs && (key == Key.Right || key == Key.LongRight) && f.tabs.lastOrNull()?.id == focus.tabId) {
            settingsFocused = true
            return
        }
        val before = focus
        val after = FocusModel.onKey(key, f, before)
        if (after == before) return
        focus = after
        if (after.tabId != before.tabId) {
            prefs.edit().putString("last_tab", after.tabId).apply()
            snapToken++
            markSeenSoon()
        }
        if ((key == Key.LongRight || key == Key.LongLeft) && !after.inTabs) {
            f.video(after.videoId)?.let { showHint(it.channelTitle) }
        }
        if (key == Key.NextUnwatched && !after.inTabs) showHint("Next unwatched")
    }

    private fun showHint(text: String) {
        hint = text
        hintJob?.cancel()
        hintJob = viewModelScope.launch { delay(1400); hint = null }
    }

    /** New-count badges clear once a tab has been on screen for a moment. */
    private fun markSeenSoon() {
        val tabId = focus.tabId
        val t = feed?.tab(tabId) ?: return
        if (t.newCount == 0) return
        seenJob?.cancel()
        seenJob = viewModelScope.launch {
            delay(2500)
            try {
                withContext(Dispatchers.IO) { a.api.tabSeen(tabId) }
            } catch (_: Exception) {
            }
        }
    }

    fun loadDetail(id: String) {
        if (details.containsKey(id) || id in detailMisses) return
        viewModelScope.launch {
            try {
                details[id] = withContext(Dispatchers.IO) { a.api.detail(id) }
            } catch (_: Exception) {
                detailMisses += id
            }
        }
    }

    fun playArgs(): PlayerArgs? = focus.videoId?.let { playArgsFor(it) }

    /** What OK on [id] in the current tab would play (snapshot, autoplay rule). */
    fun playArgsFor(id: String): PlayerArgs? {
        val f = feed ?: return null
        val tab = f.tab(focus.tabId) ?: return null
        return PlayerArgs(tab.id, id, tab.videoIds.toList(), f.videos, autoplay = tab.kind != "continue" && tab.kind != "history")
    }

    /**
     * Back from the player: land on the exact tab and the last item that played, then refresh.
     * If playback started in the grid, the grid is still open and this focuses that card in it.
     */
    fun returnFrom(tabId: String, lastId: String) {
        touched = true
        focus = Focus(tabId, lastId, inTabs = false)
        feed?.let { focus = FocusModel.reconcile(it, it, focus, gridOpen) }
        snapToken++
        refresh()
    }

    // ---- long-press menu actions (Tier C) ----

    /** Bumps each time a menu action's API call has finished (screens showing stored lists reload on it). */
    var actionsDone by mutableIntStateOf(0)
        private set

    fun runAction(sentenceOnSuccess: String?, block: Api.() -> Unit) {
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { a.api.block() }
                sentenceOnSuccess?.let { showHint(it) }
            } catch (e: ApiError) {
                showHint(e.sentence)
            }
            actionsDone++
            refresh()
        }
    }
}
