#define _GNU_SOURCE
#include <errno.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>
#include "xdg-shell-client.h"
#include "presentation-time-client.h"

/* Ordinary client connection: never requests privileged capture capabilities. */
struct probe;
struct phase {
    struct probe *probe;
    unsigned number;
    bool framed, presented;
    struct wl_buffer *buffer;
    void *pixels;
    size_t size;
};
struct probe {
    struct wl_display *display;
    struct wl_compositor *compositor;
    struct wl_shm *shm;
    struct xdg_wm_base *wm;
    struct wp_presentation *presentation;
    struct wl_seat *seat;
    struct wl_keyboard *keyboard;
    struct wl_surface *surface;
    struct xdg_surface *xdg_surface;
    struct xdg_toplevel *toplevel;
    struct phase phases[2];
    bool configured, focused, announced, key_seen, success, closed, failed;
    unsigned expected_key;
};
static uint64_t milliseconds(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}
static void status(struct probe *p) {
    if (p->phases[0].presented && p->phases[0].framed && p->focused && !p->announced) {
        printf("CUSTOM_DESKTOP_PROBE_AWAIT_INPUT key=%u\n", p->expected_key);
        p->announced = true;
    }
    if (p->configured && p->announced && p->key_seen && p->phases[1].presented &&
        p->phases[1].framed && !p->failed && !p->success) {
        puts("CUSTOM_DESKTOP_WINDOW_INPUT_OK configured=1 initial_presented=1 keyboard_focus=1 key_pressed=1 redraw_presented=1");
        p->success = true;
    }
}
static void frame_done(void *data, struct wl_callback *callback, uint32_t time) {
    struct phase *phase = data;
    phase->framed = true;
    printf("CUSTOM_DESKTOP_PROBE_FRAME phase=%u time=%u\n", phase->number, time);
    wl_callback_destroy(callback);
    status(phase->probe);
}
static const struct wl_callback_listener frame_listener = { .done = frame_done };
static void feedback_output(void *data, struct wp_presentation_feedback *feedback, struct wl_output *output) {
    (void)data; (void)feedback; (void)output;
}
static void feedback_presented(void *data, struct wp_presentation_feedback *feedback,
        uint32_t hi, uint32_t lo, uint32_t nano, uint32_t refresh, uint32_t seq_hi,
        uint32_t seq_lo, uint32_t flags) {
    struct phase *phase = data;
    phase->presented = true;
    printf("CUSTOM_DESKTOP_PROBE_PRESENTED phase=%u sec=%llu nsec=%u refresh=%u sequence=%llu flags=%u\n",
           phase->number, (unsigned long long)(((uint64_t)hi << 32) | lo), nano, refresh,
           (unsigned long long)(((uint64_t)seq_hi << 32) | seq_lo), flags);
    wp_presentation_feedback_destroy(feedback);
    status(phase->probe);
}
static void feedback_discarded(void *data, struct wp_presentation_feedback *feedback) {
    struct phase *phase = data;
    fprintf(stderr, "Required presentation discarded: phase=%u\n", phase->number);
    phase->probe->failed = true;
    wp_presentation_feedback_destroy(feedback);
}
static const struct wp_presentation_feedback_listener feedback_listener = {
    .sync_output = feedback_output, .presented = feedback_presented, .discarded = feedback_discarded
};
static bool commit_pattern(struct probe *p, unsigned number) {
    struct phase *phase = &p->phases[number - 1];
    const int width = 480, height = 320, stride = width * 4;
    phase->probe = p; phase->number = number; phase->size = stride * height;
    int fd = memfd_create("custom-distro-desktop-probe", MFD_CLOEXEC);
    if (fd < 0 || ftruncate(fd, phase->size) < 0) { if (fd >= 0) close(fd); return false; }
    phase->pixels = mmap(NULL, phase->size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (phase->pixels == MAP_FAILED) { phase->pixels = NULL; close(fd); return false; }
    uint32_t *pixels = phase->pixels;
    const uint32_t before[4] = { 0xffcb3140, 0xff247e32, 0xff305ee0, 0xffe6bd29 };
    const uint32_t after[4] = { 0xff00ddd5, 0xffffffff, 0xff181818, 0xffdd00ba };
    for (int y = 0; y < height; ++y) for (int x = 0; x < width; ++x)
        pixels[y * width + x] = (number == 1 ? before : after)[(y >= height / 2) * 2 + (x >= width / 2)];
    struct wl_shm_pool *pool = wl_shm_create_pool(p->shm, fd, phase->size);
    phase->buffer = wl_shm_pool_create_buffer(pool, 0, width, height, stride, WL_SHM_FORMAT_XRGB8888);
    wl_shm_pool_destroy(pool); close(fd);
    struct wl_callback *callback = wl_surface_frame(p->surface);
    wl_callback_add_listener(callback, &frame_listener, phase);
    struct wp_presentation_feedback *feedback = wp_presentation_feedback(p->presentation, p->surface);
    wp_presentation_feedback_add_listener(feedback, &feedback_listener, phase);
    wl_surface_attach(p->surface, phase->buffer, 0, 0);
    wl_surface_damage(p->surface, 0, 0, width, height);
    wl_surface_commit(p->surface);
    printf("CUSTOM_DESKTOP_PROBE_COMMIT phase=%u width=%d height=%d\n", number, width, height);
    return true;
}
static void xdg_configure(void *data, struct xdg_surface *surface, uint32_t serial) {
    struct probe *p = data;
    xdg_surface_ack_configure(surface, serial);
    printf("CUSTOM_DESKTOP_PROBE_CONFIGURE serial=%u\n", serial);
    if (!p->configured) { p->configured = true; if (!commit_pattern(p, 1)) p->failed = true; }
}
static const struct xdg_surface_listener xdg_listener = { .configure = xdg_configure };
static void toplevel_configure(void *data, struct xdg_toplevel *t, int32_t width, int32_t height, struct wl_array *states) {
    (void)data; (void)t; (void)states;
    printf("CUSTOM_DESKTOP_PROBE_TOPLEVEL width=%d height=%d\n", width, height);
}
static void toplevel_close(void *data, struct xdg_toplevel *t) { (void)t; ((struct probe *)data)->closed = true; }
static const struct xdg_toplevel_listener toplevel_listener = { .configure = toplevel_configure, .close = toplevel_close };
static void wm_ping(void *data, struct xdg_wm_base *wm, uint32_t serial) { (void)data; xdg_wm_base_pong(wm, serial); }
static const struct xdg_wm_base_listener wm_listener = { .ping = wm_ping };
static void presentation_clock(void *data, struct wp_presentation *object, uint32_t clock) {
    (void)data; (void)object; printf("CUSTOM_DESKTOP_PROBE_PRESENTATION_CLOCK id=%u\n", clock);
}
static const struct wp_presentation_listener presentation_listener = { .clock_id = presentation_clock };
static void keyboard_keymap(void *data, struct wl_keyboard *k, uint32_t format, int fd, uint32_t size) {
    (void)data; (void)k; (void)format; (void)size; close(fd);
}
static void keyboard_enter(void *data, struct wl_keyboard *k, uint32_t serial, struct wl_surface *surface, struct wl_array *keys) {
    (void)k; (void)serial; (void)keys; struct probe *p = data;
    p->focused = surface == p->surface;
    printf("CUSTOM_DESKTOP_PROBE_FOCUS entered=%d\n", p->focused); status(p);
}
static void keyboard_leave(void *data, struct wl_keyboard *k, uint32_t serial, struct wl_surface *surface) {
    (void)k; (void)serial; (void)surface; ((struct probe *)data)->focused = false;
}
static void keyboard_key(void *data, struct wl_keyboard *k, uint32_t serial, uint32_t time, uint32_t key, uint32_t state) {
    (void)k; (void)serial; struct probe *p = data;
    printf("CUSTOM_DESKTOP_PROBE_KEY key=%u state=%u time=%u focus=%d\n", key, state, time, p->focused);
    if (key == p->expected_key && state == WL_KEYBOARD_KEY_STATE_PRESSED && p->focused && p->announced && !p->key_seen) {
        p->key_seen = true; if (!commit_pattern(p, 2)) p->failed = true;
    }
}
static void keyboard_modifiers(void *data, struct wl_keyboard *k, uint32_t serial, uint32_t dep, uint32_t lat, uint32_t lock, uint32_t group) {
    (void)data; (void)k; (void)serial; (void)dep; (void)lat; (void)lock; (void)group;
}
static const struct wl_keyboard_listener keyboard_listener = {
    .keymap = keyboard_keymap, .enter = keyboard_enter, .leave = keyboard_leave,
    .key = keyboard_key, .modifiers = keyboard_modifiers
};
static void seat_capabilities(void *data, struct wl_seat *seat, uint32_t capabilities) {
    struct probe *p = data;
    if ((capabilities & WL_SEAT_CAPABILITY_KEYBOARD) && !p->keyboard) {
        p->keyboard = wl_seat_get_keyboard(seat);
        wl_keyboard_add_listener(p->keyboard, &keyboard_listener, p);
    }
}
static const struct wl_seat_listener seat_listener = { .capabilities = seat_capabilities };
static void registry_global(void *data, struct wl_registry *registry, uint32_t name, const char *interface, uint32_t version) {
    (void)version; struct probe *p = data;
    if (!strcmp(interface, "wl_compositor")) p->compositor = wl_registry_bind(registry, name, &wl_compositor_interface, 1);
    else if (!strcmp(interface, "wl_shm")) p->shm = wl_registry_bind(registry, name, &wl_shm_interface, 1);
    else if (!strcmp(interface, "xdg_wm_base")) {
        p->wm = wl_registry_bind(registry, name, &xdg_wm_base_interface, 1);
        xdg_wm_base_add_listener(p->wm, &wm_listener, p);
    } else if (!strcmp(interface, "wp_presentation")) {
        p->presentation = wl_registry_bind(registry, name, &wp_presentation_interface, 1);
        wp_presentation_add_listener(p->presentation, &presentation_listener, p);
    } else if (!strcmp(interface, "wl_seat") && !p->seat) {
        p->seat = wl_registry_bind(registry, name, &wl_seat_interface, 1);
        wl_seat_add_listener(p->seat, &seat_listener, p);
    }
}
static void registry_remove(void *data, struct wl_registry *registry, uint32_t name) { (void)data; (void)registry; (void)name; }
static const struct wl_registry_listener registry_listener = { .global = registry_global, .global_remove = registry_remove };
static int dispatch_until(struct probe *p, uint64_t deadline, bool finish) {
    while (!p->failed && !p->closed && (!finish || !p->success)) {
        if (milliseconds() >= deadline) return 1;
        while (wl_display_prepare_read(p->display) != 0) {
            if (wl_display_dispatch_pending(p->display) < 0) return -1;
            if (p->failed || p->closed || (finish && p->success)) return 0;
        }
        if (wl_display_flush(p->display) < 0 && errno != EAGAIN) { wl_display_cancel_read(p->display); return -1; }
        struct pollfd fd = { .fd = wl_display_get_fd(p->display), .events = POLLIN };
        uint64_t left = deadline - milliseconds();
        int ready = poll(&fd, 1, (int)(left > 250 ? 250 : left));
        if (ready > 0 && (fd.revents & POLLIN)) {
            if (wl_display_read_events(p->display) < 0) return -1;
        } else {
            wl_display_cancel_read(p->display);
            if (ready < 0 && errno != EINTR) return -1;
            if (fd.revents & (POLLERR | POLLHUP | POLLNVAL)) return -1;
        }
        if (wl_display_dispatch_pending(p->display) < 0) return -1;
    }
    return 0;
}
int main(int argc, char **argv) {
    unsigned timeout = 40, hold = 0;
    struct probe p = { .expected_key = 16 };
    setvbuf(stdout, NULL, _IOLBF, 0);
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--help")) {
            puts("desktop-session-probe [--timeout SECONDS] [--hold SECONDS] [--expect-key EVDEV]\nRequires a real configured, presented xdg-toplevel, focused key press (Q=16), and presented redraw."); return 0;
        }
        if (i + 1 >= argc) return 2;
        char *end = NULL; unsigned long value = strtoul(argv[i + 1], &end, 10);
        if (!*argv[i + 1] || *end || value > 3600) return 2;
        if (!strcmp(argv[i], "--timeout")) timeout = value;
        else if (!strcmp(argv[i], "--hold")) hold = value;
        else if (!strcmp(argv[i], "--expect-key")) p.expected_key = value;
        else return 2;
        ++i;
    }
    p.display = wl_display_connect(NULL);
    if (!p.display) { fprintf(stderr, "Cannot connect to the requested Wayland session: %s\n", strerror(errno)); return 3; }
    struct wl_registry *registry = wl_display_get_registry(p.display);
    wl_registry_add_listener(registry, &registry_listener, &p);
    if (wl_display_roundtrip(p.display) < 0 || wl_display_roundtrip(p.display) < 0 ||
        !p.compositor || !p.shm || !p.wm || !p.presentation || !p.keyboard) {
        fprintf(stderr, "Required ordinary-client compositor/SHM/xdg-shell/presentation/keyboard globals missing\n"); return 4;
    }
    p.surface = wl_compositor_create_surface(p.compositor);
    p.xdg_surface = xdg_wm_base_get_xdg_surface(p.wm, p.surface);
    xdg_surface_add_listener(p.xdg_surface, &xdg_listener, &p);
    p.toplevel = xdg_surface_get_toplevel(p.xdg_surface);
    xdg_toplevel_add_listener(p.toplevel, &toplevel_listener, &p);
    xdg_toplevel_set_title(p.toplevel, "Custom Distro desktop qualification");
    xdg_toplevel_set_app_id(p.toplevel, "org.customdistro.DesktopProbe");
    xdg_toplevel_set_min_size(p.toplevel, 480, 320); xdg_toplevel_set_max_size(p.toplevel, 480, 320);
    wl_surface_commit(p.surface);
    int dispatched = dispatch_until(&p, milliseconds() + (uint64_t)timeout * 1000, true);
    if (!p.success || dispatched < 0) {
        fprintf(stderr, "Desktop qualification failed: configured=%d initial_frame=%d initial_presented=%d focus=%d key=%d redraw_frame=%d redraw_presented=%d protocol_error=%d\n",
            p.configured, p.phases[0].framed, p.phases[0].presented, p.focused, p.key_seen,
            p.phases[1].framed, p.phases[1].presented, wl_display_get_error(p.display));
        return 5;
    }
    if (hold) dispatch_until(&p, milliseconds() + (uint64_t)hold * 1000, false);
    xdg_toplevel_destroy(p.toplevel); xdg_surface_destroy(p.xdg_surface); wl_surface_destroy(p.surface);
    wl_display_flush(p.display); wl_display_disconnect(p.display);
    for (unsigned i = 0; i < 2; ++i) if (p.phases[i].pixels) munmap(p.phases[i].pixels, p.phases[i].size);
    return 0;
}
