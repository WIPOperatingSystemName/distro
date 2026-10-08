/* Empty-root bootstrap helper using real libalpm transactions and database.
 * No dependency solver/database implementation lives in this project.
 * Install hooks and scriptlets are disabled by the actual libalpm flags.
 * This local development helper does not fetch packages or accept URLs.
 */
#include <alpm.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#ifndef CD_COMPILE_SYSROOT_BASE
#define CD_COMPILE_SYSROOT_BASE ""
#endif

/* Compile sysroots contain target headers/libraries, not the runtime closure.
 * This exception belongs only to the project's generated private build tree.
 * Check here as well as in Python so direct invocation cannot weaken images.
 */
static int compile_sysroot_allowed(const char *root, const char *dbpath) {
    char base[PATH_MAX], resolved_root[PATH_MAX], resolved_db[PATH_MAX];
    char expected_db[PATH_MAX];
    if(!CD_COMPILE_SYSROOT_BASE[0] || !realpath(CD_COMPILE_SYSROOT_BASE, base)
            || strcmp(base, CD_COMPILE_SYSROOT_BASE) != 0
            || !realpath(root, resolved_root) || !realpath(dbpath, resolved_db)) {
        return 0;
    }
    size_t base_length = strlen(base), root_length = strlen(resolved_root);
    if(strncmp(resolved_root, base, base_length) != 0
            || resolved_root[base_length] != '/'
            || root_length < base_length + strlen("/x/sysroot")
            || strcmp(resolved_root + root_length - strlen("/sysroot"), "/sysroot") != 0) {
        return 0;
    }
    int length = snprintf(expected_db, sizeof(expected_db), "%s/var/lib/pacman", resolved_root);
    return length > 0 && (size_t)length < sizeof(expected_db)
        && strcmp(resolved_db, expected_db) == 0;
}

static void failure(alpm_handle_t *handle, const char *operation) {
    fprintf(stderr, "%s: %s\n", operation, alpm_strerror(alpm_errno(handle)));
}

int main(int argc, char **argv) {
    int removing = argc > 1 && strcmp(argv[1], "--remove") == 0;
    int compile_sysroot = argc > 1 && strcmp(argv[1], "--compile-sysroot") == 0;
    int offset = removing || compile_sysroot ? 1 : 0;
    if(argc < 5 + offset) {
        fprintf(stderr, "usage: distro-seed-install [--remove|--compile-sysroot] ROOT DBPATH ARCH PACKAGE...\n");
        return 2;
    }
    if(compile_sysroot && !compile_sysroot_allowed(argv[1 + offset], argv[2 + offset])) {
        fprintf(stderr, "compile sysroot must be a generated project out/work directory ending in /sysroot\n");
        return 2;
    }
    alpm_errno_t err;
    alpm_handle_t *handle = alpm_initialize(argv[1 + offset], argv[2 + offset], &err);
    if(!handle) {
        fprintf(stderr, "initialize: %s\n", alpm_strerror(err));
        return 1;
    }
    alpm_option_add_architecture(handle, argv[3 + offset]);
    int flags = ALPM_TRANS_FLAG_NOSCRIPTLET | ALPM_TRANS_FLAG_NOHOOKS;
    if(compile_sysroot) {
        flags |= ALPM_TRANS_FLAG_NODEPS;
    }
    if(alpm_trans_init(handle, flags) < 0) {
        failure(handle, "transaction init");
        alpm_release(handle);
        return 1;
    }
    int result = 1;
    for(int index = 4 + offset; index < argc; index++) {
        alpm_pkg_t *package = NULL;
        if(removing) {
            package = alpm_db_get_pkg(alpm_get_localdb(handle), argv[index]);
            if(!package || alpm_remove_pkg(handle, package) < 0) {
                fprintf(stderr, "remove package: %s is not installed or cannot be removed\n", argv[index]);
                goto done;
            }
            continue;
        }
        /* Local development input; Python enforces its locked SHA-256 digest.
         * Signed/networked public updates must go through configured pacman.
         */
        if(alpm_pkg_load(handle, argv[index], 1, 0, &package) < 0) {
            failure(handle, "load package");
            goto done;
        }
        if(alpm_add_pkg(handle, package) < 0) {
            failure(handle, "add package");
            alpm_pkg_free(package);
            goto done;
        }
    }
    alpm_list_t *data = NULL;
    if(alpm_trans_prepare(handle, &data) < 0) {
        failure(handle, "prepare (dependency/conflict checks)");
        /* Process is short-lived; error payload types vary by errno. */
        goto done;
    }
    if(alpm_trans_commit(handle, &data) < 0) {
        failure(handle, "commit");
        goto done;
    }
    result = 0;
done:
    alpm_trans_release(handle);
    alpm_release(handle);
    return result;
}
