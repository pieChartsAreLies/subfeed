package com.gerstl.subfeed

import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class AuthStatusTest {
    private fun s(json: String) = AuthStatus.parse(SubfeedJson.parseToJsonElement(json).jsonObject)

    @Test
    fun waits_while_signing_in_even_with_an_old_token() {
        val st = s("""{"signed_in":true,"signing_in":true,"account":"Old","error":null,"import_summary":null}""")
        assertEquals(AuthStatus.Code.Waiting, AuthStatus.code(st))
        assertEquals("Waiting for you to finish signing in on your phone.", AuthStatus.line(st))
    }

    @Test
    fun failure_closes_the_card_with_the_sentence() {
        val st = s("""{"signed_in":false,"signing_in":false,"account":null,"error":"The sign-in code expired. Choose Sign in to get a new one.","import_summary":null}""")
        assertEquals(AuthStatus.Code.Failed("The sign-in code expired. Choose Sign in to get a new one."), AuthStatus.code(st))
    }

    @Test
    fun done_when_signed_in_without_error() {
        val st = s("""{"signed_in":true,"signing_in":false,"account":"Alex","error":null,"import_summary":{"added":12,"removed":3,"kept":17}}""")
        assertEquals(AuthStatus.Code.Done, AuthStatus.code(st))
        assertEquals("Signed in to Google as Alex.", AuthStatus.line(st))
        assertEquals("From Google: 12 channels added, 3 removed, 17 kept.", st.summary)
    }

    @Test
    fun not_set_up_and_no_summary() {
        val st = s("""{"signed_in":false,"account":null,"error":"Google sign-in isn't set up yet.","import_summary":null}""")
        assertEquals("Google sign-in isn't set up yet.", AuthStatus.line(st))
        assertNull(st.summary)
        assertEquals(AuthStatus.Code.Failed("Google sign-in isn't set up yet."), AuthStatus.code(st))
    }

    @Test
    fun ended_without_token_or_error_still_says_something() {
        val st = s("""{"signed_in":false,"signing_in":false,"error":null}""")
        assertEquals(AuthStatus.Code.Failed("Sign-in didn't finish. Choose Sign in to try again."), AuthStatus.code(st))
    }

    @Test
    fun one_channel_is_singular() {
        assertEquals("From Google: 1 channel added, 0 removed, 5 kept.",
            AuthStatus.summary(SubfeedJson.parseToJsonElement("""{"added":1,"removed":0,"kept":5}""")))
    }
}
