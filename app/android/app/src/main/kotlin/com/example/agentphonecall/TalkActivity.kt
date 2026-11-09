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
 * Small screen of the «Talk» widget: records what the user says, transcribes it on the Mac (Whisper) and sends it to
 * whoever the button chose: «firstmate», «hermes» (optional, hermes-agent) or «note» (the coordinator's inbox).
 * The answer is spoken in the configured voice and shown on screen.
 */
class TalkActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var heading: TextView
    private lateinit var body: TextView
    private lateinit var level: TextView
    @Volatile private var stop = false
    @Volatile private var closed = false
    private var to = "hermes"

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        to = intent.getStringExtra("to") ?: "hermes"
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(22), dp(20), dp(22), dp(18))
            background = GradientDrawable(GradientDrawable.Orientation.TL_BR, intArrayOf(Color.parseColor("#F00A1017"), Color.parseColor("#F00B1822"))).apply {
                cornerRadius = dp(26).toFloat(); setStroke(dp(1), Color.parseColor("#6622D3EE"))
            }
        }
        heading = TextView(this).apply { setTextColor(Color.parseColor("#22D3EE")); textSize = 12f; letterSpacing = 0.2f; typeface = android.graphics.Typeface.create("sans-serif-condensed", android.graphics.Typeface.BOLD) }
        body = TextView(this).apply { setTextColor(Color.parseColor("#E8EEF3")); textSize = 17f; setPadding(0, dp(10), 0, dp(6)) }
        level = TextView(this).apply { setTextColor(Color.parseColor("#34D399")); textSize = 22f; typeface = android.graphics.Typeface.MONOSPACE }
        val doneButton = Button(this).apply {
            text = "Done"; setTextColor(Color.parseColor("#06121A"))
            background = GradientDrawable().apply { cornerRadius = dp(14).toFloat(); setColor(Color.parseColor("#22D3EE")) }
            setOnClickListener { if (recording) stop = true else close() }
        }
        box.addView(heading); box.addView(body); box.addView(level)
        box.addView(doneButton, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(46)).apply { topMargin = dp(10) })
        setContentView(box)
        window.setGravity(Gravity.BOTTOM)
        window.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        window.setBackgroundDrawableResource(android.R.color.transparent)

        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            show("NO PERMISSION", "Open the Agent Call app and allow the microphone."); return
        }
        Thread { flow() }.start()
    }

    override fun onDestroy() { closed = true; stop = true; super.onDestroy() }

    private fun close() { closed = true; finish() }
    private fun show(t: String, c: String, n: String = "") = main.post { heading.text = t; body.text = c; level.text = n }
    private fun closeIn(ms: Long) { main.postDelayed({ if (!isFinishing) close() }, ms) }
    private val label get() = when (to) { "firstmate" -> "FIRSTMATE"; "note" -> "QUICK NOTE"; else -> "HERMES" }

    @Volatile private var recording = false

    private fun flow() {
        val prefs = getSharedPreferences(FleetWidget.PREFS, MODE_PRIVATE)
        val base = prefs.getString("url", null); val token = prefs.getString("token", null)
        if (base == null || token == null) { show("NOT CONNECTED", "Open the Agent Call app once to set it up."); return }
        try {
            show(label, "Listening…")
            val pcm = record()
            if (pcm == null) { show(label, "I didn't hear you."); closeIn(1800); return }
            show(label, "Transcribing…")
            val text = JSONObject(http("POST", "$base/dictate", pcm, "application/octet-stream", token, 60000)).optString("text").trim()
            if (text.isEmpty()) { show(label, "I didn't catch what you said."); closeIn(2000); return }
            when (to) {
                "note" -> {
                    http("POST", "$base/talk", json(to, text), "application/json", token, 30000)
                    show("NOTE SAVED", "«$text»"); closeIn(2600)
                }
                "firstmate" -> {
                    show(label, "«$text»\n\nWaiting for the answer…")
                    http("POST", "$base/talk", json(to, text), "application/json", token, 30000)
                    for (i in 0 until 4) {
                        if (closed) return
                        val replies = JSONObject(http("GET", "$base/reply?timeout=30", null, null, token, 45000)).optJSONArray("replies") ?: JSONArray()
                        if (replies.length() > 0) {
                            val r = (0 until replies.length()).joinToString("\n\n") { replies.optString(it) }
                            show(label, r); speak(base, token, r); return
                        }
                    }
                    show(label, "The coordinator has not answered yet. Its answer will arrive as a notification.")
                }
                else -> {
                    show(label, "«$text»\n\nHermes is thinking…")
                    val r = JSONObject(http("POST", "$base/talk", json(to, text), "application/json", token, 200000)).optString("reply")
                    show(label, r.ifEmpty { "Hermes did not answer." })
                    if (r.isNotEmpty()) speak(base, token, r)
                }
            }
        } catch (e: Exception) {
            show("ERROR", if (e.message?.contains("401") == true) "The Mac refused access." else "No connection to your Mac.")
        }
    }

    private fun json(to: String, text: String) = JSONObject().put("to", to).put("text", text).toString().toByteArray()

    /** Records until the phrase ends (voice, then silence), 30 s at most. Null if nobody spoke. */
    private fun record(): ByteArray? {
        val rate = 16000
        val buf = max(AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT), 8192)
        val rec = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, buf)
        val out = ByteArrayOutputStream()
        val block = ShortArray(1600) // 100 ms
        var elapsed = 0.0; var voice = 0.0; var silence = 0.0; var floor = Float.MAX_VALUE
        recording = true
        rec.startRecording()
        try {
            while (!closed) {
                val n = rec.read(block, 0, block.size)
                if (n <= 0) break
                var sum = 0.0
                for (i in 0 until n) { val v = block[i] / 32768.0; sum += v * v }
                val rms = sqrt(sum / n).toFloat()
                for (i in 0 until n) { out.write(block[i].toInt() and 0xff); out.write((block[i].toInt() shr 8) and 0xff) }
                elapsed += n / rate.toDouble()
                if (elapsed <= 1.0) floor = min(floor, rms)
                val threshold = if (floor == Float.MAX_VALUE) 0.006f else min(0.03f, max(0.006f, floor * 2))
                if (rms >= threshold) { voice += n / rate.toDouble(); silence = 0.0 } else silence += n / rate.toDouble()
                main.post { level.text = "▮".repeat(min(12, (rms * 400).toInt() + 1)) }
                if (voice >= 0.2 && (silence >= 1.1 || stop)) break
                if (voice < 0.2 && (elapsed >= 6.0 || stop)) return null
                if (elapsed >= 30.0) break
            }
        } finally { rec.stop(); rec.release(); recording = false; main.post { level.text = "" } }
        return if (voice >= 0.2) out.toByteArray() else null
    }

    private fun http(method: String, url: String, payload: ByteArray?, type: String?, token: String, timeout: Int): String {
        val c = URL(url).openConnection() as HttpURLConnection
        try {
            c.requestMethod = method; c.connectTimeout = 8000; c.readTimeout = timeout
            c.setRequestProperty("Authorization", "Bearer $token")
            if (payload != null) { c.doOutput = true; type?.let { c.setRequestProperty("Content-Type", it) }; c.outputStream.use { it.write(payload) } }
            if (c.responseCode !in 200..299) throw RuntimeException("HTTP ${c.responseCode}")
            return c.inputStream.bufferedReader().readText()
        } finally { c.disconnect() }
    }

    /** Reads the text aloud in the configured voice: the server sends 48 kHz mono PCM, played as it arrives. */
    private fun speak(base: String, token: String, text: String) {
        val c = URL("$base/speak").openConnection() as HttpURLConnection
        var track: AudioTrack? = null
        try {
            c.requestMethod = "POST"; c.doOutput = true; c.connectTimeout = 8000; c.readTimeout = 120000
            c.setRequestProperty("Authorization", "Bearer $token"); c.setRequestProperty("Content-Type", "application/json")
            c.outputStream.use { it.write(JSONObject().put("text", text).toString().toByteArray()) }
            if (c.responseCode !in 200..299) return
            val min = AudioTrack.getMinBufferSize(48000, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
            track = AudioTrack.Builder()
                .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                .setAudioFormat(AudioFormat.Builder().setSampleRate(48000).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                .setBufferSizeInBytes(min * 4).setTransferMode(AudioTrack.MODE_STREAM).build()
            track.play()
            // Samples are 2 bytes: a read can cut one in half, and writing it like that misaligns all the audio (it
            // sounds like static). Only an even number of bytes is written; the rest goes on to the next read.
            val b = ByteArray(9600)
            var rest = 0
            c.inputStream.use { input ->
                while (!closed) {
                    val n = input.read(b, rest, b.size - rest)
                    if (n <= 0) break
                    val total = rest + n
                    val usable = total - total % 2
                    track.write(b, 0, usable)
                    rest = total - usable
                    if (rest > 0) b[0] = b[usable]
                }
            }
            Thread.sleep(400)
        } catch (e: Exception) { /* no voice: the text is already on screen */ } finally {
            try { track?.stop(); track?.release() } catch (e: Exception) {}
            c.disconnect()
        }
    }
}
