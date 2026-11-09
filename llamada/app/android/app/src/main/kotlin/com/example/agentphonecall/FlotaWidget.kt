package com.example.agentphonecall

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Color
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.view.View
import android.widget.RemoteViews
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

/** Widget «Flota»: cuotas de Claude Code, Codex y Grok y trabajo pendiente, leídos de GET /flota del servidor. */
class FlotaWidget : AppWidgetProvider() {

    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) = refrescar(context)

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == ACCION_REFRESCAR) refrescar(context) else super.onReceive(context, intent)
    }

    private fun refrescar(context: Context) {
        val manager = AppWidgetManager.getInstance(context)
        val ids = manager.getAppWidgetIds(ComponentName(context, FlotaWidget::class.java))
        if (ids.isEmpty()) return
        pintar(context, manager, ids, "Actualizando…", null)
        Thread {
            val datos = try { leer(context) } catch (e: Exception) { null }
            pintar(context, manager, ids, if (datos == null) "sin conexión" else null, datos)
        }.start()
    }

    private fun leer(context: Context): JSONObject? {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val base = prefs.getString("url", null) ?: return null
        val token = prefs.getString("token", null) ?: return null
        val conexion = URL("$base/flota").openConnection() as HttpURLConnection
        conexion.setRequestProperty("Authorization", "Bearer $token")
        conexion.connectTimeout = 8000
        conexion.readTimeout = 8000
        return try { JSONObject(conexion.inputStream.bufferedReader().readText()) } finally { conexion.disconnect() }
    }

    private fun pintar(context: Context, manager: AppWidgetManager, ids: IntArray, aviso: String?, d: JSONObject?) {
        val v = RemoteViews(context.packageName, R.layout.widget_flota)
        val toque = PendingIntent.getBroadcast(
            context, 0, Intent(context, FlotaWidget::class.java).setAction(ACCION_REFRESCAR),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        v.setOnClickPendingIntent(R.id.raiz, toque)
        if (d != null) {
            val cuotas = d.optJSONArray("cuotas")
            val filas = listOf(
                intArrayOf(R.id.q1_nombre, R.id.q1_barra, R.id.q1_pct, R.id.q1_reinicia),
                intArrayOf(R.id.q2_nombre, R.id.q2_barra, R.id.q2_pct, R.id.q2_reinicia),
                intArrayOf(R.id.q3_nombre, R.id.q3_barra, R.id.q3_pct, R.id.q3_reinicia))
            for ((i, f) in filas.withIndex()) {
                val q = cuotas?.optJSONObject(i)
                v.setTextViewText(f[0], (q?.optString("nombre") ?: "").uppercase(Locale.getDefault()))
                if (q != null && q.optString("estado") == "ok") {
                    val r = q.optInt("restante")
                    v.setProgressBar(f[1], 100, r, false)
                    v.setTextViewText(f[2], "$r%")
                    v.setTextViewText(f[3], renueva(q.optString("reinicia")))
                    // Cuota baja: la barra y el número pasan a ámbar o rojo.
                    val alerta = if (r < 15) Color.parseColor("#F0675A") else if (r < 35) Color.parseColor("#F2B84B") else null
                    if (alerta != null) {
                        v.setTextColor(f[2], alerta)
                        v.setColorStateList(f[1], "setProgressTintList", ColorStateList.valueOf(alerta))
                    }
                } else {
                    v.setProgressBar(f[1], 100, 0, false)
                    v.setTextViewText(f[2], "—")
                    v.setTextColor(f[2], Color.parseColor("#6F8396"))
                    v.setTextViewText(f[3], "sin lectura")
                }
            }
            val fl = d.optJSONObject("flota")
            val espera = fl?.optInt("espera") ?: 0
            v.setTextViewText(R.id.c_marcha_n, (fl?.optInt("marcha") ?: 0).toString())
            v.setTextViewText(R.id.c_decision_n, espera.toString())
            v.setTextViewText(R.id.c_cola_n, (fl?.optInt("cola") ?: 0).toString())
            if (espera > 0) {
                v.setInt(R.id.c_decision, "setBackgroundResource", R.drawable.chip_alerta)
                v.setTextColor(R.id.c_decision_n, Color.parseColor("#F2B84B"))
            }
            v.setTextViewText(R.id.hora, hora(d.optString("actualizado")))
        } else if (aviso != null) {
            v.setTextViewText(R.id.hora, aviso)
        }
        pintarTareasPrincipal(context, v)
        manager.updateAppWidget(ids, v)
    }

    /**
     * Con una instantánea del firstmate principal (la que llega por FCM) los contadores y la lista salen de ella, no del backlog de esta Mac;
     * si pasó de 2 horas se dice en la nota. Sin instantánea no cambia nada.
     */
    private fun pintarTareasPrincipal(context: Context, v: RemoteViews) {
        val t = TareasPrincipal.leer(context)
        if (t == null) {
            v.setViewVisibility(R.id.tareas_nota, View.GONE)
            for (id in LINEAS) v.setViewVisibility(id, View.GONE)
            return
        }
        v.setTextViewText(R.id.c_marcha_n, t.marcha.size.toString())
        v.setTextViewText(R.id.c_decision_n, t.espera.size.toString())
        v.setTextViewText(R.id.c_cola_n, t.cola.size.toString())
        val alerta = Color.parseColor("#F2B84B")
        v.setTextColor(R.id.c_decision_n, if (t.espera.isNotEmpty()) alerta else Color.parseColor("#E8EEF3"))
        v.setInt(R.id.c_decision, "setBackgroundResource", if (t.espera.isNotEmpty()) R.drawable.chip_alerta else R.drawable.chip_fondo)
        val caducada = t.caducada()
        v.setViewVisibility(R.id.tareas_nota, View.VISIBLE)
        v.setTextViewText(R.id.tareas_nota, if (caducada) "Principal · ${t.etiqueta()} · puede no estar al día" else "Principal · ${t.etiqueta()}")
        v.setTextColor(R.id.tareas_nota, if (caducada) alerta else Color.parseColor("#6F8396"))
        // Una línea por TextView: cada una con su elipsis, sin partir ni salirse del widget.
        val lineas = t.lineasFlota()
        for ((i, id) in LINEAS.withIndex()) {
            val l = lineas.getOrNull(i)
            v.setViewVisibility(id, if (l == null) View.GONE else View.VISIBLE)
            if (l == null) continue
            val texto = SpannableStringBuilder()
            if (l.marca.isNotEmpty()) {
                texto.append(l.marca).append(" ")
                val color = if (l.marca == "▶") "#22D3EE" else "#6F8396"
                texto.setSpan(ForegroundColorSpan(Color.parseColor(color)), 0, l.marca.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            }
            texto.append(l.texto)
            v.setTextViewText(id, texto)
            v.setTextColor(id, Color.parseColor(if (caducada || l.marca.isEmpty()) "#6F8396" else "#E8EEF3"))
        }
    }

    /** «renueva en 5 d» / «en 7 h» a partir de la fecha ISO de renovación de la cuota. */
    private fun renueva(iso: String): String = try {
        val f = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }
        val horas = (f.parse(iso.take(19))!!.time - System.currentTimeMillis()) / 3600000
        if (horas >= 48) "renueva en ${horas / 24} d" else if (horas >= 1) "renueva en $horas h" else "renueva pronto"
    } catch (e: Exception) { "" }

    private fun hora(iso: String): String = try {
        val f = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }
        SimpleDateFormat("HH:mm", Locale.getDefault()).format(f.parse(iso)!!)
    } catch (e: Exception) { "—" }

    companion object {
        const val PREFS = "llamada_widget"
        const val ACCION_REFRESCAR = "com.example.agentphonecall.FLOTA_REFRESCAR"
        private val LINEAS = listOf(R.id.tareas_l1, R.id.tareas_l2, R.id.tareas_l3)
    }
}
