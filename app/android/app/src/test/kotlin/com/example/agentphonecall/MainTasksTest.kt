package com.example.agentphonecall

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class MainTasksTest {
    private fun waiting(vararg e: String) =
        MainTasks.parse("""{"generated":"2026-10-21T11:30:00.000Z","waiting":[${e.joinToString(",")}]}""").decisions

    @Test fun withADetailTheFullTextIsTheDetail() {
        val d = waiting("""{"id":"d1","title":"Merge the branch…","detail":"  Merge the branch feature/x into main now  ","options":["Merge"]}""").single()
        assertEquals("Merge the branch…", d.title)
        assertEquals("Merge the branch feature/x into main now", d.detail)
        assertEquals("Merge the branch feature/x into main now", d.text)
        assertEquals(listOf("Merge"), d.options)
    }

    @Test fun withoutADetailTheFullTextIsTheTitle() {
        val ds = waiting(""""Text only"""", """{"id":"d1","title":"Ship"}""", """{"id":"d2","title":"Delete","detail":"  "}""", """{"id":"d3","title":"Move","detail":5}""")
        assertEquals(listOf("Text only", "Ship", "Delete", "Move"), ds.map { it.text })
        ds.forEach { assertNull(it.detail) }
    }

    @Test fun withoutAnIdTheDetailIsKept() {
        val d = waiting("""{"title":"Notice","detail":"A long notice"}""").single()
        assertNull(d.id)
        assertEquals("A long notice", d.text)
    }

    @Test fun theTextsSentCarryTheIdAndTheFullText() {
        assertEquals(
            "[widget-callme] id=d1 | The user asks you to call them to explain the decision: Merge the branch feature/x into main now",
            DecisionSender.callMeText("d1", "Merge the branch feature/x into main now"))
        assertEquals(
            "[widget-decision] id=d1 answer=Yes | The user answers «Yes» to the decision: Merge the branch feature/x into main now",
            DecisionSender.answerText("d1", "Yes", "Merge the branch feature/x into main now"))
    }

    private fun main(running: List<String>, waiting: List<String>, queued: List<String>) =
        MainTasks(0L, running, waiting.map { MainTasks.Decision("x", it, listOf("Yes")) }, queued)

    @Test fun fleetShowsRunningAndQueuedButNotTheDecisions() {
        val l = main(listOf("m1", "m2", "m3"), listOf("d1"), listOf("c1", "c2")).fleetLines()
        assertEquals(listOf("▶" to "m1", "▶" to "m2", "·" to "c1"), l.map { it.mark to it.text })
    }

    @Test fun fleetWithNothingRunningSaysSo() {
        val l = main(emptyList(), listOf("d1", "d2"), listOf("c1")).fleetLines()
        assertEquals(listOf("" to "Nothing running", "·" to "c1"), l.map { it.mark to it.text })
        assertEquals(listOf("" to "Nothing running"), main(emptyList(), emptyList(), emptyList()).fleetLines().map { it.mark to it.text })
    }
}
