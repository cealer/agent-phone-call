package com.example.agentphonecall

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/**
 * Small screen of the «Hablar» (talk) widget: records what the user says, transcribes it on the Mac (Whisper) and
 * sends it to whoever the button chose: «firstmate», «hermes» (optional, hermes-agent) or «nota» (the firstmate
 * inbox). The answer is spoken in the configured voice and shown on screen.
 */
class HablarActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var titulo: TextView
    private lateinit var cuerpo: TextView
    private lateinit var nivel: TextView
    @Volatile private var parar = false
    @Volatile private var cerrado = false
    private var quien = "hermes"

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        quien = intent.getStringExtra("quien") ?: "hermes"
        val caja = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(22), dp(20), dp(22), dp(18))
            background = GradientDrawable(GradientDrawable.Orientation.TL_BR, intArrayOf(Color.parseColor("#F00A1017"), Color.parseColor("#F00B1822"))).apply {
                cornerRadius = dp(26).toFloat(); setStroke(dp(1), Color.parseColor("#6622D3EE"))
            }
        }
        titulo = TextView(this).apply { setTextColor(Color.parseColor("#22D3EE")); textSize = 12f; letterSpacing = 0.2f; typeface = android.graphics.Typeface.create("sans-serif-condensed", android.graphics.Typeface.BOLD) }
        cuerpo = TextView(this).apply { setTextColor(Color.parseColor("#E8EEF3")); textSize = 17f; setPadding(0, dp(10), 0, dp(6)) }
        nivel = TextView(this).apply { setTextColor(Color.parseColor("#34D399")); textSize = 22f; typeface = android.graphics.Typeface.MONOSPACE }
        val botonListo = Button(this).apply {
            text = "Listo"; setTextColor(Color.parseColor("#06121A"))
            background = GradientDrawable().apply { cornerRadius = dp(14).toFloat(); setColor(Color.parseColor("#22D3EE")) }
            setOnClickListener { if (grabando) parar = true else cerrar() }
        }
        caja.addView(titulo); caja.addView(cuerpo); caja.addView(nivel)
        caja.addView(botonListo, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(46)).apply { topMargin = dp(10) })
        setContentView(caja)
        window.setGravity(Gravity.BOTTOM)
        window.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        window.setBackgroundDrawableResource(android.R.color.transparent)

        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            mostrar("SIN PERMISO", "Abre la app Llamada y acepta el micrófono."); return
        }
        Thread { flujo() }.start()
    }

    override fun onDestroy() { cerrado = true; parar = true; super.onDestroy() }

    private fun cerrar() { cerrado = true; finish() }
    private fun mostrar(t: String, c: String, n: String = "") = main.post { titulo.text = t; cuerpo.text = c; nivel.text = n }
    private fun cerrarEn(ms: Long) { main.postDelayed({ if (!isFinishing) cerrar() }, ms) }
    private val etiqueta get() = when (quien) { "firstmate" -> "FIRSTMATE"; "nota" -> "NOTA RÁPIDA"; else -> "HERMES" }

    @Volatile private var grabando = false

    private fun flujo() {
        val prefs = getSharedPreferences(FlotaWidget.PREFS, MODE_PRIVATE)
        val base = prefs.getString("url", null); val token = prefs.getString("token", null)
        if (base == null || token == null) { mostrar("SIN CONEXIÓN", "Abre la app Llamada una vez para configurarla."); return }
        try {
            mostrar(etiqueta, "Te escucho…")
            val pcm = grabar()
            if (pcm == null) { mostrar(etiqueta, "No te escuché."); cerrarEn(1800); return }
            mostrar(etiqueta, "Transcribiendo…")
            val texto = JSONObject(http("POST", "$base/dictar", pcm, "application/octet-stream", token, 60000)).optString("text").trim()
            if (texto.isEmpty()) { mostrar(etiqueta, "No entendí lo que dijiste."); cerrarEn(2000); return }
            when (quien) {
                "nota" -> {
                    http("POST", "$base/hablar", json(quien, texto), "application/json", token, 30000)
                    mostrar("NOTA GUARDADA", "«$texto»"); cerrarEn(2600)
                }
                "firstmate" -> {
                    mostrar(etiqueta, "«$texto»\n\nEsperando respuesta…")
                    http("POST", "$base/hablar", json(quien, texto), "application/json", token, 30000)
                    for (i in 0 until 4) {
                        if (cerrado) return
                        val respuestas = JSONObject(http("GET", "$base/respuesta?timeout=30", null, null, token, 45000)).optJSONArray("respuestas") ?: JSONArray()
                        if (respuestas.length() > 0) {
                            val r = (0 until respuestas.length()).joinToString("\n\n") { respuestas.optString(it) }
                            mostrar(etiqueta, r); decir(base, token, r); return
                        }
                    }
                    mostrar(etiqueta, "El firstmate aún no contesta. Su respuesta llegará como notificación.")
                }
                else -> {
                    mostrar(etiqueta, "«$texto»\n\nHermes está pensando…")
                    val r = JSONObject(http("POST", "$base/hablar", json(quien, texto), "application/json", token, 200000)).optString("respuesta")
                    mostrar(etiqueta, r.ifEmpty { "Hermes no contestó." })
                    if (r.isNotEmpty()) decir(base, token, r)
                }
            }
        } catch (e: Exception) {
            mostrar("ERROR", if (e.message?.contains("401") == true) "El Mac rechazó el acceso." else "No hay conexión con tu Mac.")
        }
    }

    private fun json(q: String, t: String) = JSONObject().put("quien", q).put("texto", t).toString().toByteArray()

    /** Graba hasta que termina la frase (voz y luego silencio), con tope de 30 s. Null si nadie habló. */
    private fun grabar(): ByteArray? {
        val tasa = 16000
        val buf = max(AudioRecord.getMinBufferSize(tasa, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT), 8192)
        val rec = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, tasa, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, buf)
        val salida = ByteArrayOutputStream()
        val bloque = ShortArray(1600) // 100 ms
        var tiempo = 0.0; var voz = 0.0; var silencio = 0.0; var piso = Float.MAX_VALUE
        grabando = true
        rec.startRecording()
        try {
            while (!cerrado) {
                val n = rec.read(bloque, 0, bloque.size)
                if (n <= 0) break
                var suma = 0.0
                for (i in 0 until n) { val v = bloque[i] / 32768.0; suma += v * v }
                val rms = sqrt(suma / n).toFloat()
                for (i in 0 until n) { salida.write(bloque[i].toInt() and 0xff); salida.write((bloque[i].toInt() shr 8) and 0xff) }
                tiempo += n / tasa.toDouble()
                if (tiempo <= 1.0) piso = min(piso, rms)
                val umbral = if (piso == Float.MAX_VALUE) 0.006f else min(0.03f, max(0.006f, piso * 2))
                if (rms >= umbral) { voz += n / tasa.toDouble(); silencio = 0.0 } else silencio += n / tasa.toDouble()
                main.post { nivel.text = "▮".repeat(min(12, (rms * 400).toInt() + 1)) }
                if (voz >= 0.2 && (silencio >= 1.1 || parar)) break
                if (voz < 0.2 && (tiempo >= 6.0 || parar)) return null
                if (tiempo >= 30.0) break
            }
        } finally { rec.stop(); rec.release(); grabando = false; main.post { nivel.text = "" } }
        return if (voz >= 0.2) salida.toByteArray() else null
    }

    private fun http(metodo: String, url: String, cuerpo: ByteArray?, tipo: String?, token: String, espera: Int): String {
        val c = URL(url).openConnection() as HttpURLConnection
        try {
            c.requestMethod = metodo; c.connectTimeout = 8000; c.readTimeout = espera
            c.setRequestProperty("Authorization", "Bearer $token")
            if (cuerpo != null) { c.doOutput = true; tipo?.let { c.setRequestProperty("Content-Type", it) }; c.outputStream.use { it.write(cuerpo) } }
            if (c.responseCode !in 200..299) throw RuntimeException("HTTP ${c.responseCode}")
            return c.inputStream.bufferedReader().readText()
        } finally { c.disconnect() }
    }

    /** Lee el texto en la voz configurada: el servidor manda PCM de 48 kHz mono y se reproduce a medida que llega. */
    private fun decir(base: String, token: String, texto: String) {
        val c = URL("$base/voz").openConnection() as HttpURLConnection
        var pista: AudioTrack? = null
        try {
            c.requestMethod = "POST"; c.doOutput = true; c.connectTimeout = 8000; c.readTimeout = 120000
            c.setRequestProperty("Authorization", "Bearer $token"); c.setRequestProperty("Content-Type", "application/json")
            c.outputStream.use { it.write(JSONObject().put("text", texto).toString().toByteArray()) }
            if (c.responseCode !in 200..299) return
            val min = AudioTrack.getMinBufferSize(48000, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
            pista = AudioTrack.Builder()
                .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                .setAudioFormat(AudioFormat.Builder().setSampleRate(48000).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                .setBufferSizeInBytes(min * 4).setTransferMode(AudioTrack.MODE_STREAM).build()
            pista.play()
            // Las muestras son de 2 bytes: una lectura puede cortar una a la mitad, y escribirla así desalinea todo el
            // audio (suena a estática). Se escribe solo un número par de bytes y el resto pasa a la siguiente lectura.
            val b = ByteArray(9600)
            var resto = 0
            c.inputStream.use { entrada ->
                while (!cerrado) {
                    val n = entrada.read(b, resto, b.size - resto)
                    if (n <= 0) break
                    val total = resto + n
                    val util = total - total % 2
                    pista.write(b, 0, util)
                    resto = total - util
                    if (resto > 0) b[0] = b[util]
                }
            }
            Thread.sleep(400)
        } catch (e: Exception) { /* sin voz: el texto ya está en pantalla */ } finally {
            try { pista?.stop(); pista?.release() } catch (e: Exception) {}
            c.disconnect()
        }
    }
}
