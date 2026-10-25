package com.gerstl.subfeed

import android.view.KeyEvent as AKey
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.focusable
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Text
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private const val AREA_CHIPS = 0
private const val AREA_LETTERS = 1
private const val AREA_SORT = 2
private const val AREA_LIST = 3

private data class Chip(val label: String, val key: String?)

/** UI state of the Channels screen; Root keeps one while Screen.Channels is on the stack so Back from a channel grid returns here unchanged. */
class ChannelsState {
    var all by mutableStateOf<List<ChannelInfo>?>(null)
    var filter by mutableStateOf(ChannelList.Filter(null, null))
    var sort by mutableStateOf(ChannelList.Sort.AZ)
    var area by mutableIntStateOf(3)
    var chipIdx by mutableIntStateOf(0)
    var letterIdx by mutableIntStateOf(0)
    var focusId by mutableStateOf<String?>(null)
    /** Kept here so the scroll position survives Back from a channel grid. */
    val listState = LazyListState()
    /** Ids shown by the last composition, to find the neighbour when the focused row drops out. */
    var shownIds: List<String> = emptyList()

    fun applyFilter(f: ChannelList.Filter) { filter = f; focusId = null }
}

/**
 * Settings > Channels: every channel, A to Z or least recently watched, filtered by category
 * and first letter. Areas top to bottom: chips, letters, sort toggle, list.
 */
@Composable
fun ChannelsScreen(st: ChannelsState, vm: BrowseViewModel, api: Api, onOpen: (String) -> Unit, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    var all by st::all
    var error by remember { mutableStateOf<String?>(null) }
    var filter by st::filter
    var sort by st::sort
    var area by st::area
    var chipIdx by st::chipIdx
    var letterIdx by st::letterIdx
    var focusId by st::focusId
    var panel by remember { mutableStateOf<ChannelInfo?>(null) }
    var hint by remember { mutableStateOf<String?>(null) }
    var hintToken by remember { mutableIntStateOf(0) }
    var centerLong by remember { mutableStateOf(false) }
    val requester = remember { FocusRequester() }
    val listState = st.listState
    val chipState = rememberLazyListState()

    val categories = approvedCategories(vm.feed)
    val chips = remember(categories) {
        listOf(Chip("All", null)) + categories.map { Chip(it.second, "cat-${it.first}") } +
            Chip("Other", "other") + Chip("Hidden", "hidden")
    }
    val list = ChannelList.apply(all.orEmpty(), filter, sort, categories.map { it.first }.toSet())
    val showClear = ChannelList.showClearFilters(list, filter)
    val listIdx = list.indexOfFirst { it.id == focusId }.coerceAtLeast(0)

    fun showHint(text: String) { hint = text; hintToken++ }

    suspend fun reload() {
        try {
            all = withContext(Dispatchers.IO) { api.channels() }
            error = null
        } catch (e: ApiError) {
            error = e.sentence
            if (all == null) all = emptyList()
        }
    }

    fun runAction(success: String, block: Api.() -> Unit) {
        val keep = panel?.id
        panel = null
        scope.launch {
            try {
                withContext(Dispatchers.IO) { api.block() }
                reload()
                keep?.let { focusId = it }
                showHint(success)
                vm.refresh()
            } catch (e: ApiError) {
                showHint(e.sentence)
            }
        }
    }

    fun setFilter(f: ChannelList.Filter) = st.applyFilter(f)

    fun jumpToLetter() {
        val i = ChannelList.firstIndexWithLetter(list, ChannelList.LETTERS[letterIdx])
        if (i >= 0) focusId = list[i].id
    }

    Box(
        Modifier.fillMaxSize().background(Palette.bg)
            .focusRequester(requester)
            .onKeyEvent { e ->
                val ne = e.nativeKeyEvent
                val down = ne.action == AKey.ACTION_DOWN
                val center = ne.keyCode == AKey.KEYCODE_DPAD_CENTER || ne.keyCode == AKey.KEYCODE_ENTER || ne.keyCode == AKey.KEYCODE_NUMPAD_ENTER
                if (panel != null) {
                    // the key-up of the long-press that opened the panel can land here
                    if (center && !down) centerLong = false
                    return@onKeyEvent center
                }
                when {
                    ne.keyCode == AKey.KEYCODE_BACK -> { if (!down) onBack(); true }
                    ne.keyCode == AKey.KEYCODE_DPAD_UP -> {
                        if (down) {
                            if (area == AREA_LIST && !showClear && listIdx > 0) focusId = list[listIdx - 1].id
                            else if (area > AREA_CHIPS) area--
                        }
                        true
                    }
                    ne.keyCode == AKey.KEYCODE_DPAD_DOWN -> {
                        if (down) {
                            if (area == AREA_LIST) { if (!showClear && listIdx < list.lastIndex) focusId = list[listIdx + 1].id }
                            else if (area < AREA_SORT || list.isNotEmpty() || showClear) area++
                        }
                        true
                    }
                    ne.keyCode == AKey.KEYCODE_DPAD_LEFT || ne.keyCode == AKey.KEYCODE_DPAD_RIGHT -> {
                        if (down) {
                            val step = if (ne.keyCode == AKey.KEYCODE_DPAD_RIGHT) 1 else -1
                            when (area) {
                                AREA_CHIPS -> chipIdx = (chipIdx + step).coerceIn(0, chips.lastIndex)
                                AREA_LETTERS -> {
                                    letterIdx = (letterIdx + step).coerceIn(0, ChannelList.LETTERS.lastIndex)
                                    jumpToLetter()
                                }
                            }
                        }
                        true
                    }
                    center -> {
                        if (area == AREA_LIST && !showClear && list.isNotEmpty()) {
                            val ch = list[listIdx]
                            if (down) {
                                if (ne.repeatCount == 0) centerLong = false
                                if (ne.repeatCount >= 1 && !centerLong) { centerLong = true; panel = ch }
                            } else {
                                if (!centerLong) onOpen(ch.id)
                                centerLong = false
                            }
                        } else if (!down) {
                            when (area) {
                                AREA_CHIPS -> setFilter(filter.copy(category = chips[chipIdx].key))
                                AREA_LETTERS -> {
                                    val l = ChannelList.LETTERS[letterIdx]
                                    setFilter(filter.copy(letter = if (filter.letter == l) null else l))
                                }
                                AREA_SORT -> sort = if (sort == ChannelList.Sort.AZ) ChannelList.Sort.LEAST_RECENT else ChannelList.Sort.AZ
                                else -> if (showClear) {
                                    setFilter(ChannelList.Filter(null, null))
                                    area = if (all.orEmpty().isEmpty()) AREA_CHIPS else AREA_LIST
                                }
                            }
                        }
                        true
                    }
                    else -> true
                }
            }
            .focusable()
    ) {
        Column(Modifier.fillMaxSize().padding(horizontal = 72.dp, vertical = 40.dp)) {
            Text("Channels", style = Type.big, color = Palette.text)
            Spacer(Modifier.height(16.dp))
            LazyRow(state = chipState) {
                itemsIndexed(chips) { i, c ->
                    Pill(c.label, focused = area == AREA_CHIPS && i == chipIdx, active = c.key == filter.category)
                    Spacer(Modifier.width(10.dp))
                }
            }
            Spacer(Modifier.height(12.dp))
            Row {
                ChannelList.LETTERS.forEachIndexed { i, l ->
                    val focused = area == AREA_LETTERS && i == letterIdx
                    Box(
                        Modifier.padding(end = 4.dp).width(34.dp).height(34.dp).clip(RoundedCornerShape(8.dp))
                            .background(if (focused) Palette.text else Palette.surface),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(
                            l.toString(), style = Type.chip,
                            color = when {
                                focused -> Palette.bg
                                l == filter.letter -> Palette.accent
                                else -> Palette.textDim
                            },
                        )
                    }
                }
            }
            Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Sort", style = Type.chip, color = Palette.textFaint)
                Spacer(Modifier.width(10.dp))
                Pill(
                    if (sort == ChannelList.Sort.AZ) "A to Z" else "Least recently watched",
                    focused = area == AREA_SORT, active = false,
                )
            }
            Spacer(Modifier.height(16.dp))
            when {
                error != null && all.orEmpty().isEmpty() -> Text(error ?: "", style = Type.heroMeta, color = Palette.danger)
                all == null -> Text("Loading", style = Type.heroMeta, color = Palette.textDim)
                showClear -> {
                    Text("No channels match.", style = Type.heroMeta, color = Palette.textDim)
                    Spacer(Modifier.height(12.dp))
                    Pill("Clear filters", focused = area == AREA_LIST, active = false)
                }
                list.isEmpty() -> Text("No channels yet.", style = Type.heroMeta, color = Palette.textDim)
                else -> LazyColumn(state = listState, modifier = Modifier.fillMaxSize()) {
                    itemsIndexed(list, key = { _, c -> c.id }) { i, c ->
                        ChannelRow(c, categories, focused = area == AREA_LIST && i == listIdx)
                    }
                }
            }
            if (error != null && all.orEmpty().isNotEmpty()) {
                Text(error ?: "", style = Type.heroMeta, color = Palette.danger)
            }
        }
        HintChip(hint, Modifier.align(Alignment.BottomCenter).padding(bottom = 40.dp))
    }
    panel?.let { ch ->
        ChannelPanel(ch, categories, onAction = { s, b -> runAction(s, b) }, onClose = { panel = null })
    }

    LaunchedEffect(Unit) { reload() }
    // The focused row left the list (moved or hidden under a filter): focus its neighbour, not row 0.
    val shownIds = list.map { it.id }
    LaunchedEffect(shownIds) {
        focusId = ChannelList.focusAfter(st.shownIds, shownIds, focusId)
        st.shownIds = shownIds
    }
    LaunchedEffect(showClear) { if (showClear) area = AREA_LIST }
    LaunchedEffect(panel == null) { if (panel == null) requester.requestFocus() }
    LaunchedEffect(hintToken) { if (hint != null) { delay(2600); hint = null } }
    LaunchedEffect(chipIdx) { chipState.animateScrollToItem(chipIdx) }
    // keep the focused row on screen without recentring on every step
    LaunchedEffect(focusId, list.size, sort) {
        if (list.isEmpty()) return@LaunchedEffect
        val visible = listState.layoutInfo.visibleItemsInfo
        if (visible.isEmpty()) { listState.scrollToItem(listIdx); return@LaunchedEffect }
        val first = visible.first().index
        val last = visible.last().index
        if (listIdx < first) listState.animateScrollToItem(listIdx)
        else if (listIdx >= last) listState.animateScrollToItem((listIdx - (last - first - 1)).coerceAtLeast(0))
    }
}

@Composable
private fun Pill(label: String, focused: Boolean, active: Boolean) {
    Box(
        Modifier.clip(RoundedCornerShape(50))
            .background(if (focused) Palette.text else Palette.surface)
            .then(if (active && !focused) Modifier.border(2.dp, Palette.accent, RoundedCornerShape(50)) else Modifier)
            .padding(horizontal = 18.dp, vertical = 8.dp)
    ) {
        Text(label, style = Type.chip, color = if (focused) Palette.bg else if (active) Palette.accent else Palette.textDim, maxLines = 1)
    }
}

@Composable
private fun ChannelRow(c: ChannelInfo, categories: List<Pair<Int, String>>, focused: Boolean) {
    val category = categories.firstOrNull { it.first == c.categoryId }?.second ?: "Other"
    val detail = buildList {
        add(category)
        if (c.unwatchedCount > 0) add("${c.unwatchedCount} unwatched")
        Format.age(c.lastUploadAt).takeIf { it.isNotEmpty() }?.let { add("Last upload $it") }
        add(Format.age(c.lastWatchedAt).takeIf { it.isNotEmpty() }?.let { "Last watched $it" } ?: "Never watched")
    }.joinToString("  ·  ")
    Column(
        Modifier.fillMaxWidth().height(68.dp).padding(vertical = 3.dp).clip(RoundedCornerShape(10.dp))
            .background(if (focused) Palette.text else Palette.surface)
            .padding(horizontal = 16.dp, vertical = 6.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                c.name, style = Type.cardTitle, color = if (focused) Palette.bg else Palette.text,
                maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false),
            )
            if (c.hidden) {
                Spacer(Modifier.width(12.dp))
                Text("Hidden", style = Type.badge, color = if (focused) Palette.bg else Palette.accent)
            }
        }
        Text(detail, style = Type.chip, color = if (focused) Palette.surfaceHi else Palette.textFaint, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}
