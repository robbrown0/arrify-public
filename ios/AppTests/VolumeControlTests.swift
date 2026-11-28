import XCTest
import UIKit
import AVFoundation
import MediaPlayer
@testable import Arrify

final class VolumeControlTests: XCTestCase {
    @MainActor func testTabletAlignmentFollowsLateInsertionReplacementAndAncestorLayout() async throws {
        let container = VolumeControlContainer(volumeView: emptyVolume(), centersNativeHeight: true,
            notifications: NotificationCenter(), makeVolumeView: { self.emptyVolume() })
        let window = host(container)
        defer { container.removeFromSuperview(); window.isHidden = true }
        container.refreshIfIdle()
        try await Task.sleep(for: .milliseconds(60))
        let volume = container.volumeView
        let first = UISlider(frame: CGRect(x: 0, y: -12, width: 300, height: 30))
        first.value = 0.18
        let events = VolumeEventCounter()
        first.addTarget(events, action: #selector(VolumeEventCounter.changed), for: .valueChanged)
        volume.addSubview(first)
        let firstFrame = first.frame
        try await Task.sleep(for: .milliseconds(60))
        assertCentered(first, in: container)
        XCTAssertEqual(first.frame, firstFrame)
        first.removeFromSuperview()
        let ancestor = UIView(frame: CGRect(x: 0, y: 9, width: 300, height: 40))
        let replacement = UISlider(frame: CGRect(x: 0, y: 5, width: 300, height: 30))
        replacement.value = 0.18
        replacement.addTarget(events, action: #selector(VolumeEventCounter.changed), for: .valueChanged)
        ancestor.addSubview(replacement); volume.addSubview(ancestor)
        try await Task.sleep(for: .milliseconds(60))
        assertCentered(replacement, in: container)
        ancestor.frame.origin.y = -16
        try await Task.sleep(for: .milliseconds(60))
        assertCentered(replacement, in: container)
        XCTAssertEqual(replacement.frame.origin.y, 5, "Only the whole native view may move")
        XCTAssertEqual(first.value, 0.18, accuracy: 0.001)
        XCTAssertEqual(replacement.value, 0.18, accuracy: 0.001)
        XCTAssertEqual(events.count, 0, "Layout must never produce a receiver-volume action")
    }

    @MainActor func testTabletAlignmentSurvivesRefreshResizeAndLateLayout() async throws {
        let notifications = NotificationCenter()
        let container = VolumeControlContainer(volumeView: fixtureVolume(), centersNativeHeight: true,
            notifications: notifications, makeVolumeView: { self.fixtureVolume() })
        let window = host(container)
        defer { container.removeFromSuperview(); window.isHidden = true }
        container.refreshIfIdle()
        for size in [CGSize(width: 130, height: 44), CGSize(width: 240, height: 56)] {
            container.frame.size = size
            container.setNeedsLayout(); container.layoutIfNeeded()
            try await Task.sleep(for: .milliseconds(60))
            let previous = container.volumeView
            notifications.post(name: AVAudioSession.routeChangeNotification, object: nil)
            try await Task.sleep(for: .milliseconds(600))
            XCTAssertFalse(container.volumeView === previous)
            let slider = try XCTUnwrap(container.volumeView.viewWithTag(4647) as? UISlider)
            slider.frame.origin.y = -13
            let frame = slider.frame
            try await Task.sleep(for: .milliseconds(60))
            assertCentered(slider, in: container)
            XCTAssertEqual(slider.frame, frame)
            XCTAssertEqual(slider.value, 0.18, accuracy: 0.001)
        }
    }

    @MainActor func testTabletAlignmentStopsOnSuspendAndDetachAndResumesOnForeground() async throws {
        let notifications = NotificationCenter()
        let container = VolumeControlContainer(volumeView: fixtureVolume(), centersNativeHeight: true,
            notifications: notifications, makeVolumeView: { self.fixtureVolume() })
        let window = host(container)
        defer { container.removeFromSuperview(); window.isHidden = true }
        container.refreshIfIdle()
        try await Task.sleep(for: .milliseconds(60))
        let slider = try XCTUnwrap(container.volumeView.viewWithTag(4647) as? UISlider)
        assertCentered(slider, in: container)
        notifications.post(name: UIApplication.willResignActiveNotification, object: nil)
        try await Task.sleep(for: .milliseconds(60))
        let suspendedTransform = container.volumeView.transform
        slider.frame.origin.y = 13
        try await Task.sleep(for: .milliseconds(60))
        XCTAssertEqual(container.volumeView.transform, suspendedTransform)
        notifications.post(name: UIApplication.didBecomeActiveNotification, object: nil)
        try await Task.sleep(for: .milliseconds(600))
        let current = try XCTUnwrap(container.volumeView.viewWithTag(4647) as? UISlider)
        current.frame.origin.y = -11
        try await Task.sleep(for: .milliseconds(60))
        assertCentered(current, in: container)
        container.removeFromSuperview()
        let detachedTransform = container.volumeView.transform
        current.frame.origin.y = 9
        try await Task.sleep(for: .milliseconds(60))
        XCTAssertEqual(container.volumeView.transform, detachedTransform)
    }

    @MainActor func testPhoneVolumeGeometryIsUnchangedByLateTabletAlignmentWork() async throws {
        let container = VolumeControlContainer(volumeView: fixtureVolume(),
            notifications: NotificationCenter(), makeVolumeView: { self.fixtureVolume() })
        let window = host(container)
        defer { container.removeFromSuperview(); window.isHidden = true }
        container.refreshIfIdle()
        try await Task.sleep(for: .milliseconds(60))
        let slider = try XCTUnwrap(container.volumeView.viewWithTag(4647) as? UISlider)
        slider.frame.origin.y = -17
        try await Task.sleep(for: .milliseconds(60))
        XCTAssertEqual(container.volumeView.transform, .identity)
        XCTAssertEqual(slider.frame.origin.y, -17)
    }

    @MainActor private func emptyVolume() -> MPVolumeView {
        let view = MPVolumeView(frame: .zero)
        for child in view.subviews { child.isHidden = true }
        return view
    }

    @MainActor private func fixtureVolume() -> MPVolumeView {
        let view = emptyVolume()
        // Match the initially empty parent's width. Starting at300 inside a
        // zero-width parent makes flexibleWidth expand the fixture to600 when
        // hosted, so its center is outside the real native view's hit region.
        let slider = UISlider(frame: CGRect(x: 0, y: -8, width: view.bounds.width, height: 30))
        slider.tag = 4647; slider.value = 0.18
        slider.autoresizingMask = [.flexibleWidth]
        view.addSubview(slider)
        return view
    }

    @MainActor private func assertCentered(_ slider: UISlider, in container: UIView,
                                           file: StaticString = #filePath, line: UInt = #line) {
        let track = slider.trackRect(forBounds: slider.bounds)
        XCTAssertLessThanOrEqual(slider.frame.width, container.bounds.width,
                                 "The fixture must fit the native control", file: file, line: line)
        XCTAssertEqual(slider.convert(track, to: container).midY, container.bounds.midY,
                       accuracy: 0.5, file: file, line: line)
        let point = slider.convert(CGPoint(x: track.midX, y: track.midY), to: container)
        XCTAssertTrue(container.hitTest(point, with: nil) === slider,
                      "A touch at the visible track must reach the same slider", file: file, line: line)
    }

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
