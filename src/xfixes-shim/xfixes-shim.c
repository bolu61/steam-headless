/*
 * XFixesGetCursorImage's pixels are unsigned long* (8 bytes/pixel on LP64),
 * but Steam's 64-bit steamui.so reads them as packed 32-bit, corrupting the
 * streamed cursor. This shim repacks the array in place before that read.
 * Writing element i at byte offset 4*i while reading from 8*i never clobbers
 * an unread element. See docs/valve-remote-play-bugs.md (Bug 3).
 *
 * LD_PRELOAD this into the Steam client process. Remove if Valve fixes the
 * underlying read -- it would then narrow an array Steam reads correctly.
 */

#define _GNU_SOURCE
#include <dlfcn.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include <X11/Xlib.h>
#include <X11/extensions/Xfixes.h>

/*
 * Only the Steam client misreads the pixel array. LD_PRELOAD is inherited, and
 * Steam forwards it into the game container as an explicit --ld-preload, so
 * games load this object too -- and there Wine reads the same array correctly
 * as unsigned longs, so narrowing it would corrupt their cursor.
 *
 * Steam sets SteamGameId/SteamAppId for a game launch and not for its own
 * client process, so that is the discriminator.
 */
static int s_bInert;

__attribute__((constructor)) static void
xfixes_narrow_init(void)
{
	s_bInert = getenv("SteamGameId") || getenv("SteamAppId");
}

XFixesCursorImage *
XFixesGetCursorImage(Display *dpy)
{
	static XFixesCursorImage *(*real_XFixesGetCursorImage)(Display *);

	if (!real_XFixesGetCursorImage) {
		real_XFixesGetCursorImage =
			(XFixesCursorImage *(*)(Display *))dlsym(RTLD_NEXT, "XFixesGetCursorImage");
		if (!real_XFixesGetCursorImage)
			return NULL;
	}

	XFixesCursorImage *image = real_XFixesGetCursorImage(dpy);

	/* No-op in games, and on ILP32 where the array is already packed. */
	if (s_bInert || sizeof(unsigned long) == 4 || !image || !image->pixels)
		return image;

	size_t count = (size_t)image->width * (size_t)image->height;
	unsigned char *base = (unsigned char *)image->pixels;

	for (size_t i = 0; i < count; i++) {
		unsigned long wide;
		uint32_t narrow;

		memcpy(&wide, base + i * sizeof(unsigned long), sizeof(wide));
		narrow = (uint32_t)wide;
		memcpy(base + i * sizeof(uint32_t), &narrow, sizeof(narrow));
	}

	return image;
}
