/*
 * Dumps the current X cursor's raw bytes as 32-bit words; compare with and
 * without libxfixes-shim.so preloaded. Cursor is 1x1 while hidden, so run
 * it against something with a visible pointer.
 *
 *   DISPLAY=:0 ./build/probe
 *   DISPLAY=:0 LD_PRELOAD=./build/libxfixes-shim.so ./build/probe
 */

#include <stdio.h>
#include <stdint.h>
#include <string.h>

#include <X11/Xlib.h>
#include <X11/extensions/Xfixes.h>

#define MAX_WORDS 16

int
main(void)
{
	Display *dpy = XOpenDisplay(NULL);
	if (!dpy) {
		fprintf(stderr, "cannot open display (set DISPLAY)\n");
		return 1;
	}

	int event_base, error_base;
	if (!XFixesQueryExtension(dpy, &event_base, &error_base)) {
		fprintf(stderr, "no XFixes extension\n");
		return 1;
	}

	XFixesCursorImage *image = XFixesGetCursorImage(dpy);
	if (!image) {
		fprintf(stderr, "no cursor image\n");
		return 1;
	}

	size_t count = (size_t)image->width * (size_t)image->height;

	printf("size=%ux%u hot=%u,%u serial=%lu sizeof(long)=%zu pixels=%zu\n",
	       image->width, image->height, image->xhot, image->yhot,
	       image->cursor_serial, sizeof(unsigned long), count);

	/* Stay inside the allocation: it holds count elements, not count words. */
	size_t words = count * sizeof(unsigned long) / sizeof(uint32_t);
	if (words > MAX_WORDS)
		words = MAX_WORDS;

	for (size_t i = 0; i < words; i++) {
		uint32_t w;
		memcpy(&w, (const unsigned char *)image->pixels + i * sizeof(uint32_t), sizeof(w));
		printf("  [%2zu] %08x%s", i, w, (i % 4 == 3) ? "\n" : "");
	}
	if (words % 4)
		printf("\n");

	return 0;
}
