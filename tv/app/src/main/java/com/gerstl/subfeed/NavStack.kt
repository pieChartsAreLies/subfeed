package com.gerstl.subfeed

/** Back stack of screens. Pure: the UI mirrors changes into Compose state itself. */
class NavStack<T>(root: T) {
    private val items = mutableListOf(root)

    val top: T get() = items.last()

    fun push(s: T) { items.add(s) }

    /** Removes the top screen; false (and nothing removed) when only the root is left. */
    fun pop(): Boolean {
        if (items.size <= 1) return false
        items.removeAt(items.lastIndex)
        return true
    }

    fun replaceTop(s: T) { items[items.lastIndex] = s }
}
