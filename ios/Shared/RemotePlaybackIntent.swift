import AppIntents
import ActivityKit

/// Runs in the app process, using its Keychain session rather than extension credentials.
struct RemotePlaybackIntent: LiveActivityIntent {
    static var title: LocalizedStringResource = "Control Arrify playback"
    static var openAppWhenRun: Bool = false
    static var isDiscoverable: Bool = false
    @Parameter(title: "Activity") var activityID: String
    @Parameter(title: "Command") var command: String
    init() {}
    init(activityID: String, command: String) { self.activityID = activityID; self.command = command }
    @MainActor func perform() async throws -> some IntentResult {
        #if ARRIFY_APP
        await RemoteControlRuntime.perform(activityID: activityID, command: command)
        #endif
        return .result()
    }
}
