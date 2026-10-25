package com.gerstl.subfeed

/** Remote keys as the browse screen understands them (long-presses already decoded). */
enum class Key { Up, Down, Left, Right, LongLeft, LongRight, NextUnwatched }

/** Where focus is: which tab, which video (also what the featured area shows), and whether the tab strip has it. */
data class Focus(val tabId: String, val videoId: String?, val inTabs: Boolean)

/**
 * Pure focus rules for the browse screen. The UI renders whatever this returns; nothing about
 * focus lives in Compose's focus system, so "Up from anywhere goes to the tabs" and "Back restores
 * the exact item" cannot drift with layout.
 */
object FocusModel {

    /** Tab entry: first in-progress, else newest unwatched, else newest (row order already encodes this). */
    fun entryItem(tab: Tab, feed: Feed): String? {
        val ids = tab.rowIds
        tab.continueIds.firstOrNull { it in ids }?.let { return it }
        ids.firstOrNull { feed.videos[it]?.watched == false }?.let { return it }
        return ids.firstOrNull()
    }

    fun initial(feed: Feed, rememberedTab: String?): Focus {
        val tab = feed.tab(rememberedTab ?: "") ?: feed.tabs.firstOrNull() ?: return Focus("all", null, true)
        val id = entryItem(tab, feed)
        return Focus(tab.id, id, inTabs = id == null)
    }

    fun onKey(key: Key, feed: Feed, focus: Focus): Focus {
        val tabIndex = feed.tabs.indexOfFirst { it.id == focus.tabId }
        if (tabIndex < 0) return initial(feed, null)
        val tab = feed.tabs[tabIndex]
        if (focus.inTabs) {
            return when (key) {
                Key.Left, Key.LongLeft -> switchTo(feed, tabIndex - 1) ?: focus
                Key.Right, Key.LongRight -> switchTo(feed, tabIndex + 1) ?: focus
                Key.Down -> {
                    val id = focus.videoId?.takeIf { it in tab.rowIds } ?: entryItem(tab, feed)
                    if (id == null) focus else Focus(tab.id, id, inTabs = false)
                }
                Key.Up, Key.NextUnwatched -> focus
            }
        }
        val ids = tab.rowIds
        val i = ids.indexOf(focus.videoId)
        if (i < 0) return Focus(tab.id, entryItem(tab, feed), inTabs = true)
        val target = when (key) {
            Key.Up -> return focus.copy(inTabs = true)
            Key.Down -> i
            Key.Left -> maxOf(0, i - 1)
            Key.Right -> minOf(ids.lastIndex, i + 1)
            Key.LongRight -> nextChannelStart(ids, feed, i)
            Key.LongLeft -> prevChannelStart(ids, feed, i)
            Key.NextUnwatched -> (i + 1..ids.lastIndex).firstOrNull { feed.videos[ids[it]]?.watched == false } ?: i
        }
        return focus.copy(videoId = ids[target])
    }

    private fun switchTo(feed: Feed, index: Int): Focus? {
        val t = feed.tabs.getOrNull(index) ?: return null
        return Focus(t.id, entryItem(t, feed), inTabs = true)
    }

    private fun channel(ids: List<String>, feed: Feed, i: Int) = feed.videos[ids[i]]?.channelId

    private fun nextChannelStart(ids: List<String>, feed: Feed, i: Int): Int {
        val c = channel(ids, feed, i)
        return (i + 1..ids.lastIndex).firstOrNull { channel(ids, feed, it) != c } ?: ids.lastIndex
    }

    private fun prevChannelStart(ids: List<String>, feed: Feed, i: Int): Int {
        fun runStart(j: Int): Int {
            var k = j
            val c = channel(ids, feed, j)
            while (k > 0 && channel(ids, feed, k - 1) == c) k--
            return k
        }
        val start = runStart(i)
        return if (start < i) start else if (i == 0) 0 else runStart(i - 1)
    }

    /** True when the item at [index] starts a new channel run (drawn as a visual boundary). */
    fun isChannelBoundary(tab: Tab, feed: Feed, index: Int): Boolean {
        val ids = tab.rowIds
        if (index <= 0 || index > ids.lastIndex) return false
        return channel(ids, feed, index) != channel(ids, feed, index - 1)
    }

    /** After a refresh: keep the same video id; else its nearest surviving neighbour (forward first). */
    fun reconcile(old: Feed, new: Feed, focus: Focus, gridOpen: Boolean = false): Focus {
        val newTab = new.tab(focus.tabId) ?: return initial(new, null).copy(inTabs = true)
        // with the grid open focus may legitimately sit beyond the row's cap
        val newIds = if (gridOpen) newTab.videoIds else newTab.rowIds
        val id = focus.videoId
        if (id != null && id in newIds) return focus
        if (newIds.isEmpty()) return Focus(newTab.id, null, inTabs = true)
        val oldIds = (if (gridOpen) old.tab(focus.tabId)?.videoIds else old.tab(focus.tabId)?.rowIds).orEmpty()
        val at = oldIds.indexOf(id)
        if (at >= 0) {
            val present = newIds.toHashSet()
            (at + 1..oldIds.lastIndex).firstOrNull { oldIds[it] in present }?.let { return focus.copy(videoId = oldIds[it]) }
            (at - 1 downTo 0).firstOrNull { oldIds[it] in present }?.let { return focus.copy(videoId = oldIds[it]) }
        }
        return focus.copy(videoId = entryItem(newTab, new))
    }
}
