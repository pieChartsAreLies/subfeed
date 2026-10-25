package com.gerstl.subfeed

import android.view.KeyEvent as AKey
import androidx.compose.foundation.background
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Text
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import java.time.Instant

/** Health as plain sentences, one per line, with a good/bad dot. Pure so it can be read in tests. */
object HealthSentences {
    fun from(h: JsonObject, now: Instant = Instant.now()): List<Pair<Boolean, String>> {
        fun s(k: String) = h[k]?.jsonPrimitive?.contentOrNull
        fun b(k: String) = h[k]?.jsonPrimitive?.booleanOrNull
        fun l(k: String) = h[k]?.jsonPrimitive?.longOrNull
        val out = mutableListOf<Pair<Boolean, String>>()
        val limited = b("youtube_limited") == true
        out += if (limited) false to (s("message") ?: "YouTube is limiting us right now. Your saved videos still work.")
        else true to "YouTube is playing normally."
        s("last_poll")?.let { out += true to "New uploads were last checked ${Format.age(it.substringBefore('.').let { t -> if (t.endsWith("Z")) t else "${t}Z" }, now).lowercase()}." }
            ?: run { out += false to "New uploads haven't been checked yet." }
        when (b("canary_ok")) {
            true -> out += true to "The playback check passed."
            false -> out += false to "The last playback check failed."
            null -> {}
        }
        when (b("nas_mounted")) {
            false -> out += false to "Can't reach the NAS, so saving is paused."
            true -> {
                val used = l("saved_bytes") ?: 0
                val quota = l("saved_quota_bytes") ?: 0
                if (quota > 0) out += true to "Saved videos use ${gb(used)} of ${gb(quota)}."
            }
            null -> {}
        }
        if (s("last_sub_sync") == null) out += false to "Your subscriptions haven't synced from Google yet, so Subfeed uses its starting channel list."
        s("ytdlp_version")?.let { out += true to "Video extractor version $it." }
        return out
    }

    private const val GIB = 1024.0 * 1024 * 1024
    private fun gb(b: Long): String = if (b < 10 * GIB) "%.1f GB".format(b / GIB) else "${(b / GIB).toLong()} GB"
}

/** /api/auth/status read as sentences. Pure so it can be read in tests. */
object AuthStatus {
    data class S(val signedIn: Boolean, val signingIn: Boolean, val account: String?, val error: String?, val summary: String?)

    sealed interface Code {
        data object Waiting : Code
        data object Done : Code
        data class Failed(val sentence: String) : Code
    }

    fun parse(j: JsonObject): S {
        fun s(k: String) = (j[k] as? JsonPrimitive)?.contentOrNull
        fun b(k: String) = (j[k] as? JsonPrimitive)?.booleanOrNull == true
        return S(b("signed_in"), b("signing_in"), s("account"), s("error"), summary(j["import_summary"]))
    }

    /** "From Google: 12 channels added, 3 removed, 17 kept." or null before the first import. */
    fun summary(e: JsonElement?): String? {
        val o = e as? JsonObject ?: return null
        fun n(k: String) = (o[k] as? JsonPrimitive)?.longOrNull ?: 0L
        val added = n("added")
        return "From Google: $added channel${if (added == 1L) "" else "s"} added, ${n("removed")} removed, ${n("kept")} kept."
    }

    fun line(s: S): String = when {
        s.signingIn -> "Waiting for you to finish signing in on your phone."
        s.error != null -> s.error
        s.signedIn -> "Signed in to Google${s.account?.let { " as $it" } ?: ""}."
        else -> "Not signed in to Google yet."
    }

    /** What the code card does next. An older token keeps signed_in true while a new attempt
     *  runs, so nothing is decided until signing_in is false. */
    fun code(s: S): Code = when {
        s.signingIn -> Code.Waiting
        s.error != null -> Code.Failed(s.error)
        s.signedIn -> Code.Done
        else -> Code.Failed("Sign-in didn't finish. Choose Sign in to try again.")
    }
}

@Composable
fun SettingsScreen(vm: BrowseViewModel, api: Api, onChannels: () -> Unit, onClose: () -> Unit) {
    val scope = rememberCoroutineScope()
    var health by remember { mutableStateOf<List<Pair<Boolean, String>>>(emptyList()) }
    var signIn by remember { mutableStateOf<String?>(null) }
    var index by remember { mutableIntStateOf(0) }
    var signedIn by remember { mutableStateOf(false) }
    var importLine by remember { mutableStateOf<String?>(null) }
    var code by remember { mutableStateOf<Pair<String, String>?>(null) }   // user_code to verification_url
    var codeError by remember { mutableStateOf<String?>(null) }
    val requester = remember { FocusRequester() }

    fun load() {
        scope.launch {
            try {
                val j = withContext(Dispatchers.IO) { SubfeedJson.parseToJsonElement(api.healthJson()).jsonObject }
                health = HealthSentences.from(j)
            } catch (e: ApiError) {
                health = listOf(false to e.sentence)
            } catch (_: Exception) {
            }
            signIn = try {
                val st = AuthStatus.parse(withContext(Dispatchers.IO) { SubfeedJson.parseToJsonElement(api.getRaw("/api/auth/status")).jsonObject })
                signedIn = st.signedIn
                importLine = st.summary
                AuthStatus.line(st)
            } catch (e: ApiError) {
                if (e.status == 404) "Google sign-in isn't set up on the server yet." else e.sentence
            }
        }
    }

    fun startSignIn() {
        scope.launch {
            codeError = null
            try {
                val j = withContext(Dispatchers.IO) { SubfeedJson.parseToJsonElement(api.postRaw("/api/auth/device/start")).jsonObject }
                val c = j["user_code"]?.jsonPrimitive?.contentOrNull
                val u = j["verification_url"]?.jsonPrimitive?.contentOrNull
                if (c == null || u == null) { codeError = "Google didn't send a sign-in code. Try again."; return@launch }
                code = c to u
            } catch (e: ApiError) {
                codeError = e.sentence
            }
        }
    }

    // While a code is on screen, check every 5 s whether the user finished on their phone. The
    // server keeps signing_in true until the attempt (and the first sync) is over; then the card
    // closes and either the feed refreshes or the failure sentence shows under the button.
    LaunchedEffect(code) {
        if (code == null) return@LaunchedEffect
        while (true) {
            kotlinx.coroutines.delay(5_000)
            val st = try {
                AuthStatus.parse(withContext(Dispatchers.IO) { SubfeedJson.parseToJsonElement(api.getRaw("/api/auth/status")).jsonObject })
            } catch (_: Exception) { null } ?: continue
            when (val next = AuthStatus.code(st)) {
                AuthStatus.Code.Waiting -> continue
                AuthStatus.Code.Done -> { code = null; codeError = null; load(); vm.refresh() }
                is AuthStatus.Code.Failed -> { code = null; codeError = next.sentence; load() }
            }
            break
        }
    }

    Box(
        Modifier.fillMaxSize().background(Palette.bg)
            .focusRequester(requester)
            .onKeyEvent { e ->
                val ne = e.nativeKeyEvent
                val down = ne.action == AKey.ACTION_DOWN
                when (ne.keyCode) {
                    AKey.KEYCODE_BACK -> { if (!down) { if (code != null) code = null else onClose() }; true }
                    AKey.KEYCODE_DPAD_UP -> { if (down) index = (index - 1).coerceAtLeast(0); true }
                    AKey.KEYCODE_DPAD_DOWN -> { if (down) index = (index + 1).coerceAtMost(1); true }
                    AKey.KEYCODE_DPAD_CENTER, AKey.KEYCODE_ENTER -> {
                        if (!down && index == 0 && code == null) startSignIn()
                        else if (!down && index == 1 && code == null) onChannels()
                        true
                    }
                    else -> true
                }
            }
            .focusable()
    ) {
        Row(Modifier.fillMaxSize().padding(horizontal = 72.dp, vertical = 48.dp)) {
            Column(Modifier.weight(1f).padding(end = 48.dp)) {
                Wordmark(0.9f)
                Spacer(Modifier.height(28.dp))
                SettingsRow(if (signedIn) "Sign in with a different Google account" else "Sign in with Google", "Follow your real YouTube subscriptions", index == 0)
                codeError?.let {
                    Spacer(Modifier.height(6.dp))
                    Text(it, style = Type.heroMeta, color = Palette.danger)
                }
                Spacer(Modifier.height(28.dp))
                Spacer(Modifier.height(12.dp))
                SettingsRow("Channels", "Browse, filter, move and hide your channels", index == 1)
            }
            Column(Modifier.width(480.dp)) {
                Spacer(Modifier.height(58.dp))
                Text("Status", style = Type.big, color = Palette.text)
                Spacer(Modifier.height(16.dp))
                signIn?.let { StatusLine(it.startsWith("Signed in"), it) }
                importLine?.let { StatusLine(true, it) }
                health.forEach { (good, text) -> StatusLine(good, text) }
                Spacer(Modifier.height(20.dp))
                Text("Subfeed ${BuildConfig.VERSION_NAME}", style = Type.chip, color = Palette.textFaint)
            }
        }
    }
    code?.let { (c, u) -> CodeCard(c, u) }
    LaunchedEffect(Unit) { requester.requestFocus(); load() }
}

@Composable
private fun SettingsRow(label: String, detail: String?, focused: Boolean) {
    Column(
        Modifier.fillMaxWidth().padding(vertical = 3.dp).clip(RoundedCornerShape(10.dp))
            .background(if (focused) Palette.text else Palette.surface).padding(horizontal = 16.dp, vertical = 10.dp)
    ) {
        Text(label, style = Type.cardTitle, color = if (focused) Palette.bg else Palette.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
        if (detail != null) Text(detail, style = Type.chip, color = if (focused) Palette.surfaceHi else Palette.textFaint, maxLines = 1)
    }
}

/** The TV-style Google sign-in: a big code to type on the phone. */
@Composable
private fun CodeCard(code: String, url: String) {
    Box(Modifier.fillMaxSize().background(Palette.bg), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text("On your phone, go to", style = Type.heroMeta, color = Palette.textDim)
            Spacer(Modifier.height(8.dp))
            Text(url.removePrefix("https://"), style = Type.big, color = Palette.text)
            Spacer(Modifier.height(28.dp))
            Text("and enter this code", style = Type.heroMeta, color = Palette.textDim)
            Spacer(Modifier.height(12.dp))
            Text(code, style = Type.heroTitle.copy(fontSize = androidx.compose.ui.unit.TextUnit(64f, androidx.compose.ui.unit.TextUnitType.Sp), letterSpacing = androidx.compose.ui.unit.TextUnit(6f, androidx.compose.ui.unit.TextUnitType.Sp)), color = Palette.accent)
            Spacer(Modifier.height(28.dp))
            Text("This screen moves on by itself once you're signed in. Back cancels.", style = Type.heroMeta, color = Palette.textFaint)
        }
    }
}

@Composable
private fun StatusLine(good: Boolean, text: String) {
    Row(Modifier.padding(vertical = 6.dp), verticalAlignment = Alignment.Top) {
        Box(Modifier.padding(top = 8.dp).size(10.dp).clip(CircleShape).background(if (good) Color(0xFF6BD49A) else Palette.accent))
        Spacer(Modifier.width(14.dp))
        Text(text, style = Type.heroMeta, color = Palette.textDim)
    }
}
