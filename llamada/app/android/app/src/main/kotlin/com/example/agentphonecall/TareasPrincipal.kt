package com.example.agentphonecall

import android.content.Context
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Última instantánea de tareas del firstmate principal (VPS) que dejó la app al recibir el mensaje FCM `tareas`.
 * La guarda Dart con home_widget en la clave `tareas` de HomeWidgetPreferences.
 */
class TareasPrincipal(
    val generado: Long,
    val marcha: List<String>,
    val decisiones: List<Decision>,
    val cola: List<String>,
) {
    /** Títulos de lo que espera al capitán (los usa también el widget Flota). */
    val espera: List<String> get() = decisiones.map { it.titulo }

    /** Una línea de la sección Principal del widget Flota: [marca] «▶» (marcha), «·» (cola) o vacía. */
    class Linea(val marca: String, val texto: String)

    /**
     * Lo que el widget Flota muestra del principal: hasta 2 en marcha (o «Sin trabajo en marcha») y la siguiente de cola.
     * Las decisiones no: tienen su propio widget y Flota solo las cuenta.
     */
    fun lineasFlota(): List<Linea> =
        marcha.take(2).map { Linea("▶", it) }.ifEmpty { listOf(Linea("", "Sin trabajo en marcha")) } +
            cola.take(1).map { Linea("·", it) }

    fun caducada(ahora: Long = System.currentTimeMillis()) = ahora - generado > CADUCA_MS

    /** «11:30», o «11:30 · hace 3 h» cuando ya no es actual. */
    fun etiqueta(ahora: Long = System.currentTimeMillis()): String {
        val hora = SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(generado))
        if (!caducada(ahora)) return hora
        val horas = (ahora - generado) / 3600000
        return if (horas >= 48) "$hora · hace ${horas / 24} d" else "$hora · hace $horas h"
    }

    /**
     * Un elemento de `espera`: con [id] admite respuesta; sin él solo se muestra. Igual que `Decision` de lib/tareas.dart.
     * [detalle] es el texto completo opcional (el emisor puede acortar [titulo]); [texto] es lo que se muestra entero y se envía.
     */
    class Decision(val id: String?, val titulo: String, val opciones: List<String>, val detalle: String? = null) {
        val texto: String get() = detalle ?: titulo
    }

    companion object {
        private const val CADUCA_MS = 2 * 3600000L

        fun leer(context: Context): TareasPrincipal? = try {
            val texto = context.getSharedPreferences("HomeWidgetPreferences", Context.MODE_PRIVATE)
                .getString("tareas", null)
            if (texto == null) null else interpretar(texto)
        } catch (e: Exception) { null }

        fun interpretar(texto: String): TareasPrincipal {
            val j = JSONObject(texto)
            val f = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }
            return TareasPrincipal(
                generado = f.parse(j.getString("generado").take(19))!!.time,
                marcha = lista(j, "marcha"), decisiones = decisiones(j), cola = lista(j, "cola"))
        }

        private fun lista(j: JSONObject, clave: String): List<String> {
            val a = j.optJSONArray(clave) ?: return emptyList()
            return (0 until a.length()).map { a.optString(it) }.filter { it.isNotBlank() }
        }

        private fun decisiones(j: JSONObject): List<Decision> {
            val a = j.optJSONArray("espera") ?: return emptyList()
            return (0 until a.length()).mapNotNull { i ->
                when (val e = a.opt(i)) {
                    is String -> e.trim().takeIf { it.isNotEmpty() }?.let { Decision(null, it, emptyList()) }
                    is JSONObject -> {
                        val titulo = e.optString("titulo").trim()
                        val id = e.optString("id").trim()
                        val detalle = (e.opt("detalle") as? String)?.trim()?.takeIf { it.isNotEmpty() }
                        if (titulo.isEmpty()) null
                        else if (id.isEmpty()) Decision(null, titulo, emptyList(), detalle)
                        else {
                            val o = e.optJSONArray("opciones")
                            val opciones = if (o == null) emptyList() else (0 until o.length()).mapNotNull { (o.opt(it) as? String)?.trim()?.takeIf { t -> t.isNotEmpty() } }
                            Decision(id, titulo, opciones.take(3).ifEmpty { listOf("Sí", "No") }, detalle)
                        }
                    }
                    else -> null
                }
            }
        }
    }
}
