package com.example.agentphonecall

import android.media.AudioManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.telecom.CallAudioState
import com.cloudwebrtc.webrtc.audio.AudioSwitchManager
import com.hiennv.flutter_callkit_incoming.CallkitConnection
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private var llamarPendiente: String? = null
    /** Sección Decisiones por abrir: "" para la lista, o la decisión (id, o texto si no tiene) tocada en el widget. */
    private var abrirDecisionesPendiente: String? = null

    override fun onCreate(savedInstanceState: android.os.Bundle?) {
        super.onCreate(savedInstanceState)
        llamarPendiente = intent?.getStringExtra(HablarWidget.EXTRA_LLAMAR)
        abrirDecisionesPendiente = decisionPedida(intent)
        fijarSiSePide(intent)
    }

    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        intent.getStringExtra(HablarWidget.EXTRA_LLAMAR)?.let { llamarPendiente = it }
        decisionPedida(intent)?.let { abrirDecisionesPendiente = it }
        fijarSiSePide(intent)
    }

    private fun decisionPedida(intent: android.content.Intent?): String? =
        if (intent?.getBooleanExtra(DecisionesWidget.EXTRA_ABRIR, false) == true)
            intent.getStringExtra(DecisionesWidget.EXTRA_DECISION) ?: ""
        else null

    /** `am start --es fijar flota|llamar`: pide a Android fijar ese widget ahora, sin importar si ya se pidió antes. */
    private fun fijarSiSePide(intent: android.content.Intent?) {
        val clase = when (intent?.getStringExtra("fijar")) {
            "flota" -> FlotaWidget::class.java
            "hablar" -> HablarWidget::class.java
            "decisiones" -> DecisionesWidget::class.java
            else -> return
        }
        val manager = android.appwidget.AppWidgetManager.getInstance(this)
        if (manager.isRequestPinAppWidgetSupported) manager.requestPinAppWidget(android.content.ComponentName(this, clase), null, null)
    }

    /** Si un widget nuestro no está en ninguna pantalla, pide a Android fijarlo (una sola vez por widget; el usuario acepta). */
    private fun pedirWidgetUnaVez(clase: Class<*>, marca: String) {
        val prefs = getSharedPreferences(FlotaWidget.PREFS, MODE_PRIVATE)
        val manager = android.appwidget.AppWidgetManager.getInstance(this)
        val componente = android.content.ComponentName(this, clase)
        if (prefs.getBoolean(marca, false) || manager.getAppWidgetIds(componente).isNotEmpty()) return
        if (manager.isRequestPinAppWidgetSupported && manager.requestPinAppWidget(componente, null, null)) {
            prefs.edit().putBoolean(marca, true).apply()
        }
    }

    /**
     * Al colgar, devuelve el audio del teléfono a las demás apps, pase lo que pase antes:
     * 1. la sesión de audio de WebRTC (modo llamada y foco de audio que pidió): flutter_webrtc solo la suelta al
     *    desechar la última PeerConnection, y un micrófono abierto tras colgar la deja tomada sin ninguna;
     * 2. la llamada del sistema (Telecom) de esta llamada, aunque ya no figure en la lista del plugin;
     * 3. detrás de ambos (stop() se ejecuta en el hilo principal), el modo de audio normal, sin altavoz forzado,
     *    sin Bluetooth SCO ni dispositivo de comunicación fijado. Solo se toca el modo si quedó en «llamada por
     *    internet»: una llamada telefónica real (MODE_IN_CALL) no se toca.
     */
    private fun liberarAudio(callId: String?, result: MethodChannel.Result) {
        try { AudioSwitchManager.instance?.stop() } catch (_: Exception) {}
        try { callId?.let { CallkitConnection.find(it) }?.markEnded() } catch (_: Exception) {}
        Handler(Looper.getMainLooper()).post {
            try {
                val audio = getSystemService(AUDIO_SERVICE) as AudioManager
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) audio.clearCommunicationDevice()
                @Suppress("DEPRECATION")
                if (audio.isSpeakerphoneOn) audio.isSpeakerphoneOn = false
                @Suppress("DEPRECATION")
                if (audio.isBluetoothScoOn) { audio.stopBluetoothSco(); audio.isBluetoothScoOn = false }
                if (audio.mode == AudioManager.MODE_IN_COMMUNICATION) audio.mode = AudioManager.MODE_NORMAL
            } catch (_: Exception) {}
            result.success(true)
        }
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        // A call answered from the lock screen is a self-managed Telecom call: Android
        // routes its audio through the Connection and ignores app-level speaker changes.
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "llamada/audio")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "setSpeaker" -> {
                        val id = call.argument<String>("callId")
                        val on = call.argument<Boolean>("on") ?: false
                        val connection = id?.let { CallkitConnection.find(it) }
                        if (connection == null) {
                            result.success(false) // not a Telecom call: caller uses WebRTC routing
                        } else {
                            @Suppress("DEPRECATION")
                            connection.setAudioRoute(
                                if (on) CallAudioState.ROUTE_SPEAKER else CallAudioState.ROUTE_WIRED_OR_EARPIECE
                            )
                            result.success(true)
                        }
                    }
                    "liberar" -> liberarAudio(call.argument<String>("callId"), result)
                    else -> result.notImplemented()
                }
            }
        // El widget «Flota» necesita la URL y el token del servidor: la app se los deja al abrirse.
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "llamada/widget")
            .setMethodCallHandler { call, result ->
                if (call.method == "setConfig") {
                    val url = call.argument<String>("url"); val token = call.argument<String>("token")
                    // Vacíos (APK sin --dart-define) no pisan la configuración buena que ya tienen los widgets.
                    if (!url.isNullOrBlank() && !token.isNullOrBlank())
                        getSharedPreferences(FlotaWidget.PREFS, MODE_PRIVATE).edit()
                            .putString("url", url).putString("token", token).apply()
                    sendBroadcast(android.content.Intent(this, FlotaWidget::class.java).setAction(FlotaWidget.ACCION_REFRESCAR))
                    pedirWidgetUnaVez(FlotaWidget::class.java, "pedido2")
                    result.success(true)
                } else if (call.method == "takeAbrirDecisiones") {
                    result.success(abrirDecisionesPendiente)
                    abrirDecisionesPendiente = null
                } else if (call.method == "takeDial") {
                    result.success(llamarPendiente)
                    llamarPendiente = null
                } else result.notImplemented()
            }
        // Sección «Decisiones»: el estado y el envío son los mismos que usa el widget (DecisionesEstado / DecisionEnvio).
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "llamada/decisiones")
            .setMethodCallHandler { call, result ->
                val app = applicationContext
                fun enSegundoPlano(trabajo: () -> Any?) = Thread {
                    val salida = try { trabajo() } catch (e: Exception) { "Error: ${e.message}" }
                    runOnUiThread { result.success(salida) }
                }.start()
                when (call.method) {
                    "estados" -> result.success(DecisionesEstado.todo(app).toString())
                    "responder" -> {
                        val id = call.argument<String>("id"); val opcion = call.argument<String>("opcion"); val titulo = call.argument<String>("titulo") ?: ""
                        if (id == null || opcion == null) result.error("args", "faltan id u opcion", null)
                        else enSegundoPlano { DecisionEnvio.responder(app, id, titulo, opcion) }
                    }
                    "llamame" -> {
                        val id = call.argument<String>("id"); val titulo = call.argument<String>("titulo") ?: ""
                        if (id == null) result.error("args", "falta id", null) else enSegundoPlano { DecisionEnvio.llamame(app, id, titulo) }
                    }
                    else -> result.notImplemented()
                }
            }
    }
}
