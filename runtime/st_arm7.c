/* ARM7 por HLE (fase 1.3 de docs/PLAN.md): responde a los mensajes que el ARM9 manda por la
 * FIFO del PXI. Un mensaje es una palabra: bits 0-4 tag (servicio), bit 5 error, bits 6-31 datos.
 *
 * Por defecto cada servicio "acepta" la orden: responde con el mismo tag y los mismos datos
 * (comando) y resultado 0 = éxito. Es lo que espera el ARM9 para dar por terminada una petición
 * (visto con la ROM: tras mandar tag 5 / datos 1000h gira hasta que llega la respuesta).
 * Los servicios que devuelven datos los dejan en la zona compartida antes de responder:
 *   - RTC (tag 5): fecha y hora del PC en BCD (27FFDE8h).
 * Supuestos (sin documentación oficial): números de tag del SDK (4 NVRAM, 5 RTC, 6 táctil,
 * 7 sonido, 8 energía, 9 micrófono, 10 Wi-Fi, 12 OS, 14 cartucho) y la dirección del RTC. */
#ifndef _WIN32
#define _POSIX_C_SOURCE 200809L
#endif
#include <stdio.h>
#include <string.h>
#include <time.h>

#include "strecomp.h"

static const char* tag_name(unsigned tag) {
    switch (tag) {
    case 4: return "NVRAM";
    case 5: return "RTC";
    case 6: return "táctil";
    case 7: return "sonido";
    case 8: return "energía";
    case 9: return "micrófono";
    case 10: return "Wi-Fi";
    case 11: return "FS";
    case 12: return "OS";
    case 13: return "cartucho GBA";
    case 14: return "cartucho";
    default: return "?";
    }
}

static uint8_t bcd(int v) { return (uint8_t)(((v / 10) << 4) | (v % 10)); }

static void rtc_datetime(void) {
    time_t t = time(NULL);
    struct tm tmv;
#ifdef _WIN32
    localtime_s(&tmv, &t);
#else
    localtime_r(&t, &tmv);
#endif
    uint8_t d[8] = {bcd(tmv.tm_year % 100), bcd(tmv.tm_mon + 1), bcd(tmv.tm_mday), (uint8_t)tmv.tm_wday,
                    (uint8_t)(bcd(tmv.tm_hour) | (tmv.tm_hour >= 12 ? 0x40 : 0)), bcd(tmv.tm_min), bcd(tmv.tm_sec), 0};
    st_load(0x027FFDE8u, d, sizeof d);
}

static unsigned long logged;

void st_arm7_pxi(StCpu* c, uint32_t msg) {
    (void)c;
    unsigned tag = msg & 0x1Fu;
    uint32_t data = msg >> 6;
    if (tag == 5) rtc_datetime();
    uint32_t reply = (data << 6) | tag;   /* mismo comando, sin error */
    if (logged++ < 64)
        fprintf(stderr, "[arm7] %s (tag %u): datos %07X -> respuesta %08X\n", tag_name(tag), tag, data, reply);
    st_pxi_reply(reply);
}
