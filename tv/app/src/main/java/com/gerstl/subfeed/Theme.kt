package com.gerstl.subfeed

import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.tv.material3.ExperimentalTvMaterial3Api
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.darkColorScheme

/** Near-black for OLED (not pure #000 on big panels), warm amber accent, soft greys. */
object Palette {
    val bg = Color(0xFF0D0E12)
    val surface = Color(0xFF17191F)
    val surfaceHi = Color(0xFF22252D)
    val text = Color(0xFFF2F3F5)
    val textDim = Color(0xFFB4B8C2)
    val textFaint = Color(0xFF7D828E)
    val accent = Color(0xFFFFB547)
    val onAccent = Color(0xFF1A1305)
    val watchedScrim = Color(0xB3000000)
    val focusRing = Color(0xFFFFFFFF)
    val danger = Color(0xFFFF7A6B)
}

object Type {
    private val sans = FontFamily.SansSerif
    val tab = TextStyle(fontFamily = sans, fontSize = 20.sp, fontWeight = FontWeight.Medium, letterSpacing = 0.2.sp)
    val heroTitle = TextStyle(fontFamily = sans, fontSize = 34.sp, fontWeight = FontWeight.SemiBold, lineHeight = 40.sp, letterSpacing = (-0.3).sp)
    val heroMeta = TextStyle(fontFamily = sans, fontSize = 18.sp, fontWeight = FontWeight.Normal, lineHeight = 24.sp)
    val heroBody = TextStyle(fontFamily = sans, fontSize = 17.sp, fontWeight = FontWeight.Normal, lineHeight = 24.sp)
    val chip = TextStyle(fontFamily = sans, fontSize = 14.sp, fontWeight = FontWeight.Medium)
    val cardTitle = TextStyle(fontFamily = sans, fontSize = 20.sp, fontWeight = FontWeight.Medium, lineHeight = 25.sp)
    val badge = TextStyle(fontFamily = sans, fontSize = 13.sp, fontWeight = FontWeight.Bold)
    val big = TextStyle(fontFamily = sans, fontSize = 26.sp, fontWeight = FontWeight.SemiBold, lineHeight = 34.sp)
}

object Dims {
    val gutter = 48.dp
    val cardWidth = 240.dp
    val cardGap = 18.dp
    val radius = 12.dp
}

@OptIn(ExperimentalTvMaterial3Api::class)
@Composable
fun SubfeedTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = Palette.accent,
            onPrimary = Palette.onAccent,
            background = Palette.bg,
            surface = Palette.surface,
            onSurface = Palette.text,
            onBackground = Palette.text,
        ),
        content = content,
    )
}
