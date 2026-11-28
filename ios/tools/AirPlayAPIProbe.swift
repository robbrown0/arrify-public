// Standalone, unsigned simulator experiment. Not part of the Arrify app target.
import UIKit
import AVKit
import AVFAudio
import Network
#if canImport(AVSystemRouting)
import AVSystemRouting
#endif

@MainActor func report(_ event: String, _ extra: [String: Any] = [:]) {
    let session = AVAudioSession.sharedInstance()
    var result: [String: Any] = ["event": event, "os": UIDevice.current.systemVersion,
        "outputs": session.currentRoute.outputs.map { ["name": $0.portName, "uid": $0.uid, "type": $0.portType.rawValue] }]
    extra.forEach { result[$0.key] = $0.value }
    if let data = try? JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]),
       let text = String(data: data, encoding: .utf8) {
        print("AIRPLAY_API_PROBE " + text)
        fflush(stdout)
        let directory = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let file = directory.appendingPathComponent("probe.jsonl")
        let line = Data((text + "\n").utf8)
        if FileManager.default.fileExists(atPath: file.path), let handle = try? FileHandle(forWritingTo: file) {
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: line)
            try? handle.close()
        } else { try? line.write(to: file) }
    }
}

#if canImport(AVSystemRouting)
final class RouteObserver: NSObject, AVSystemRouteControllerObserver {
    func systemRouteController(_ controller: AVSystemRouteController, handle event: AVSystemRouteEvent) async -> Bool {
        await report("extension-route-event", ["name": event.route.routeDisplayName,
                     "symbol": event.route.routeSymbolName, "protocol": event.route.protocolType.identifier])
        return false
    }
}
#endif

@main @MainActor final class ProbeApp: UIResponder, UIApplicationDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        report("application-start", ["legacyRoute": AirPlayLegacyRouteProbe()])
        return true
    }
    func application(_ application: UIApplication, configurationForConnecting session: UISceneSession,
                     options: UIScene.ConnectionOptions) -> UISceneConfiguration {
        let configuration = UISceneConfiguration(name: "Probe", sessionRole: session.role)
        configuration.delegateClass = ProbeScene.self
        return configuration
    }
}

@MainActor final class ProbeScene: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?
    #if canImport(AVSystemRouting)
    let observer = RouteObserver()
    #endif
    let picker = AVRoutePickerView()
    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options: UIScene.ConnectionOptions) {
        guard let scene = scene as? UIWindowScene else { return }
        let controller = UIViewController()
        controller.view.backgroundColor = .systemBackground
        picker.frame = CGRect(x: 40, y: 80, width: 250, height: 60)
        controller.view.addSubview(picker)
        let window = UIWindow(windowScene: scene)
        window.rootViewController = controller
        window.makeKeyAndVisible()
        self.window = window
        #if canImport(AVSystemRouting)
        let registered = AVSystemRouteController.shared.addObserver(observer)
        report("start", ["extensionAvailable": AVSystemRouteController.supportedExtensionAvailable,
                         "observerRegistered": registered])
        #else
        report("start", ["AVSystemRoutingModuleAvailable": false])
        #endif
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [self] in
            let controls = picker.subviews.compactMap { $0 as? UIControl }.map { control in
                ["label": control.accessibilityLabel ?? "", "value": control.accessibilityValue ?? ""]
            }
            report("settled", ["pickerLabel": picker.accessibilityLabel ?? "",
                               "pickerValue": picker.accessibilityValue ?? "", "publicControlAccessibility": controls,
                               "legacyRoute": AirPlayLegacyRouteProbe()])
            #if canImport(AVSystemRouting)
            AVSystemRouteController.shared.removeObserver(observer)
            #endif
            exit(0)
        }
    }
}
