/*
 * Diagnostic-only LD_PRELOAD shim: logs every wlr_region_confine() call and
 * its result, then calls through unchanged. Used to test (and rule out) the
 * hypothesis that sway's pointer_motion() silently drops confined-drag
 * motion whenever the tracked start point falls marginally outside the
 * confine region.
 *
 * DISPLAY=:0 LD_PRELOAD=./build/libregion-confine-trace.so sway ...
 */

#define _GNU_SOURCE
#include <dlfcn.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

typedef struct pixman_region32 pixman_region32_t;

typedef _Bool (*wlr_region_confine_fn)(pixman_region32_t *region, double x1,
	double y1, double x2, double y2, double *x2_out, double *y2_out);

static FILE *
log_file(void)
{
	static FILE *f;
	if (!f) {
		const char *path = getenv("REGION_CONFINE_TRACE_LOG");
		f = fopen(path ? path : "/tmp/region-confine-trace.log", "a");
		if (f)
			setvbuf(f, NULL, _IOLBF, 0);
	}
	return f;
}

_Bool
wlr_region_confine(pixman_region32_t *region, double x1, double y1,
	double x2, double y2, double *x2_out, double *y2_out)
{
	static wlr_region_confine_fn real_fn;
	if (!real_fn)
		real_fn = (wlr_region_confine_fn)dlsym(RTLD_NEXT, "wlr_region_confine");

	_Bool ok = real_fn(region, x1, y1, x2, y2, x2_out, y2_out);

	FILE *f = log_file();
	if (f) {
		struct timespec ts;
		clock_gettime(CLOCK_MONOTONIC, &ts);
		if (ok) {
			fprintf(f, "%ld.%06ld ok    start=(%.4f,%.4f) req=(%.4f,%.4f) out=(%.4f,%.4f)\n",
				(long)ts.tv_sec, ts.tv_nsec / 1000, x1, y1, x2, y2, *x2_out, *y2_out);
		} else {
			fprintf(f, "%ld.%06ld DROPPED start=(%.4f,%.4f) floor_start=(%.0f,%.0f) req=(%.4f,%.4f)\n",
				(long)ts.tv_sec, ts.tv_nsec / 1000, x1, y1, floor(x1), floor(y1), x2, y2);
		}
	}

	return ok;
}
