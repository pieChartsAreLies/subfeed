package com.gerstl.subfeed

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/** Wire types. They follow the service contract (plan Revision 2 + Task 2 report) verbatim. */

val SubfeedJson = Json {
    ignoreUnknownKeys = true
    explicitNulls = false
    coerceInputValues = true
    encodeDefaults = true
}

@Serializable
data class Feed(
    val tabs: List<Tab> = emptyList(),
    val videos: Map<String, Video> = emptyMap(),
    @SerialName("generated_at") val generatedAt: String? = null,
) {
    fun tab(id: String): Tab? = tabs.firstOrNull { it.id == id }
    fun video(id: String?): Video? = id?.let { videos[it] }
    /** True when the video is listed in the Continue Watching tab. */
    fun inContinue(id: String): Boolean = tabs.any { it.kind == "continue" && id in it.videoIds }
}

@Serializable
data class Tab(
    val id: String,
    val name: String,
    val kind: String = "category",
    @SerialName("new_count") val newCount: Int = 0,
    @SerialName("video_ids") val videoIds: List<String> = emptyList(),
    @SerialName("continue_ids") val continueIds: List<String> = emptyList(),
) {
    /** What the main-screen row shows and focus moves through: History is capped at [HISTORY_ROW_LIMIT]; its grid (and every other tab) uses all of [videoIds]. */
    val rowIds: List<String> get() = if (kind == "history") videoIds.take(HISTORY_ROW_LIMIT) else videoIds

    companion object { const val HISTORY_ROW_LIMIT = 20 }
}

@Serializable
data class Video(
    val id: String,
    @SerialName("channel_id") val channelId: String = "",
    @SerialName("channel_title") val channelTitle: String = "",
    @SerialName("category_id") val categoryId: Int? = null,
    val title: String = "",
    @SerialName("published_at") val publishedAt: String? = null,
    @SerialName("duration_s") val durationS: Double? = null,
    @SerialName("thumbnail_url") val thumbnailUrl: String? = null,
    @SerialName("position_s") val positionS: Double = 0.0,
    @SerialName("max_position_s") val maxPositionS: Double = 0.0,
    val watched: Boolean = false,
    @SerialName("save_state") val saveState: String? = null,
    @SerialName("unavailable_reason") val unavailableReason: String? = null,
    val state: String? = null,
) {
    val playable: Boolean get() = unavailableReason == null
    /** 0..1 progress for the thumbnail bar; 0 when untouched or watched. */
    val progress: Float
        get() {
            val d = durationS ?: return 0f
            if (d <= 0 || watched) return 0f
            return (positionS / d).toFloat().coerceIn(0f, 1f)
        }
}

@Serializable
data class Segment(val start: Double, val end: Double)

/** Contract says `{start, title}`; the service currently passes yt-dlp's `{start_time, end_time, title}`. Accept both. */
@Serializable
data class Chapter(
    val start: Double? = null,
    @SerialName("start_time") val startTime: Double? = null,
    val title: String = "",
) {
    val at: Double get() = start ?: startTime ?: 0.0
}

@Serializable
data class PlayRequest(
    val decoders: List<String>,
    @SerialName("max_height") val maxHeight: Int,
    @SerialName("from_start") val fromStart: Boolean = false,
)

@Serializable
data class PlayResponse(
    @SerialName("session_id") val sessionId: String? = null,
    val source: String = "youtube",
    @SerialName("video_url") val videoUrl: String,
    @SerialName("audio_url") val audioUrl: String? = null,
    val headers: Map<String, String> = emptyMap(),
    @SerialName("expires_at") val expiresAt: String? = null,
    @SerialName("video_mime") val videoMime: String? = null,
    @SerialName("audio_mime") val audioMime: String? = null,
    val height: Int? = null,
    @SerialName("duration_s") val durationS: Double? = null,
    @SerialName("resume_s") val resumeS: Double = 0.0,
    val sponsor: List<Segment> = emptyList(),
    val chapters: List<Chapter> = emptyList(),
    val captions: List<CaptionTrack> = emptyList(),
)

/** One WebVTT caption track from `/play` (original language and/or English). */
@Serializable
data class CaptionTrack(
    val lang: String = "en",
    val url: String = "",
    val mime: String = "text/vtt",
    val auto: Boolean = false,
) {
    val label: String
        get() {
            val name = java.util.Locale(lang).getDisplayLanguage(java.util.Locale.ENGLISH).ifBlank { lang }
            return if (auto) "$name, auto-generated" else name
        }
}

@Serializable
data class Detail(val description: String? = null, val chapters: List<Chapter> = emptyList())

@Serializable
data class ProgressBody(
    @SerialName("position_s") val positionS: Double,
    @SerialName("duration_s") val durationS: Double,
    @SerialName("sponsor_s") val sponsorS: Double,
    val ended: Boolean = false,
    @SerialName("content_position_s") val contentPositionS: Double? = null,
)

@Serializable
data class ChannelInfo(
    val id: String,
    val name: String = "",
    @SerialName("category_id") val categoryId: Int? = null,
    @SerialName("category_source") val categorySource: String? = null,
    val hidden: Boolean = false,
    @SerialName("video_count") val videoCount: Int = 0,
    @SerialName("unwatched_count") val unwatchedCount: Int = 0,
    @SerialName("last_upload_at") val lastUploadAt: String? = null,
    @SerialName("last_watched_at") val lastWatchedAt: String? = null,
)

@Serializable
data class ChannelVideos(
    val channel: ChannelInfo,
    val total: Int = 0,
    val videos: List<Video> = emptyList(),
)

@Serializable
data class CategoryOut(val id: Int, val name: String)

@Serializable
data class ErrorBody(val sentence: String? = null, val message: String? = null)
