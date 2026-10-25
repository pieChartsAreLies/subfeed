package com.gerstl.subfeed

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Text
import android.view.KeyEvent as AKey

/** Pieces shared by the video action menu and the Channels screen's side panel. */
internal data class MenuItem(val label: String, val detail: String? = null, val run: () -> Unit)

/** The feed's category tabs as (id, name). */
internal fun approvedCategories(feed: Feed?): List<Pair<Int, String>> =
    feed?.tabs.orEmpty().filter { it.kind == "category" }
        .mapNotNull { t -> t.id.removePrefix("cat-").toIntOrNull()?.let { it to t.name } }

/** The "Move to" page: each category, Other, then New category…. [onMove] gets a null id for Other. */
internal fun categoryMoveItems(
    categories: List<Pair<Int, String>>, current: Int?, onNew: () -> Unit, onMove: (Int?, String) -> Unit,
): List<MenuItem> = buildList {
    categories.forEach { (id, name) ->
        add(MenuItem(name, if (id == current) "Current" else null) { onMove(id, name) })
    }
    add(MenuItem("Other", if (current == null) "Current" else null) { onMove(null, "Other") })
    add(MenuItem("New category…", null, onNew))
}

@Composable
internal fun MenuRow(item: MenuItem, focused: Boolean) {
    Column(
        Modifier.fillMaxWidth().padding(vertical = 3.dp).clip(RoundedCornerShape(10.dp))
            .background(if (focused) Palette.text else Color.Transparent)
            .padding(horizontal = 16.dp, vertical = 10.dp)
    ) {
        Text(item.label, style = Type.cardTitle, color = if (focused) Palette.bg else Palette.text, maxLines = 1)
        if (item.detail != null) {
            Text(item.detail, style = Type.chip, color = if (focused) Palette.surfaceHi else Palette.textFaint, maxLines = 1)
        }
    }
}

@Composable
internal fun NewCategory(onDone: (String) -> Unit, onCancel: () -> Unit) {
    var text by remember { mutableStateOf("") }
    val fr = remember { FocusRequester() }
    Text("New category name", style = Type.chip, color = Palette.accent)
    Spacer(Modifier.height(12.dp))
    BasicTextField(
        value = text,
        onValueChange = { if (it.length <= 40) text = it },
        singleLine = true,
        textStyle = Type.cardTitle.copy(color = Palette.text),
        cursorBrush = SolidColor(Palette.accent),
        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
        keyboardActions = KeyboardActions(onDone = { onDone(text) }),
        modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(Palette.surfaceHi)
            .border(2.dp, Palette.accent, RoundedCornerShape(10.dp)).padding(14.dp)
            .focusRequester(fr)
            .onKeyEvent { e ->
                if (e.nativeKeyEvent.keyCode == AKey.KEYCODE_BACK && e.nativeKeyEvent.action == AKey.ACTION_UP) { onCancel(); true } else false
            },
    )
    Spacer(Modifier.height(12.dp))
    Text("Type with the on-screen keyboard or the remote's voice button, then press Done.", style = Type.chip, color = Palette.textFaint)
    LaunchedEffect(Unit) { fr.requestFocus() }
}
