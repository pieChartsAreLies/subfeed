package com.gerstl.subfeed

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ChannelListTest {
    private fun ch(
        name: String, cat: Int? = null, hidden: Boolean = false, watched: String? = null,
    ) = ChannelInfo(id = "id-$name", name = name, categoryId = cat, hidden = hidden, lastWatchedAt = watched)

    private fun names(l: List<ChannelInfo>) = l.map { it.name }

    @Test
    fun letter_strips_the_and_maps_non_ascii_to_hash() {
        assertEquals('V', ChannelList.letter("The Verge"))
        assertEquals('V', ChannelList.letter("the verge"))
        assertEquals('#', ChannelList.letter("3Blue1Brown"))
        assertEquals('#', ChannelList.letter("Été"))
        assertEquals('A', ChannelList.letter("Adam"))
        assertEquals('T', ChannelList.letter("Theory"))
        assertEquals('T', ChannelList.letter("The"))
        assertEquals('#', ChannelList.letter(""))
    }

    @Test
    fun sortKey_strips_the_and_ignores_case() {
        assertEquals("verge", ChannelList.sortKey("The Verge"))
        assertEquals("verge", ChannelList.sortKey("Verge"))
        assertEquals("theory", ChannelList.sortKey("Theory"))
    }

    @Test
    fun az_sort_uses_stripped_names() {
        val all = listOf(ch("The Verge"), ch("Zed"), ch("Apple"), ch("the Basics"))
        assertEquals(listOf("Apple", "the Basics", "The Verge", "Zed"), names(ChannelList.apply(all, ChannelList.Filter(null, null), ChannelList.Sort.AZ)))
    }

    @Test
    fun category_and_letter_filters_intersect() {
        val all = listOf(ch("Alpha", 1), ch("Axe", 2), ch("Beta", 1), ch("The Apex", 1))
        val r = ChannelList.apply(all, ChannelList.Filter("cat-1", 'A'), ChannelList.Sort.AZ)
        assertEquals(listOf("Alpha", "The Apex"), names(r))
        val r2 = ChannelList.apply(all, ChannelList.Filter(null, 'A'), ChannelList.Sort.AZ)
        assertEquals(listOf("Alpha", "The Apex", "Axe"), names(r2))
    }

    @Test
    fun other_is_null_or_unapproved_category() {
        val all = listOf(ch("A", null), ch("B", 1), ch("C", 99))
        val r = ChannelList.apply(all, ChannelList.Filter("other", null), ChannelList.Sort.AZ, approved = setOf(1))
        assertEquals(listOf("A", "C"), names(r))
        val r2 = ChannelList.apply(all, ChannelList.Filter("other", null), ChannelList.Sort.AZ)
        assertEquals(listOf("A"), names(r2))
    }

    @Test
    fun hidden_filter_restricts_but_all_and_categories_include_hidden() {
        val all = listOf(ch("A", 1, hidden = true), ch("B", 1), ch("C", null, hidden = true))
        assertEquals(listOf("A", "C"), names(ChannelList.apply(all, ChannelList.Filter("hidden", null), ChannelList.Sort.AZ)))
        assertEquals(listOf("A", "B", "C"), names(ChannelList.apply(all, ChannelList.Filter(null, null), ChannelList.Sort.AZ)))
        assertEquals(listOf("A", "B"), names(ChannelList.apply(all, ChannelList.Filter("cat-1", null), ChannelList.Sort.AZ)))
        assertEquals(listOf("C"), names(ChannelList.apply(all, ChannelList.Filter("other", null), ChannelList.Sort.AZ)))
    }

    @Test
    fun least_recent_puts_never_watched_first_then_oldest() {
        val all = listOf(
            ch("Newest", watched = "2026-10-20T00:00:00Z"),
            ch("Zeta"),
            ch("Oldest", watched = "2026-01-01T00:00:00Z"),
            ch("Alpha"),
            ch("Middle", watched = "2026-05-05T00:00:00Z"),
        )
        val r = ChannelList.apply(all, ChannelList.Filter(null, null), ChannelList.Sort.LEAST_RECENT)
        assertEquals(listOf("Alpha", "Zeta", "Oldest", "Middle", "Newest"), names(r))
    }

    @Test
    fun clear_filters_shows_only_when_empty_and_filtered() {
        val none = emptyList<ChannelInfo>()
        assertTrue(ChannelList.showClearFilters(none, ChannelList.Filter("cat-1", null)))
        assertTrue(ChannelList.showClearFilters(none, ChannelList.Filter(null, 'Q')))
        assertFalse(ChannelList.showClearFilters(none, ChannelList.Filter(null, null)))
        assertFalse(ChannelList.showClearFilters(listOf(ch("A")), ChannelList.Filter("cat-1", null)))
    }

    @Test
    fun firstIndexWithLetter_finds_first_or_minus_one() {
        val l = listOf(ch("Apple"), ch("The Basics"), ch("Bee"))
        assertEquals(1, ChannelList.firstIndexWithLetter(l, 'B'))
        assertEquals(-1, ChannelList.firstIndexWithLetter(l, 'Z'))
    }

    @Test
    fun focusAfter_keeps_focus_or_takes_the_row_at_the_same_position() {
        val old = listOf("a", "b", "c", "d")
        assertEquals("b", ChannelList.focusAfter(old, listOf("a", "b", "d"), "b"))
        assertEquals("c", ChannelList.focusAfter(old, listOf("a", "c", "d"), "b"))   // index 1 now holds c
        assertEquals("c", ChannelList.focusAfter(old, listOf("a", "b", "c"), "d"))   // clamped to the last row
        assertEquals(null, ChannelList.focusAfter(old, emptyList(), "b"))
        assertEquals(null, ChannelList.focusAfter(old, listOf("a"), null))
    }
}
