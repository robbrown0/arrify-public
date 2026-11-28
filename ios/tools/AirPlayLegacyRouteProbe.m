// Read-only comparison with the deprecated C route API, never activation/routing.
#import "AirPlayLegacyRouteProbe.h"
#import <AudioToolbox/AudioSession.h>

NSDictionary *AirPlayLegacyRouteProbe(void) {
    CFDictionaryRef route = NULL;
    UInt32 size = sizeof(route);
    OSStatus status = AudioSessionGetProperty(kAudioSessionProperty_AudioRouteDescription, &size, &route);
    NSMutableDictionary *result = [@{@"status": @(status)} mutableCopy];
    if (status == noErr && route != NULL) {
        NSDictionary *dictionary = CFBridgingRelease(route);
        result[@"route"] = [NSJSONSerialization isValidJSONObject:dictionary] ? dictionary : dictionary.description;
    }
    return result;
}
