package com.gerstl.subfeed

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ChannelsStateTest {
    @Test
    fun state_survives_between_screen_instances_and_setFilter_resets_focus() {
        val st = ChannelsState()
        st.sort = ChannelList.Sort.LEAST_RECENT
        st.focusId = "x"
        st.applyFilter(ChannelList.Filter("hidden", 'B'))
        assertNull(st.focusId)
        st.focusId = "y"
        // a second composition of the screen reads the same holder
        assertEquals(ChannelList.Filter("hidden", 'B'), st.filter)
        assertEquals(ChannelList.Sort.LEAST_RECENT, st.sort)
        assertEquals("y", st.focusId)
    }
}
