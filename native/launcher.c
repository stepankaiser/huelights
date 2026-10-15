// Main executable of HueLights.app. It starts the Python daemon as a child process, so macOS
// attributes the daemon's Bluetooth and Calendar use to HueLights.app (which declares them in
// its Info.plist) instead of killing it like a bare Python process.
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>

extern char **environ;
static pid_t child;

static void forward(int sig) {
    if (child > 0) kill(child, sig);
}

int main(void) {
    char python[2048], script[2048];
    const char *home = getenv("HOME");
    snprintf(python, sizeof python, "%s/Library/Application Support/HueLights/venv/bin/python", home);
    snprintf(script, sizeof script, "%s/Library/Application Support/HueLights/lightd.py", home);
    char *argv[] = {python, "-u", script, NULL};
    signal(SIGTERM, forward);
    signal(SIGINT, forward);
    if (posix_spawn(&child, python, NULL, NULL, argv, environ) != 0) {
        perror("posix_spawn");
        return 1;
    }
    int status = 0;
    while (waitpid(child, &status, 0) < 0) {}
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
