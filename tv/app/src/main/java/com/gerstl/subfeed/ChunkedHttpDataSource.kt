package com.gerstl.subfeed

import android.net.Uri
import androidx.media3.common.C
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.TransferListener

/**
 * googlevideo throttles open-ended Range requests (measured: ~1.7 MB/s, and stalls on the TV)
 * but serves bounded ranges at full speed (11-33 MB/s). yt-dlp avoids the throttle with 10 MB
 * http chunks; this does the same for Media3: every upstream request is `Range: bytes=a-b` of at
 * most [chunkBytes], re-opened transparently when a chunk ends. Wrap DefaultHttpDataSource.
 */
class ChunkedHttpDataSource(
    private val upstream: DataSource,
    private val chunkBytes: Long = 10L * 1024 * 1024,
) : DataSource {

    private var spec: DataSpec? = null
    private var position = 0L          // absolute position of the next byte to deliver
    private var endExclusive = C.LENGTH_UNSET.toLong()   // absolute end of the whole request, if known
    private var chunkRemaining = 0L
    private var upstreamOpen = false

    override fun addTransferListener(transferListener: TransferListener) = upstream.addTransferListener(transferListener)

    override fun open(dataSpec: DataSpec): Long {
        spec = dataSpec
        position = dataSpec.position
        endExclusive = if (dataSpec.length == C.LENGTH_UNSET.toLong()) C.LENGTH_UNSET.toLong() else dataSpec.position + dataSpec.length
        openChunk()
        // Report the remaining length of the whole resource so the extractor can seek.
        return if (endExclusive != C.LENGTH_UNSET.toLong()) endExclusive - position else C.LENGTH_UNSET.toLong()
    }

    private fun openChunk() {
        val s = spec!!
        var len = chunkBytes
        if (endExclusive != C.LENGTH_UNSET.toLong()) len = minOf(len, endExclusive - position)
        val chunkSpec = s.buildUpon().setPosition(position).setLength(len).build()
        val opened = upstream.open(chunkSpec)
        upstreamOpen = true
        chunkRemaining = if (opened == C.LENGTH_UNSET.toLong()) len else opened
        if (endExclusive == C.LENGTH_UNSET.toLong()) {
            // Total size from "Content-Range: bytes a-b/total"; the server clamps the last chunk.
            val cr = upstream.responseHeaders.entries.firstOrNull { it.key.equals("Content-Range", true) }?.value?.firstOrNull()
            val total = cr?.substringAfter('/', "")?.toLongOrNull()
            if (total != null) endExclusive = total
        }
    }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        if (length == 0) return 0
        while (true) {
            if (chunkRemaining <= 0) {
                val done = endExclusive != C.LENGTH_UNSET.toLong() && position >= endExclusive
                if (done) return C.RESULT_END_OF_INPUT
                upstream.close(); upstreamOpen = false
                openChunk()
            }
            val n = upstream.read(buffer, offset, minOf(length.toLong(), chunkRemaining).toInt())
            if (n == C.RESULT_END_OF_INPUT) {
                // Server ended the chunk early: if the total is unknown this is the end of the resource.
                if (endExclusive == C.LENGTH_UNSET.toLong() || position >= endExclusive) return C.RESULT_END_OF_INPUT
                chunkRemaining = 0
                continue
            }
            position += n
            chunkRemaining -= n
            return n
        }
    }

    override fun getUri(): Uri? = upstream.uri

    override fun getResponseHeaders(): Map<String, List<String>> = upstream.responseHeaders

    override fun close() {
        if (upstreamOpen) upstream.close()
        upstreamOpen = false
        spec = null
    }
}
