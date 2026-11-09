package com.example.agentphonecall

import android.content.Context
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Estado local del widget Decisiones: qué decisiones ya se respondieron («Respondido: <opción>») y cuáles fallaron al
 * enviar. Una respuesta se guarda solo tras un 200 del servidor. Se descarta cuando llega una instantánea nueva que ya
 * no trae la decisión.
 */
object DecisionesEstado {
    private const val PREFS = "decisiones_estado"

    class Respondida(val opcion: String, val generado: Long)

    private fun leer(context: Context, clave: String): JSONObject = try {
        JSONObject(context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(clave, null) ?: "{}")
    } catch (e: Exception) { JSONObject() }

    private fun guardar(context: Context, clave: String, j: JSONObject) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(clave, j.toString()).apply()
    }

    @Synchronized fun marcarRespondida(context: Context, id: String, opcion: String, generado: Long) {
        guardar(context, "respondidas", leer(context, "respondidas").put(id, JSONObject().put("opcion", opcion).put("generado", generado)))
        guardar(context, "errores", leer(context, "errores").also { it.remove(id) })
    }

    @Synchronized fun marcarError(context: Context, id: String, mensaje: String) {
        guardar(context, "errores", leer(context, "errores").put(id, mensaje))
    }

    /** «Llámame» enviado (200): se muestra «Te llamarán» y se limpia un error previo. */
    @Synchronized fun marcarLlamame(context: Context, id: String) {
        guardar(context, "llamames", leer(context, "llamames").put(id, true))
        guardar(context, "errores", leer(context, "errores").also { it.remove(id) })
    }

    @Synchronized fun llamame(context: Context, id: String): Boolean = leer(context, "llamames").optBoolean(id, false)

    @Synchronized fun respondida(context: Context, id: String): Respondida? =
        leer(context, "respondidas").optJSONObject(id)?.let { Respondida(it.optString("opcion"), it.optLong("generado")) }

    @Synchronized fun error(context: Context, id: String): String? = leer(context, "errores").optString(id, "").ifEmpty { null }

    /** Todo el estado para la sección Decisiones de la app: {respondidas: {id: opción}, errores: {id: motivo}, llamames: [id]}. */
    @Synchronized fun todo(context: Context): JSONObject {
        val respondidas = JSONObject()
        val r = leer(context, "respondidas")
        for (id in r.keys()) respondidas.put(id, r.optJSONObject(id)?.optString("opcion") ?: "")
        val llamames = org.json.JSONArray()
        val l = leer(context, "llamames")
        for (id in l.keys()) if (l.optBoolean(id, false)) llamames.put(id)
        return JSONObject().put("respondidas", respondidas).put("errores", leer(context, "errores")).put("llamames", llamames)
    }

    /** Olvida lo respondido o fallido de decisiones que la instantánea [actual] ya no trae (y que no es la del momento de responder). */
    @Synchronized fun depurar(context: Context, actual: TareasPrincipal) {
        val ids = actual.decisiones.mapNotNull { it.id }.toSet()
        val resp = leer(context, "respondidas")
        for (id in resp.keys().asSequence().toList()) {
            val g = resp.optJSONObject(id)?.optLong("generado") ?: 0L
            if (id !in ids && g != actual.generado) resp.remove(id)
        }
        guardar(context, "respondidas", resp)
        for (clave in listOf("errores", "llamames")) {
            val j = leer(context, clave)
            for (id in j.keys().asSequence().toList()) if (id !in ids) j.remove(id)
            guardar(context, clave, j)
        }
    }
}

/** Envío al servidor de la Mac: `POST /hablar` al firstmate con el token del teléfono. Solo un 200 cuenta como enviado. */
object DecisionEnvio {
    fun textoRespuesta(id: String, opcion: String, titulo: String) =
        "[widget-decision] id=$id respuesta=$opcion | El capitán responde «$opcion» a la decisión: $titulo"

    fun textoLlamame(id: String, titulo: String) =
        "[widget-llamame] id=$id | El capitán pide que lo llames para explicarle la decisión: $titulo"

    /** Responde una decisión y anota el resultado («Respondido» tras un 200, error si no). Null si se envió. */
    fun responder(context: Context, id: String, titulo: String, opcion: String): String? {
        // La instantánea se lee antes de enviar: «respondido» vale mientras no llegue otra distinta.
        val generado = TareasPrincipal.leer(context)?.generado ?: 0L
        val fallo = enviar(context, textoRespuesta(id, opcion, titulo))
        if (fallo == null) DecisionesEstado.marcarRespondida(context, id, opcion, generado) else DecisionesEstado.marcarError(context, id, fallo)
        DecisionesWidget.refrescar(context)
        return fallo
    }

    /** «Llámame»: sin confirmación; solo cuenta como enviado con un 200. Null si se envió. */
    fun llamame(context: Context, id: String, titulo: String): String? {
        val fallo = enviar(context, textoLlamame(id, titulo))
        if (fallo == null) DecisionesEstado.marcarLlamame(context, id) else DecisionesEstado.marcarError(context, id, fallo)
        DecisionesWidget.refrescar(context)
        return fallo
    }

    /** Null si se envió (HTTP 200); si no, el motivo en una frase corta. */
    fun enviar(context: Context, texto: String): String? {
        val prefs = context.getSharedPreferences(FlotaWidget.PREFS, Context.MODE_PRIVATE)
        val base = prefs.getString("url", null); val token = prefs.getString("token", null)
        if (base == null || token == null) return "Abre la app Llamada una vez para configurarla."
        val c = try { URL("$base/hablar").openConnection() as HttpURLConnection } catch (e: Exception) { return "Dirección del servidor inválida." }
        return try {
            c.requestMethod = "POST"; c.connectTimeout = 8000; c.readTimeout = 30000; c.doOutput = true
            c.setRequestProperty("Authorization", "Bearer $token"); c.setRequestProperty("Content-Type", "application/json")
            c.outputStream.use { it.write(JSONObject().put("quien", "firstmate").put("texto", texto).toString().toByteArray()) }
            when (val codigo = c.responseCode) {
                200 -> null
                401 -> "El Mac rechazó el acceso."
                else -> "El servidor respondió $codigo."
            }
        } catch (e: Exception) { "No hay conexión con tu Mac." } finally { c.disconnect() }
    }
}
