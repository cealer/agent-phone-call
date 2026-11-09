package com.example.agentphonecall

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class TareasPrincipalTest {
    private fun espera(vararg e: String) =
        TareasPrincipal.interpretar("""{"generado":"2026-10-21T11:30:00.000Z","espera":[${e.joinToString(",")}]}""").decisiones

    @Test fun conDetalleElTextoCompletoEsElDetalle() {
        val d = espera("""{"id":"d1","titulo":"Fusionar la rama…","detalle":"  Fusionar la rama fm/x en main ahora  ","opciones":["Fusionar"]}""").single()
        assertEquals("Fusionar la rama…", d.titulo)
        assertEquals("Fusionar la rama fm/x en main ahora", d.detalle)
        assertEquals("Fusionar la rama fm/x en main ahora", d.texto)
        assertEquals(listOf("Fusionar"), d.opciones)
    }

    @Test fun sinDetalleElTextoCompletoEsElTitulo() {
        val ds = espera(""""Solo texto"""", """{"id":"d1","titulo":"Subir"}""", """{"id":"d2","titulo":"Borrar","detalle":"  "}""", """{"id":"d3","titulo":"Mover","detalle":5}""")
        assertEquals(listOf("Solo texto", "Subir", "Borrar", "Mover"), ds.map { it.texto })
        ds.forEach { assertNull(it.detalle) }
    }

    @Test fun sinIdConservaElDetalle() {
        val d = espera("""{"titulo":"Aviso","detalle":"Aviso largo"}""").single()
        assertNull(d.id)
        assertEquals("Aviso largo", d.texto)
    }

    @Test fun losTextosEnviadosLlevanIdYTextoCompleto() {
        assertEquals(
            "[widget-llamame] id=d1 | El capitán pide que lo llames para explicarle la decisión: Fusionar la rama fm/x en main ahora",
            DecisionEnvio.textoLlamame("d1", "Fusionar la rama fm/x en main ahora"))
        assertEquals(
            "[widget-decision] id=d1 respuesta=Sí | El capitán responde «Sí» a la decisión: Fusionar la rama fm/x en main ahora",
            DecisionEnvio.textoRespuesta("d1", "Sí", "Fusionar la rama fm/x en main ahora"))
    }

    private fun principal(marcha: List<String>, espera: List<String>, cola: List<String>) =
        TareasPrincipal(0L, marcha, espera.map { TareasPrincipal.Decision("x", it, listOf("Sí")) }, cola)

    @Test fun flotaMuestraMarchaYColaPeroNoLasDecisiones() {
        val l = principal(listOf("m1", "m2", "m3"), listOf("d1"), listOf("c1", "c2")).lineasFlota()
        assertEquals(listOf("▶" to "m1", "▶" to "m2", "·" to "c1"), l.map { it.marca to it.texto })
    }

    @Test fun flotaSinMarchaLoDice() {
        val l = principal(emptyList(), listOf("d1", "d2"), listOf("c1")).lineasFlota()
        assertEquals(listOf("" to "Sin trabajo en marcha", "·" to "c1"), l.map { it.marca to it.texto })
        assertEquals(listOf("" to "Sin trabajo en marcha"), principal(emptyList(), emptyList(), emptyList()).lineasFlota().map { it.marca to it.texto })
    }
}
