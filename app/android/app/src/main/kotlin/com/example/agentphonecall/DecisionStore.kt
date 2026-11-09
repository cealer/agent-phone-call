package com.example.agentphonecall

import android.content.Context
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Local state of the Decisions widget: which decisions were already answered («Answered: <option>») and which failed to
 * send. An answer is stored only after a 200 from the server. It is dropped once a new snapshot arrives that no longer
 * carries the decision.
 */
object DecisionStore {
    private const val PREFS = "decision_state"

    class Answered(val option: String, val generated: Long)

    private fun read(context: Context, key: String): JSONObject = try {
        JSONObject(context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(key, null) ?: "{}")
    } catch (e: Exception) { JSONObject() }

    private fun save(context: Context, key: String, j: JSONObject) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(key, j.toString()).apply()
    }

    @Synchronized fun markAnswered(context: Context, id: String, option: String, generated: Long) {
        save(context, "answered", read(context, "answered").put(id, JSONObject().put("option", option).put("generated", generated)))
        save(context, "errors", read(context, "errors").also { it.remove(id) })
    }

    @Synchronized fun markError(context: Context, id: String, message: String) {
        save(context, "errors", read(context, "errors").put(id, message))
    }

    /** «Call me» sent (200): «you will get a call» shows and an earlier error is cleared. */
    @Synchronized fun markCallMe(context: Context, id: String) {
        save(context, "callMes", read(context, "callMes").put(id, true))
        save(context, "errors", read(context, "errors").also { it.remove(id) })
    }

    @Synchronized fun callMe(context: Context, id: String): Boolean = read(context, "callMes").optBoolean(id, false)

    @Synchronized fun answered(context: Context, id: String): Answered? =
        read(context, "answered").optJSONObject(id)?.let { Answered(it.optString("option"), it.optLong("generated")) }

    @Synchronized fun error(context: Context, id: String): String? = read(context, "errors").optString(id, "").ifEmpty { null }

    /** The whole state for the app's Decisions section: {answered: {id: option}, errors: {id: reason}, callMes: [id]}. */
    @Synchronized fun all(context: Context): JSONObject {
        val answered = JSONObject()
        val a = read(context, "answered")
        for (id in a.keys()) answered.put(id, a.optJSONObject(id)?.optString("option") ?: "")
        val callMes = org.json.JSONArray()
        val c = read(context, "callMes")
        for (id in c.keys()) if (c.optBoolean(id, false)) callMes.put(id)
        return JSONObject().put("answered", answered).put("errors", read(context, "errors")).put("callMes", callMes)
    }

    /** Forgets what was answered or failed for decisions the [current] snapshot no longer carries (and that is not the one answered against). */
    @Synchronized fun prune(context: Context, current: MainTasks) {
        val ids = current.decisions.mapNotNull { it.id }.toSet()
        val answered = read(context, "answered")
        for (id in answered.keys().asSequence().toList()) {
            val g = answered.optJSONObject(id)?.optLong("generated") ?: 0L
            if (id !in ids && g != current.generated) answered.remove(id)
        }
        save(context, "answered", answered)
        for (key in listOf("errors", "callMes")) {
            val j = read(context, key)
            for (id in j.keys().asSequence().toList()) if (id !in ids) j.remove(id)
            save(context, key, j)
        }
    }
}

/** Sending to the Mac's server: `POST /talk` to the coordinator, with the phone token. Only a 200 counts as sent. */
object DecisionSender {
    fun answerText(id: String, option: String, title: String) =
        "[widget-decision] id=$id answer=$option | The user answers «$option» to the decision: $title"

    fun callMeText(id: String, title: String) =
        "[widget-callme] id=$id | The user asks you to call them to explain the decision: $title"

    /** Answers a decision and records the result («Answered» after a 200, an error otherwise). Null if it was sent. */
    fun answer(context: Context, id: String, title: String, option: String): String? {
        // The snapshot is read before sending: «answered» holds until a different one arrives.
        val generated = MainTasks.read(context)?.generated ?: 0L
        val failure = send(context, answerText(id, option, title))
        if (failure == null) DecisionStore.markAnswered(context, id, option, generated) else DecisionStore.markError(context, id, failure)
        DecisionsWidget.refresh(context)
        return failure
    }

    /** «Call me»: no confirmation; it only counts as sent with a 200. Null if it was sent. */
    fun callMe(context: Context, id: String, title: String): String? {
        val failure = send(context, callMeText(id, title))
        if (failure == null) DecisionStore.markCallMe(context, id) else DecisionStore.markError(context, id, failure)
        DecisionsWidget.refresh(context)
        return failure
    }

    /** Null if it was sent (HTTP 200); otherwise the reason, in one short sentence. */
    fun send(context: Context, text: String): String? {
        val prefs = context.getSharedPreferences(FleetWidget.PREFS, Context.MODE_PRIVATE)
        val base = prefs.getString("url", null); val token = prefs.getString("token", null)
        if (base == null || token == null) return "Open the Agent Call app once to set it up."
        val c = try { URL("$base/talk").openConnection() as HttpURLConnection } catch (e: Exception) { return "Invalid server address." }
        return try {
            c.requestMethod = "POST"; c.connectTimeout = 8000; c.readTimeout = 30000; c.doOutput = true
            c.setRequestProperty("Authorization", "Bearer $token"); c.setRequestProperty("Content-Type", "application/json")
            c.outputStream.use { it.write(JSONObject().put("to", "firstmate").put("text", text).toString().toByteArray()) }
            when (val code = c.responseCode) {
                200 -> null
                401 -> "The Mac refused access."
                else -> "The server answered $code."
            }
        } catch (e: Exception) { "No connection to your Mac." } finally { c.disconnect() }
    }
}
