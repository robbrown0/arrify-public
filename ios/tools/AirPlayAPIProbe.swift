// Standalone, unsigned simulator experiment. Not part of the Arrify app target.
import UIKit
import AVKit
import AVFAudio
import Network
import AVSystemRouting

@MainActor func report(_ event: String, _ extra: [String: Any] = [:]) {
    let session = AVAudioSession.sharedInstance()
    var result: [String: Any] = ["event": event, "os": UIDevice.current.systemVersion,
        "outputs": session.currentRoute.outputs.map { ["name": $0.portName, "uid": $0.uid, "type": $0.portType.rawValue] }]
    extra.forEach { result[$0.key] = $0.value }
    if let data = try? JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]),
       let text = String(data: data, encoding: .utf8) {
        print("AIRPLAY_API_PROBE " + text)
        fflush(stdout)
    }
}

final class RouteObserver: NSObject, AVSystemRouteControllerObserver {
    func systemRouteController(_ controller: AVSystemRouteController, handle event: AVSystemRouteEvent) async -> Bool {
        await report("extension-route-event", ["name": event.route.routeDisplayName,
                     "symbol": event.route.routeSymbolName, "protocol": event.route.protocolType.identifier])
        return false
    }
}

@main @MainActor final class ProbeApp: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    let observer = RouteObserver()
    let picker = AVRoutePickerView()
    func application(_ application: UIApplication, didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        let controller = UIViewController()
        controller.view.backgroundColor = .systemBackground
        picker.frame = CGRect(x: 40, y: 80, width: 250, height: 60)
        controller.view.addSubview(picker)
        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = controller
        window.makeKeyAndVisible()
        self.window = window
        let registered = AVSystemRouteController.shared.addObserver(observer)
        report("start", ["extensionAvailable": AVSystemRouteController.supportedExtensionAvailable,
                         "observerRegistered": registered])
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [self] in
            let controls = picker.subviews.compactMap { $0 as? UIControl }.map { control in
                ["label": control.accessibilityLabel ?? "", "value": control.accessibilityValue ?? ""]
            }
            report("settled", ["pickerLabel": picker.accessibilityLabel ?? "",
                               "pickerValue": picker.accessibilityValue ?? "", "publicControlAccessibility": controls,
                               "extensionAvailable": AVSystemRouteController.supportedExtensionAvailable])
            AVSystemRouteController.shared.removeObserver(observer)
            exit(0)
        }
        return true
    }
}
