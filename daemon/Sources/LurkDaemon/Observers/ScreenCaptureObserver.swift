import AppKit
import CoreGraphics

/// Captures a screenshot of the frontmost window periodically.
/// The Python engine reads these images and uses OCR/vision to understand
/// what the user is actually doing — much richer than window titles alone.
final class ScreenCaptureObserver: Observer {
    let name = "ScreenCapture"
    private(set) var isRunning = false
    private weak var manager: ObserverManager?
    private var timer: DispatchSourceTimer?
    private let captureInterval: TimeInterval = 10.0
    private let snapshotDir: URL
    private var lastCaptureHash: Int = 0

    init(manager: ObserverManager) {
        self.manager = manager
        let lurkDir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".lurk")
        snapshotDir = lurkDir.appendingPathComponent("snapshots")
    }

    func start() {
        // Create snapshot directory
        try? FileManager.default.createDirectory(
            at: snapshotDir, withIntermediateDirectories: true
        )

        isRunning = true
        scheduleNext()
        log("Screen capture started (every \(Int(captureInterval))s)")
    }

    func stop() {
        timer?.cancel()
        timer = nil
        isRunning = false
    }

    private func scheduleNext() {
        guard isRunning else { return }

        let interval = captureInterval
        let t = DispatchSource.makeTimerSource(queue: .global(qos: .utility))
        t.schedule(deadline: .now() + interval)
        t.setEventHandler { [weak self] in
            self?.capture()
            self?.scheduleNext()
        }
        t.resume()
        timer = t
    }

    private func capture() {
        guard let frontApp = NSWorkspace.shared.frontmostApplication else { return }

        let appName = frontApp.localizedName ?? "Unknown"
        let bundleId = frontApp.bundleIdentifier ?? "unknown"
        let pid = frontApp.processIdentifier

        // Capture the frontmost window using CGWindowList
        guard let image = captureWindow(pid: pid) else {
            // Fallback: capture the main display
            guard let fallback = captureMainDisplay() else { return }
            saveAndEmit(image: fallback, app: appName, bundleId: bundleId)
            return
        }

        saveAndEmit(image: image, app: appName, bundleId: bundleId)
    }

    private func saveAndEmit(image: CGImage, app: String, bundleId: String) {
        // Quick hash check — skip if screen hasn't changed much
        let hash = roughHash(image)
        if hash == lastCaptureHash {
            return
        }
        lastCaptureHash = hash

        // Save as JPEG (smaller than PNG, good enough for OCR)
        let filename = "latest.jpg"
        let fileURL = snapshotDir.appendingPathComponent(filename)

        let dest = CGImageDestinationCreateWithURL(
            fileURL as CFURL, "public.jpeg" as CFString, 1, nil
        )
        guard let dest = dest else { return }

        // Compress to keep file small — OCR doesn't need high quality
        let options: [CFString: Any] = [
            kCGImageDestinationLossyCompressionQuality: 0.6
        ]
        CGImageDestinationAddImage(dest, image, options as CFDictionary)
        CGImageDestinationFinalize(dest)

        // Also write metadata so Python knows what app this is from
        let metaURL = snapshotDir.appendingPathComponent("latest.json")
        let meta: [String: Any] = [
            "ts": Date().timeIntervalSince1970,
            "app": app,
            "bundle_id": bundleId,
            "width": image.width,
            "height": image.height,
        ]
        if let data = try? JSONSerialization.data(withJSONObject: meta),
           let str = String(data: data, encoding: .utf8) {
            try? str.write(to: metaURL, atomically: true, encoding: .utf8)
        }

        // Emit event so enrichment pipeline knows a screenshot is available
        manager?.emit(RawEvent(
            eventType: .screenshot,
            app: app,
            bundleId: bundleId,
            data: [
                "path": fileURL.path,
                "width": image.width,
                "height": image.height,
            ]
        ))
    }

    /// Capture the frontmost window of a process.
    private func captureWindow(pid: pid_t) -> CGImage? {
        // Get all on-screen windows for this process
        guard let windowList = CGWindowListCopyWindowInfo(
            [.optionOnScreenOnly, .excludeDesktopElements],
            kCGNullWindowID
        ) as? [[String: Any]] else { return nil }

        // Find the frontmost window belonging to this PID
        for window in windowList {
            guard let ownerPID = window[kCGWindowOwnerPID as String] as? pid_t,
                  ownerPID == pid,
                  let windowID = window[kCGWindowNumber as String] as? CGWindowID,
                  let layer = window[kCGWindowLayer as String] as? Int,
                  layer == 0  // normal windows only
            else { continue }

            // Capture just this window
            if let image = CGWindowListCreateImage(
                .null,
                .optionIncludingWindow,
                windowID,
                [.boundsIgnoreFraming, .nominalResolution]
            ) {
                // Skip tiny windows (tooltips, popups)
                if image.width > 200 && image.height > 200 {
                    return image
                }
            }
        }

        return nil
    }

    /// Fallback: capture the main display.
    private func captureMainDisplay() -> CGImage? {
        return CGDisplayCreateImage(CGMainDisplayID())
    }

    /// Quick rough hash to detect if screen content changed significantly.
    /// Samples a few pixels rather than hashing the entire image.
    private func roughHash(_ image: CGImage) -> Int {
        // Sample dimensions — we don't need precision, just change detection
        let w = image.width
        let h = image.height
        var hash = w &* 31 &+ h

        // Use the data provider to sample a few bytes
        guard let data = image.dataProvider?.data,
              let ptr = CFDataGetBytePtr(data) else {
            return hash
        }

        let len = CFDataGetLength(data)
        // Sample 16 evenly spaced positions
        let step = max(1, len / 16)
        for i in stride(from: 0, to: min(len, step * 16), by: step) {
            hash = hash &* 31 &+ Int(ptr[i])
        }

        return hash
    }

    private func log(_ msg: String) {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        print("[lurk \(f.string(from: Date()))] [\(name)] \(msg)")
    }
}
