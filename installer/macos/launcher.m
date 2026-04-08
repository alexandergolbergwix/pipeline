/*
 * MHM Pipeline — Native macOS Application Launcher
 *
 * Uses NSProcessInfo and proper Cocoa integration to ensure the app
 * registers with the WindowServer before exec'ing Python.
 *
 * Build:
 *   clang -o "MHM Pipeline" launcher.c -framework Cocoa -O2
 */

#import <Cocoa/Cocoa.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>
#include <mach-o/dyld.h>

#define MAX_PATH 4096

static int dir_exists(const char *path) {
    struct stat st;
    return stat(path, &st) == 0 && S_ISDIR(st.st_mode);
}

static int file_exists(const char *path) {
    struct stat st;
    return stat(path, &st) == 0 && (S_ISREG(st.st_mode) || S_ISLNK(st.st_mode));
}

int main(int argc, char *argv[]) {
    @autoreleasepool {
        char exe_path[MAX_PATH];
        uint32_t size = MAX_PATH;

        if (_NSGetExecutablePath(exe_path, &size) != 0) {
            fprintf(stderr, "MHM Pipeline: cannot resolve executable path\n");
            return 1;
        }

        /* Resolve symlinks and get real path */
        char *real_exe = realpath(exe_path, NULL);
        if (!real_exe) real_exe = exe_path;

        /* Navigate: MacOS/exe -> Contents/ -> Resources/ */
        NSString *exePath = [NSString stringWithUTF8String:real_exe];
        NSString *macosDir = [exePath stringByDeletingLastPathComponent];
        NSString *contentsDir = [macosDir stringByDeletingLastPathComponent];
        NSString *resourcesDir = [contentsDir stringByAppendingPathComponent:@"Resources"];
        NSString *pipelineDir = [resourcesDir stringByAppendingPathComponent:@"pipeline"];
        NSString *pythonBin = [pipelineDir stringByAppendingPathComponent:@".venv/bin/python"];
        NSString *modelsDir = [resourcesDir stringByAppendingPathComponent:@"models"];

        if (real_exe != exe_path) free(real_exe);

        /* Check Python exists */
        if (![[NSFileManager defaultManager] fileExistsAtPath:pythonBin]) {
            fprintf(stderr, "MHM Pipeline: Python not found at %s\n",
                    [pythonBin UTF8String]);
            return 1;
        }

        /* Set environment variables */
        NSMutableDictionary *env = [[[NSProcessInfo processInfo] environment] mutableCopy];

        /* PYTHONPATH */
        NSString *srcDir = [pipelineDir stringByAppendingPathComponent:@"src"];
        NSString *pythonpath = [NSString stringWithFormat:@"%@:%@", srcDir, pipelineDir];
        [env setObject:pythonpath forKey:@"PYTHONPATH"];

        /* Bundled NER model */
        NSString *nerModel = [modelsDir stringByAppendingPathComponent:
                              @"hebrew-manuscript-joint-ner-v2"];
        if (dir_exists([nerModel UTF8String])) {
            [env setObject:nerModel forKey:@"MHM_BUNDLED_NER_MODEL"];
        }

        /* Bundled DictaBERT */
        NSString *dictabert = [modelsDir stringByAppendingPathComponent:@"dictabert"];
        if (dir_exists([dictabert UTF8String])) {
            [env setObject:dictabert forKey:@"MHM_BUNDLED_DICTABERT"];
        }

        /* Bundled Provenance NER model */
        NSString *provModel = [modelsDir stringByAppendingPathComponent:
                               @"provenance_ner_model.pt"];
        if (file_exists([provModel UTF8String])) {
            [env setObject:provModel forKey:@"MHM_BUNDLED_PROVENANCE_MODEL"];
        }

        /* Bundled Contents NER model */
        NSString *contModel = [modelsDir stringByAppendingPathComponent:
                               @"contents_ner_model.pt"];
        if (file_exists([contModel UTF8String])) {
            [env setObject:contModel forKey:@"MHM_BUNDLED_CONTENTS_MODEL"];
        }

        /* Launch the app via NSTask so it inherits the proper GUI context.
         * first_run_done is handled in app.py via MHM_BUNDLED_NER_MODEL env var. */
        NSTask *appTask = [[NSTask alloc] init];
        [appTask setExecutableURL:[NSURL fileURLWithPath:pythonBin]];

        NSMutableArray *args = [NSMutableArray arrayWithObjects:@"-m", @"mhm_pipeline.app", nil];
        for (int i = 1; i < argc; i++) {
            [args addObject:[NSString stringWithUTF8String:argv[i]]];
        }
        [appTask setArguments:args];
        [appTask setCurrentDirectoryURL:[NSURL fileURLWithPath:pipelineDir]];
        [appTask setEnvironment:env];

        NSError *error = nil;
        if (![appTask launchAndReturnError:&error]) {
            fprintf(stderr, "MHM Pipeline: failed to launch Python: %s\n",
                    [[error localizedDescription] UTF8String]);
            return 1;
        }

        /* Wait for the Python process to exit */
        [appTask waitUntilExit];
        return [appTask terminationStatus];
    }
}
