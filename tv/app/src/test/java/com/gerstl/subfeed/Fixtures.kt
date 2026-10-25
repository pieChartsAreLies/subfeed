package com.gerstl.subfeed

/** Small feed builder for tests. `spec` = list of "id:channel[:w|:p]" (w = watched, p = in progress). */
fun video(spec: String): Video {
    val parts = spec.split(":")
    val flag = parts.getOrNull(2)
    return Video(
        id = parts[0],
        channelId = parts[1],
        channelTitle = "Channel ${parts[1]}",
        title = "Title ${parts[0]}",
        durationS = 600.0,
        positionS = if (flag == "p") 120.0 else 0.0,
        watched = flag == "w",
    )
}

fun tab(id: String, vararg specs: String): Pair<Tab, List<Video>> {
    val vids = specs.map { video(it) }
    val cont = vids.filter { it.positionS > 0 && !it.watched }.map { it.id }
    return Tab(id = id, name = id.uppercase(), videoIds = vids.map { it.id }, continueIds = cont) to vids
}

fun feedOf(vararg tabs: Pair<Tab, List<Video>>): Feed =
    Feed(tabs = tabs.map { it.first }, videos = tabs.flatMap { it.second }.associateBy { it.id })
