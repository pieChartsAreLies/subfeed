package com.gerstl.subfeed

import kotlinx.serialization.KSerializer
import kotlinx.serialization.builtins.ListSerializer
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import java.util.concurrent.TimeUnit

/** A failure the TV can show as one plain sentence. [status] is 0 when the service was unreachable. */
class ApiError(val sentence: String, val status: Int) : Exception(sentence)

/**
 * Blocking client for the Subfeed service (call from a background dispatcher). Every request
 * carries the bearer token; TLS trust for the service host is the bundled Caddy root only
 * (network_security_config.xml).
 */
class Api(
    val base: String = BuildConfig.API_BASE,
    private val token: String = BuildConfig.API_TOKEN,
) {
    val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(45, TimeUnit.SECONDS)
        .callTimeout(60, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    val authHeader: Pair<String, String> get() = "Authorization" to "Bearer $token"

    private val jsonType = "application/json".toMediaType()

    private fun request(path: String) = Request.Builder().url(base + path).header(authHeader.first, authHeader.second)

    private fun <T> call(req: Request, read: (Response) -> T): T {
        try {
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) throw ApiError(sentenceFrom(resp), resp.code)
                return read(resp)
            }
        } catch (e: ApiError) {
            throw e
        } catch (e: IOException) {
            throw ApiError(UNREACHABLE, 0)
        }
    }

    private fun sentenceFrom(resp: Response): String {
        val text = try {
            resp.body?.string().orEmpty()
        } catch (_: Exception) {
            ""
        }
        val parsed = try {
            SubfeedJson.decodeFromString(ErrorBody.serializer(), text)
        } catch (_: Exception) {
            null
        }
        return parsed?.sentence ?: parsed?.message ?: when (resp.code) {
            401, 403 -> "This TV isn't allowed to use Subfeed. The app needs a new build."
            502, 504 -> "Subfeed isn't running on the home server right now."
            else -> "Something went wrong talking to Subfeed."
        }
    }

    private fun <T> get(path: String, s: KSerializer<T>): T =
        call(request(path).get().build()) { SubfeedJson.decodeFromString(s, it.body!!.string()) }

    private fun <B, T> post(path: String, bs: KSerializer<B>, body: B, s: KSerializer<T>?): T? =
        call(request(path).post(SubfeedJson.encodeToString(bs, body).toRequestBody(jsonType)).build()) { r ->
            if (s == null || r.code == 204) null else SubfeedJson.decodeFromString(s, r.body!!.string())
        }

    fun getRaw(path: String): String = call(request(path).get().build()) { it.body!!.string() }

    fun postRaw(path: String): String =
        call(request(path).post("{}".toRequestBody(jsonType)).build()) { it.body!!.string() }

    fun feedJson(): String = call(request("/api/feed").get().build()) { it.body!!.string() }

    fun play(id: String, req: PlayRequest): PlayResponse =
        post("/api/videos/$id/play", PlayRequest.serializer(), req, PlayResponse.serializer())!!

    fun detail(id: String): Detail = get("/api/videos/$id/detail", Detail.serializer())

    /** Returns the service's watched verdict. */
    fun progress(id: String, body: ProgressBody): Boolean =
        post("/api/videos/$id/progress", ProgressBody.serializer(), body, WatchedOut.serializer())?.watched ?: false

    fun markWatched(id: String, watched: Boolean) {
        post<WatchedIn, Unit>("/api/videos/$id/watched", WatchedIn.serializer(), WatchedIn(watched), null)
    }

    fun tabSeen(tabId: String) {
        call(request("/api/tabs/$tabId/seen").post(ByteArray(0).toRequestBody(null)).build()) { }
    }

    fun channels(): List<ChannelInfo> = get("/api/channels", ListSerializer(ChannelInfo.serializer()))

    fun channelVideos(id: String, offset: Int = 0, limit: Int = 100): ChannelVideos =
        get("/api/channels/$id/videos?offset=$offset&limit=$limit", ChannelVideos.serializer())

    fun setChannelCategory(channelId: String, categoryId: Int?) {
        val body = if (categoryId == null) "{\"category_id\":null}" else "{\"category_id\":$categoryId}"
        call(request("/api/channels/$channelId").put(body.toRequestBody(jsonType)).build()) { }
    }

    fun setChannelHidden(channelId: String, hidden: Boolean) {
        call(request("/api/channels/$channelId").put("{\"hidden\":$hidden}".toRequestBody(jsonType)).build()) { }
    }

    fun createCategory(name: String): CategoryOut =
        post("/api/categories", NameIn.serializer(), NameIn(name), CategoryOut.serializer())!!

    fun save(id: String) {
        post<SaveIn, Unit>("/api/videos/$id/save", SaveIn.serializer(), SaveIn("1080p"), null)
    }

    fun unsave(id: String) {
        call(request("/api/videos/$id/save").delete().build()) { }
    }

    fun healthJson(): String = call(request("/api/health").get().build()) { it.body!!.string() }

    companion object {
        const val UNREACHABLE = "Can't reach Subfeed on the home network."
    }
}

@kotlinx.serialization.Serializable
data class WatchedOut(val watched: Boolean = false)

@kotlinx.serialization.Serializable
data class WatchedIn(val watched: Boolean)

@kotlinx.serialization.Serializable
data class NameIn(val name: String)

@kotlinx.serialization.Serializable
data class SaveIn(val quality: String)
