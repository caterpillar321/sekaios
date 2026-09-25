/*
 * sekai-winshot — Hyprland 창 하나를 캡처해 작게 줄인 그림을 내보낸다 (작업 표시줄 창 미리보기).
 *
 *   sekai-winshot <창 주소(16진)> <최대 너비> <최대 높이>
 *
 * 표준 출력: "SWS1 <너비> <높이>\n" 다음에 RGBA 바이트 (너비*높이*4).
 * hyprland-toplevel-export-v1 을 쓴다 — 가려진 창·다른 워크스페이스의 창도 찍힌다.
 * 실패하면 아무것도 쓰지 않고 1 로 끝난다.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <wayland-client.h>

#include "hyprland-toplevel-export-v1-client-protocol.h"

/* v2 요청(capture_toplevel_with_wlr_toplevel_handle)이 가리키는 인터페이스 — 쓰지 않으니 이름만 */
const struct wl_interface zwlr_foreign_toplevel_handle_v1_interface = {
    "zwlr_foreign_toplevel_handle_v1", 3, 0, NULL, 0, NULL};

static struct wl_shm *shm;
static struct hyprland_toplevel_export_manager_v1 *mgr;

static struct {
    int      have_buffer, buffer_done, ready, failed;
    uint32_t format, width, height, stride, flags;
} fr;

static void reg_global(void *d, struct wl_registry *r, uint32_t name, const char *iface, uint32_t ver) {
    (void)d;
    if (strcmp(iface, wl_shm_interface.name) == 0)
        shm = wl_registry_bind(r, name, &wl_shm_interface, 1);
    else if (strcmp(iface, hyprland_toplevel_export_manager_v1_interface.name) == 0)
        mgr = wl_registry_bind(r, name, &hyprland_toplevel_export_manager_v1_interface, ver < 2 ? ver : 2);
}
static void reg_remove(void *d, struct wl_registry *r, uint32_t name) { (void)d; (void)r; (void)name; }
static const struct wl_registry_listener reg_listener = {reg_global, reg_remove};

static int shm_ok(uint32_t f) {
    return f == WL_SHM_FORMAT_ARGB8888 || f == WL_SHM_FORMAT_XRGB8888 ||
           f == WL_SHM_FORMAT_ABGR8888 || f == WL_SHM_FORMAT_XBGR8888;
}

static void f_buffer(void *d, struct hyprland_toplevel_export_frame_v1 *f, uint32_t format, uint32_t w, uint32_t h,
                     uint32_t stride) {
    (void)d; (void)f;
    if (fr.have_buffer && shm_ok(fr.format))
        return;                              /* 이미 쓸 수 있는 형식을 받았다 */
    fr.format = format; fr.width = w; fr.height = h; fr.stride = stride;
    fr.have_buffer = 1;
}
static void f_damage(void *d, struct hyprland_toplevel_export_frame_v1 *f, uint32_t x, uint32_t y, uint32_t w,
                     uint32_t h) { (void)d; (void)f; (void)x; (void)y; (void)w; (void)h; }
static void f_flags(void *d, struct hyprland_toplevel_export_frame_v1 *f, uint32_t flags) {
    (void)d; (void)f;
    fr.flags = flags;
}
static void f_ready(void *d, struct hyprland_toplevel_export_frame_v1 *f, uint32_t a, uint32_t b, uint32_t c) {
    (void)d; (void)f; (void)a; (void)b; (void)c;
    fr.ready = 1;
}
static void f_failed(void *d, struct hyprland_toplevel_export_frame_v1 *f) {
    (void)d; (void)f;
    fr.failed = 1;
}
static void f_dmabuf(void *d, struct hyprland_toplevel_export_frame_v1 *f, uint32_t fmt, uint32_t w, uint32_t h) {
    (void)d; (void)f; (void)fmt; (void)w; (void)h;
}
static void f_buffer_done(void *d, struct hyprland_toplevel_export_frame_v1 *f) {
    (void)d; (void)f;
    fr.buffer_done = 1;
}
static const struct hyprland_toplevel_export_frame_v1_listener frame_listener = {
    f_buffer, f_damage, f_flags, f_ready, f_failed, f_dmabuf, f_buffer_done};

/* 제한 시간 안에 조건이 될 때까지 이벤트 처리 */
static int wait_for(struct wl_display *dpy, int *flag, int ms) {
    struct pollfd p = {.fd = wl_display_get_fd(dpy), .events = POLLIN};
    while (!*flag && !fr.failed) {
        while (wl_display_prepare_read(dpy) != 0)
            wl_display_dispatch_pending(dpy);
        wl_display_flush(dpy);
        int n = poll(&p, 1, ms);
        if (n <= 0) {
            wl_display_cancel_read(dpy);
            return -1;
        }
        if (wl_display_read_events(dpy) < 0)
            return -1;
        wl_display_dispatch_pending(dpy);
    }
    return fr.failed ? -1 : 0;
}

int main(int argc, char **argv) {
    if (argc != 4) {
        fprintf(stderr, "사용법: %s <창 주소> <최대 너비> <최대 높이>\n", argv[0]);
        return 2;
    }
    uint64_t addr = strtoull(argv[1], NULL, 16);
    int      maxw = atoi(argv[2]), maxh = atoi(argv[3]);
    if (!addr || maxw <= 0 || maxh <= 0)
        return 2;

    struct wl_display *dpy = wl_display_connect(NULL);
    if (!dpy)
        return 1;
    struct wl_registry *reg = wl_display_get_registry(dpy);
    wl_registry_add_listener(reg, &reg_listener, NULL);
    wl_display_roundtrip(dpy);
    if (!shm || !mgr)
        return 1;

    struct hyprland_toplevel_export_frame_v1 *frame =
        hyprland_toplevel_export_manager_v1_capture_toplevel(mgr, 0, (uint32_t)(addr & 0xFFFFFFFF));
    hyprland_toplevel_export_frame_v1_add_listener(frame, &frame_listener, NULL);
    if (wait_for(dpy, &fr.buffer_done, 2000) < 0 || !fr.have_buffer || !shm_ok(fr.format))
        return 1;

    size_t size = (size_t)fr.stride * fr.height;
    int    fd   = memfd_create("sekai-winshot", MFD_CLOEXEC);
    if (fd < 0 || ftruncate(fd, (off_t)size) < 0)
        return 1;
    uint8_t *px = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (px == MAP_FAILED)
        return 1;
    struct wl_shm_pool *pool = wl_shm_create_pool(shm, fd, (int32_t)size);
    struct wl_buffer   *buf  = wl_shm_pool_create_buffer(pool, 0, (int32_t)fr.width, (int32_t)fr.height,
                                                         (int32_t)fr.stride, fr.format);
    hyprland_toplevel_export_frame_v1_copy(frame, buf, 1);
    if (wait_for(dpy, &fr.ready, 2000) < 0)
        return 1;

    /* 비율을 지키며 줄인다 (칸 평균) */
    double s = (double)maxw / fr.width;
    if ((double)maxh / fr.height < s)
        s = (double)maxh / fr.height;
    if (s > 1)
        s = 1;
    int ow = (int)(fr.width * s + 0.5), oh = (int)(fr.height * s + 0.5);
    if (ow < 1) ow = 1;
    if (oh < 1) oh = 1;
    const int bgr   = fr.format == WL_SHM_FORMAT_ARGB8888 || fr.format == WL_SHM_FORMAT_XRGB8888; /* 바이트 순서 B G R A */
    const int alpha = fr.format == WL_SHM_FORMAT_ARGB8888 || fr.format == WL_SHM_FORMAT_ABGR8888;
    const int flip  = fr.flags & HYPRLAND_TOPLEVEL_EXPORT_FRAME_V1_FLAGS_Y_INVERT;

    uint8_t *out = malloc((size_t)ow * oh * 4);
    if (!out)
        return 1;
    for (int oy = 0; oy < oh; oy++) {
        uint32_t y0 = (uint32_t)((double)oy * fr.height / oh), y1 = (uint32_t)((double)(oy + 1) * fr.height / oh);
        if (y1 <= y0) y1 = y0 + 1;
        for (int ox = 0; ox < ow; ox++) {
            uint32_t x0 = (uint32_t)((double)ox * fr.width / ow), x1 = (uint32_t)((double)(ox + 1) * fr.width / ow);
            if (x1 <= x0) x1 = x0 + 1;
            uint64_t acc[4] = {0}, n = 0;
            for (uint32_t y = y0; y < y1 && y < fr.height; y++) {
                const uint8_t *row = px + (size_t)(flip ? fr.height - 1 - y : y) * fr.stride;
                for (uint32_t x = x0; x < x1 && x < fr.width; x++) {
                    const uint8_t *p = row + x * 4;
                    acc[0] += bgr ? p[2] : p[0];
                    acc[1] += p[1];
                    acc[2] += bgr ? p[0] : p[2];
                    acc[3] += alpha ? p[3] : 255;
                    n++;
                }
            }
            uint8_t *o = out + ((size_t)oy * ow + ox) * 4;
            for (int k = 0; k < 4; k++)
                o[k] = n ? (uint8_t)(acc[k] / n) : 0;
        }
    }

    printf("SWS1 %d %d\n", ow, oh);
    fflush(stdout);
    size_t left = (size_t)ow * oh * 4;
    const uint8_t *w = out;
    while (left) {
        ssize_t k = write(STDOUT_FILENO, w, left);
        if (k < 0) {
            if (errno == EINTR)
                continue;
            return 1;
        }
        w += k; left -= (size_t)k;
    }
    return 0;
}
