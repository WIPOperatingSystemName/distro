/* Disposable guest qualification only; never installed by an OS package. */
#define _GNU_SOURCE
#include <security/pam_appl.h>
#include <crypt.h>
#include <errno.h>
#include <fcntl.h>
#include <pwd.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <unistd.h>

struct conversation_data { const char *user; char *password; unsigned prompts; };

static void clear_responses(struct pam_response *responses, int count) {
    if (!responses) return;
    for (int i = 0; i < count; ++i) {
        if (responses[i].resp) {
            explicit_bzero(responses[i].resp, strlen(responses[i].resp));
            free(responses[i].resp);
        }
    }
    free(responses);
}

static int conversation(int count, const struct pam_message **messages,
                        struct pam_response **out, void *data) {
    struct conversation_data *context = data;
    if (count <= 0 || count > PAM_MAX_NUM_MSG || !messages || !out || !context)
        return PAM_CONV_ERR;
    *out = NULL;
    struct pam_response *responses = calloc((size_t) count, sizeof(*responses));
    if (!responses) return PAM_BUF_ERR;
    for (int i = 0; i < count; ++i) {
        if (!messages[i]) { clear_responses(responses, count); return PAM_CONV_ERR; }
        const char *value = NULL;
        switch (messages[i]->msg_style) {
        case PAM_PROMPT_ECHO_OFF: value = context->password; ++context->prompts; break;
        case PAM_PROMPT_ECHO_ON: value = context->user; break;
        case PAM_ERROR_MSG: case PAM_TEXT_INFO: break;
        default: clear_responses(responses, count); return PAM_CONV_ERR;
        }
        if (value && !(responses[i].resp = strdup(value))) {
            clear_responses(responses, count); return PAM_BUF_ERR;
        }
    }
    *out = responses;
    return PAM_SUCCESS;
}

static int conversation_self_test(void) {
    char secret[] = "disposable-conversation-fixture";
    struct conversation_data data = { .user = "custom", .password = secret };
    const struct pam_message messages[] = {{PAM_PROMPT_ECHO_OFF, "password"},
        {PAM_PROMPT_ECHO_ON, "user"}, {PAM_TEXT_INFO, "info"}, {PAM_ERROR_MSG, "error"}};
    const struct pam_message *pointers[] = {messages, messages + 1, messages + 2, messages + 3};
    struct pam_response *responses = NULL;
    int result = conversation(4, pointers, &responses, &data);
    bool good = result == PAM_SUCCESS && responses && data.prompts == 1 &&
        !strcmp(responses[0].resp, secret) && !strcmp(responses[1].resp, "custom") &&
        !responses[2].resp && !responses[3].resp;
    clear_responses(responses, 4);
    const struct pam_message invalid = {999, "invalid"};
    const struct pam_message *invalid_pointer = &invalid;
    responses = NULL;
    good = good && conversation(1, &invalid_pointer, &responses, &data) == PAM_CONV_ERR && !responses;
    explicit_bzero(secret, sizeof(secret));
    if (!good) return 1;
    puts("CUSTOM_PAM_CONVERSATION_SELF_TEST_OK");
    return 0;
}

static bool exact_file(const char *path, const char *text) {
    char buffer[128];
    FILE *file = fopen(path, "re");
    if (!file) return false;
    size_t count = fread(buffer, 1, sizeof(buffer) - 1, file);
    bool good = !ferror(file) && feof(file);
    fclose(file);
    buffer[count] = 0;
    return good && !strcmp(buffer, text);
}

static bool owned_file(const char *path, bool setuid_required) {
    struct stat info;
    return lstat(path, &info) == 0 && S_ISREG(info.st_mode) && info.st_uid == 0 &&
        !(info.st_mode & 0022) && (!setuid_required || (info.st_mode & S_ISUID));
}

static bool login_policy(void) {
    if (!owned_file("/etc/pam.d/login", false)) return false;
    FILE *file = fopen("/etc/pam.d/login", "re");
    if (!file) return false;
    char line[512], type[64], control[64], module[128], extra[128];
    unsigned auth = 0, account = 0;
    bool good = true;
    while (fgets(line, sizeof(line), file)) {
        const char *start = line;
        while (*start == ' ' || *start == '\t') ++start;
        if (*start == '#' || *start == '\n') continue;
        int fields = sscanf(start, "%63s %63s %127s %127s", type, control, module, extra);
        if (fields < 3 || type[0] == '@') { good = false; break; }
        if (!strcmp(type, "auth") || !strcmp(type, "account")) {
            if (fields != 3 || strcmp(control, "required") || strcmp(module, "pam_unix.so")) {
                good = false; break;
            }
            if (!strcmp(type, "auth")) ++auth; else ++account;
        }
    }
    good = good && !ferror(file) && auth == 1 && account == 1;
    fclose(file);
    return good;
}

static bool failed_precondition(const char *name) {
    fprintf(stderr, "PAM_PRECONDITION_FAILED %s\n", name);
    return false;
}

static bool guest_preconditions(const char *user) {
    if (getuid() != 1000 || geteuid() != 1000) return failed_precondition("normal-user-uid");
    struct passwd *account = getpwuid(getuid());
    if (!account || strcmp(account->pw_name, user)) return failed_precondition("normal-user-name");
    if (!owned_file("/run/custom-distro/pam-auth-qualification", false) ||
        !exact_file("/run/custom-distro/pam-auth-qualification", "disposable-qemu-qualification\n"))
        return failed_precondition("disposable-guest-marker");
    if (!login_policy()) return failed_precondition("login-pam-unix-policy");
    if (!owned_file("/usr/sbin/unix_chkpwd", true)) return failed_precondition("root-setuid-shadow-helper");
    struct statvfs filesystem;
    if (statvfs("/usr/sbin/unix_chkpwd", &filesystem) < 0 || (filesystem.f_flag & ST_NOSUID))
        return failed_precondition("setuid-filesystem");
    if (prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 0) return failed_precondition("no-new-privileges");
    int fd = open("/etc/shadow", O_RDONLY | O_CLOEXEC);
    if (fd >= 0) { close(fd); return failed_precondition("shadow-not-readable"); }
    if (errno != EACCES && errno != EPERM) return failed_precondition("shadow-permission-denied");
    puts("CUSTOM_PAM_AUTH_PRECONDITIONS_OK");
    return true;
}

static bool read_secret(char *buffer, size_t size) {
    if (!fgets(buffer, (int) size, stdin)) return false;
    size_t length = strlen(buffer);
    if (length && buffer[length - 1] == '\n') buffer[--length] = 0;
    else if (!feof(stdin)) return false;
    return length > 0;
}

static int fixture_hash(char *password) {
    char entropy[16], salt[CRYPT_GENSALT_OUTPUT_SIZE];
    struct crypt_data *state = calloc(1, sizeof(*state));
    int result = 2;
    if (state && strlen(password) >= 16 && getrandom(entropy, sizeof(entropy), 0) == (ssize_t) sizeof(entropy) &&
        crypt_gensalt_rn("$6$", 100000, entropy, sizeof(entropy), salt, sizeof(salt))) {
        char *hash = crypt_r(password, salt, state);
        if (hash && !strncmp(hash, "$6$rounds=100000$", 17)) { puts(hash); result = 0; }
    }
    explicit_bzero(entropy, sizeof(entropy));
    explicit_bzero(salt, sizeof(salt));
    if (state) { explicit_bzero(state, sizeof(*state)); free(state); }
    return result;
}

int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "--self-test")) return conversation_self_test();
    bool hash_only = argc == 2 && !strcmp(argv[1], "--fixture-hash");
    bool deny = argc == 3 && !strcmp(argv[1], "--expect-deny");
    bool success = argc == 3 && !strcmp(argv[1], "--expect-success");
    if (!hash_only && !deny && !success) {
        fputs("usage: pam-auth-probe --expect-deny|--expect-success USER < password-file\n", stderr);
        return 2;
    }
    if (!hash_only && !guest_preconditions(argv[2])) {
        fputs("CUSTOM_PAM_AUTH_PRECONDITIONS_FAILED\n", stderr);
        return 2;
    }
    char password[512] = {0};
    if (!read_secret(password, sizeof(password))) {
        explicit_bzero(password, sizeof(password)); return 2;
    }
    if (hash_only) {
        int result = fixture_hash(password);
        explicit_bzero(password, sizeof(password)); return result;
    }
    struct conversation_data data = { .user = argv[2], .password = password };
    struct pam_conv conv = { conversation, &data };
    pam_handle_t *handle = NULL;
    int status = pam_start("login", argv[2], &conv, &handle);
    int authenticated = status;
    int account = PAM_SYSTEM_ERR;
    if (status == PAM_SUCCESS) {
        authenticated = pam_authenticate(handle, PAM_DISALLOW_NULL_AUTHTOK);
        if (authenticated == PAM_SUCCESS) account = pam_acct_mgmt(handle, PAM_DISALLOW_NULL_AUTHTOK);
    }
    printf("PAM_RESULT uid=%lu authentication=%d account=%d password_prompts=%u\n",
           (unsigned long) getuid(), authenticated, account, data.prompts);
    bool good = data.prompts > 0 && (deny ? authenticated == PAM_AUTH_ERR :
                                    authenticated == PAM_SUCCESS && account == PAM_SUCCESS);
    if (handle) pam_end(handle, authenticated);
    explicit_bzero(password, sizeof(password));
    if (!good) { fputs("CUSTOM_PAM_AUTH_RESULT_FAILED\n", stderr); return 1; }
    puts(deny ? "CUSTOM_PAM_PASSWORD_REJECT_OK" : "CUSTOM_PAM_PASSWORD_LOGIN_OK");
    return 0;
}
