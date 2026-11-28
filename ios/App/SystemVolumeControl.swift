import SwiftUI
import AVFoundation
import MediaPlayer

/// A fresh native control reads the selected output's volume from MediaPlayer.
/// Never seed it from AVAudioSession.outputVolume, a remembered phone volume, or
/// AVPlayer.volume: those are not an individual receiver's volume authority.
final class VolumeControlContainer: UIView {
    private(set) var volumeView: MPVolumeView
    private let centersNativeHeight: Bool
    private let makeVolumeView: () -> MPVolumeView
    private let onCreate: (MPVolumeView) -> Void
    private let notifications: NotificationCenter
    private var refreshTask: Task<Void, Never>?
    private var refreshPending = false
    private var hasAttached = false
    private var alignPending = false

    init(volumeView: MPVolumeView, centersNativeHeight: Bool = false,
         notifications: NotificationCenter = .default,
         makeVolumeView: @escaping () -> MPVolumeView = VolumeControlContainer.makeNativeView,
         onCreate: @escaping (MPVolumeView) -> Void = { _ in }) {
        self.volumeView = volumeView
        self.centersNativeHeight = centersNativeHeight
        self.notifications = notifications
        self.makeVolumeView = makeVolumeView
        self.onCreate = onCreate
        super.init(frame: .zero)
        install(volumeView)
        // Selector observers do not retain the view, and UIKit delivers these
        // notifications on the main thread. Hop to the main actor in the handler
        // for notifications posted by the audio service on another thread.
        for name in [AVAudioSession.routeChangeNotification,
                     AVAudioSession.mediaServicesWereResetNotification,
                     UIApplication.didBecomeActiveNotification] {
            notifications.addObserver(self, selector: #selector(outputChanged), name: name, object: nil)
        }
        notifications.addObserver(self, selector: #selector(suspendRefresh),
                                  name: UIApplication.willResignActiveNotification, object: nil)
    }

    static func makeNativeView() -> MPVolumeView {
        let view = MPVolumeView(frame: CGRect(x: 0, y: 0, width: 300, height: 44))
        view.showsRouteButton = false
        view.tintColor = .white
        return view
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    @objc nonisolated private func outputChanged(_ notification: Notification) {
        Task { @MainActor [weak self] in self?.requestRefresh() }
    }

    @objc nonisolated private func suspendRefresh(_ notification: Notification) {
        Task { @MainActor [weak self] in
            self?.refreshTask?.cancel()
            self?.refreshTask = nil
        }
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        guard window != nil else {
            refreshTask?.cancel(); refreshTask = nil
            return
        }
        if hasAttached || refreshPending { requestRefresh() }
        hasAttached = true
        scheduleAlignment()
    }

    /// Coalesce route-negotiation bursts. Ordinary SwiftUI updates, playback
    /// ticks and changes to track titles never replace a control under a finger.
    func requestRefresh() {
        refreshPending = true
        refreshTask?.cancel(); refreshTask = nil
        guard window != nil else { return }
        if !Self.isInteracting(in: volumeView) { volumeView.isUserInteractionEnabled = false }
        refreshTask = Task { @MainActor [weak self] in
            do { try await Task.sleep(for: .milliseconds(350)) } catch { return }
            while !Task.isCancelled {
                guard let self, self.window != nil else { return }
                if self.refreshIfIdle() { return }
                // Let an active drag finish before replacing the system view.
                // No volume write occurs on either path.
                do { try await Task.sleep(for: .milliseconds(100)) } catch { return }
            }
        }
    }

    @discardableResult func refreshIfIdle() -> Bool {
        guard refreshPending else { return true }
        guard window != nil, !Self.isInteracting(in: volumeView) else { return false }
        refreshPending = false
        let focused = UIAccessibility.isVoiceOverRunning && volumeView.accessibilityElementIsFocused()
        let replacement = makeVolumeView()
        volumeView.removeFromSuperview()
        volumeView = replacement
        install(replacement)
        setNeedsLayout()
        scheduleAlignment()
        if focused { UIAccessibility.post(notification: .layoutChanged, argument: replacement) }
        return true
    }

    private func install(_ view: MPVolumeView) {
        view.frame = bounds
        view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        addSubview(view)
        onCreate(view)
    }

    private static func isInteracting(in view: UIView) -> Bool {
        if let control = view as? UIControl, control.isTracking { return true }
        return view.subviews.contains { isInteracting(in: $0) }
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        volumeView.transform = .identity
        volumeView.frame = bounds
        volumeView.layoutIfNeeded()
        alignVisibleTrack()
        scheduleAlignment()
    }

    private func scheduleAlignment() {
        guard centersNativeHeight, !alignPending else { return }
        alignPending = true
        // MediaPlayer may lay out its contents after its parent. Observe that
        // geometry on the next UI turn without subclassing MPVolumeView.
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.alignPending = false
            self.alignVisibleTrack()
        }
    }

    private func alignVisibleTrack() {
        guard centersNativeHeight else { return }
        func visibleSlider(in view: UIView) -> UISlider? {
            for child in view.subviews where !child.isHidden && child.alpha > 0 {
                if let slider = child as? UISlider { return slider }
                if let slider = visibleSlider(in: child) { return slider }
            }
            return nil
        }
        guard let slider = visibleSlider(in: volumeView) else {
            volumeView.transform = .identity
            return
        }
        let track = slider.trackRect(forBounds: slider.bounds)
        guard !track.isEmpty else { return }
        let center = slider.convert(CGPoint(x: track.midX, y: track.midY), to: volumeView)
        guard center.y.isFinite else { return }
        // Move drawing and hit testing together; never alter the slider's frame,
        // value or targets. This translation cannot accumulate across layouts.
        volumeView.transform = CGAffineTransform(translationX: 0, y: bounds.midY - center.y)
    }
}

struct SystemVolumeSlider: UIViewRepresentable {
    var centersNativeHeight = false
    var onCreate: (MPVolumeView) -> Void = { _ in }

    func makeUIView(context: Context) -> VolumeControlContainer {
        VolumeControlContainer(volumeView: VolumeControlContainer.makeNativeView(),
                               centersNativeHeight: centersNativeHeight, onCreate: onCreate)
    }
    func updateUIView(_ uiView: VolumeControlContainer, context: Context) {}
    func sizeThatFits(_ proposal: ProposedViewSize, uiView: VolumeControlContainer, context: Context) -> CGSize? {
        CGSize(width: proposal.width ?? 300, height: proposal.height ?? 44)
    }
}
