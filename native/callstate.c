// Prints {"mic":0|1,"cam":0|1}: whether any microphone or camera is in use by any app.
// Uses the same "running somewhere" flags that drive the orange/green menu-bar dots;
// reading them needs no microphone or camera permission.
#include <CoreAudio/CoreAudio.h>
#include <CoreMediaIO/CMIOHardware.h>
#include <stdio.h>
#include <stdlib.h>

static int mic_in_use(void) {
    AudioObjectPropertyAddress devices = {kAudioHardwarePropertyDevices, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    UInt32 size = 0;
    if (AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &devices, 0, NULL, &size) != noErr) return 0;
    AudioDeviceID *ids = malloc(size);
    AudioObjectGetPropertyData(kAudioObjectSystemObject, &devices, 0, NULL, &size, ids);
    int running = 0;
    for (UInt32 i = 0; i < size / sizeof(AudioDeviceID) && !running; i++) {
        AudioObjectPropertyAddress streams = {kAudioDevicePropertyStreams, kAudioObjectPropertyScopeInput, kAudioObjectPropertyElementMain};
        UInt32 streams_size = 0;
        if (AudioObjectGetPropertyDataSize(ids[i], &streams, 0, NULL, &streams_size) != noErr || streams_size == 0) continue;
        AudioObjectPropertyAddress busy = {kAudioDevicePropertyDeviceIsRunningSomewhere, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
        UInt32 value = 0, value_size = sizeof(value);
        if (AudioObjectGetPropertyData(ids[i], &busy, 0, NULL, &value_size, &value) == noErr && value) running = 1;
    }
    free(ids);
    return running;
}

static int cam_in_use(void) {
    CMIOObjectPropertyAddress devices = {kCMIOHardwarePropertyDevices, kCMIOObjectPropertyScopeGlobal, kCMIOObjectPropertyElementMain};
    UInt32 size = 0, used = 0;
    if (CMIOObjectGetPropertyDataSize(kCMIOObjectSystemObject, &devices, 0, NULL, &size) != noErr) return 0;
    CMIODeviceID *ids = malloc(size);
    CMIOObjectGetPropertyData(kCMIOObjectSystemObject, &devices, 0, NULL, size, &used, ids);
    int running = 0;
    for (UInt32 i = 0; i < used / sizeof(CMIODeviceID) && !running; i++) {
        CMIOObjectPropertyAddress busy = {kCMIODevicePropertyDeviceIsRunningSomewhere, kCMIOObjectPropertyScopeWildcard, kCMIOObjectPropertyElementWildcard};
        UInt32 value = 0, value_used = 0;
        if (CMIOObjectGetPropertyData(ids[i], &busy, 0, NULL, sizeof(value), &value_used, &value) == noErr && value) running = 1;
    }
    free(ids);
    return running;
}

int main(void) {
    printf("{\"mic\":%d,\"cam\":%d}\n", mic_in_use(), cam_in_use());
    return 0;
}
