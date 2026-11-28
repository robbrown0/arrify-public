import XCTest
import UIKit
import AVFoundation
import MediaPlayer
@testable import Arrify

final class VolumeControlTests: XCTestCase {
    @MainActor func testFirstPresentationRefreshesAfterWindowAttachment() async throws {
        let original = MPVolumeView(frame: .zero)
        let container = VolumeControlContainer(volumeView: original, notifications: NotificationCenter())
        XCTAssertNil(container.window)
        let window = host(container)
        defer { window.isHidden = true }
        XCTAssertFalse(original.isUserInteractionEnabled, "Do not accept a first touch while binding to the output")
        try await Task.sleep(for: .milliseconds(600))
        XCTAssertFalse(container.volumeView === original)
        XCTAssertTrue(container.volumeView.isUserInteractionEnabled)
    }

    @MainActor func testRouteChangeRefreshesNativeControlAndCoalescesNegotiation() async throws {
        let notifications = NotificationCenter()
        let original = MPVolumeView(frame: .zero)
        var presentations: [MPVolumeView] = []
        let container = VolumeControlContainer(volumeView: original, notifications: notifications,
                                               onCreate: { presentations.append($0) })
        let window = host(container)
        defer { window.isHidden = true }
        XCTAssertEqual(presentations.count, 1)
        for _ in 0..<10 {
            notifications.post(name: AVAudioSession.routeChangeNotification, object: nil)
        }
        try await Task.sleep(for: .milliseconds(600))
        XCTAssertEqual(presentations.count, 2, "One fresh native view for a burst of route changes")
        XCTAssertFalse(container.volumeView === original)
        XCTAssertNil(original.superview)
        XCTAssertTrue(container.volumeView.isUserInteractionEnabled)
        // Playback metadata/layout ticks must not recreate the native control.
        let settled = container.volumeView
        for width in [130.0, 300.0, 240.0] {
            container.frame.size.width = width
            container.setNeedsLayout(); container.layoutIfNeeded()
        }
        XCTAssertTrue(container.volumeView === settled)
    }

    @MainActor func testForegroundAndMediaResetRefreshEvenWhenRouteNameDidNotChange() async throws {
        let notifications = NotificationCenter()
        let container = VolumeControlContainer(volumeView: MPVolumeView(frame: .zero), notifications: notifications)
        let window = host(container)
        defer { window.isHidden = true }
        for name in [UIApplication.didBecomeActiveNotification, AVAudioSession.mediaServicesWereResetNotification] {
            let previous = container.volumeView
            notifications.post(name: name, object: nil)
            try await Task.sleep(for: .milliseconds(600))
            XCTAssertFalse(container.volumeView === previous)
        }
    }

    @MainActor func testRefreshWaitsForDragAndNeverCopiesOrWritesVolume() {
        let original = MPVolumeView(frame: .zero)
        let finger = TrackingVolumeSlider(frame: CGRect(x: 0, y: 0, width: 130, height: 30))
        finger.value = 0.8; finger.simulatedTracking = true
        original.addSubview(finger)
        let receiverSlider = UISlider(frame: finger.frame)
        receiverSlider.value = 0.18
        let events = VolumeEventCounter()
        receiverSlider.addTarget(events, action: #selector(VolumeEventCounter.changed), for: .valueChanged)
        let container = VolumeControlContainer(volumeView: original, makeVolumeView: {
            let view = MPVolumeView(frame: .zero)
            view.addSubview(receiverSlider)
            return view
        })
        let window = host(container)
        defer { window.isHidden = true }
        // The initial mount already scheduled a refresh while the finger was
        // down; it must leave this in-progress gesture alone as well.
        container.requestRefresh()
        XCTAssertFalse(container.refreshIfIdle())
        XCTAssertTrue(container.volumeView === original, "Do not replace a control during a drag")
        XCTAssertTrue(original.isUserInteractionEnabled)
        finger.simulatedTracking = false
        XCTAssertTrue(container.refreshIfIdle())
        XCTAssertFalse(container.volumeView === original)
        XCTAssertEqual(receiverSlider.value, 0.18, accuracy: 0.001, "Never copy the old route's slider value")
        XCTAssertEqual(events.count, 0, "Opening/refreshing must not send a volume command")
    }

    @MainActor func testDetachedControlRefreshesOnReattachmentAndDoesNotRetainItself() async throws {
        let notifications = NotificationCenter()
        var container: VolumeControlContainer? = VolumeControlContainer(
            volumeView: MPVolumeView(frame: .zero), notifications: notifications)
        weak var weakContainer = container
        let window = host(try XCTUnwrap(container))
        let original = try XCTUnwrap(container).volumeView
        container?.removeFromSuperview()
        notifications.post(name: AVAudioSession.routeChangeNotification, object: nil)
        try await Task.sleep(for: .milliseconds(450))
        XCTAssertTrue(container?.volumeView === original, "Detached controls do not churn")
        window.rootViewController?.view.addSubview(try XCTUnwrap(container))
        try await Task.sleep(for: .milliseconds(600))
        XCTAssertFalse(container?.volumeView === original)
        container?.requestRefresh()
        container?.removeFromSuperview()
        container = nil
        try await Task.sleep(for: .milliseconds(20))
        XCTAssertNil(weakContainer, "Notifications and pending refreshes must not retain a dismissed player")
        window.isHidden = true
    }

    @MainActor private func host(_ view: UIView) -> UIWindow {
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        window.rootViewController = UIViewController()
        window.isHidden = false
        view.frame = CGRect(x: 20, y: 100, width: 300, height: 44)
        window.rootViewController?.view.addSubview(view)
        view.layoutIfNeeded()
        return window
    }
}

@MainActor private final class TrackingVolumeSlider: UISlider {
    var simulatedTracking = false
    override var isTracking: Bool { simulatedTracking }
}

@MainActor private final class VolumeEventCounter: NSObject {
    var count = 0
    @objc func changed() { count += 1 }
}
