import AppKit
import CoreGraphics

/// Captures screenshots of ALL connected displays periodically.
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

        // Enumerate all connected displays
        var displayCount: UInt32 = 0
        CGGetActiveDisplayList(0, nil, &displayCount)
        guard displayCount > 0 else { return }

        var displayIDs = [CGDirectDisplayID](repeating: 0, count: Int(displayCount))
        CGGetActiveDisplayList(displayCount, &displayIDs, &displayCount)

        // Determine which display the frontmost window is on
        let activeDisplayIndex = findActiveDisplay(pid: pid, displays: displayIDs)

        // Capture each display
        var displayMetas: [[String: Any]] = []
        var capturedImages: [(index: Int, image: CGImage)] = []
        let primaryID = CGMainDisplayID()

        for (i, displayID) in displayIDs.enumerated() {
            guard let image = CGDisplayCreateImage(displayID) else { continue }

            let isPrimary = (displayID == primaryID)
            var meta: [String: Any] = [
                "display_id": i,
                "width": image.width,
                "height": image.height,
                "is_primary": isPrimary,
            ]

            // Tag the active display with app info
            if i == activeDisplayIndex {
                meta["app"] = appName
                meta["bundle_id"] = bundleId
            }

            displayMetas.append(meta)
            capturedImages.append((index: i, image: image))
        }

        guard !capturedImages.isEmpty else { return }

        // Combined hash across all displays for change detection
        var combinedHash = 0
        for (_, image) in capturedImages {
            combinedHash = combinedHash &* 31 &+ roughHash(image)
        }
        if combinedHash == lastCaptureHash {
            return
        }
        lastCaptureHash = combinedHash

        // Save each display as latest_N.jpg
        let jpegOptions: [CFString: Any] = [
            kCGImageDestinationLossyCompressionQuality: 0.6
        ]

        for (index, image) in capturedImages {
            let fileURL = snapshotDir.appendingPathComponent("latest_\(index).jpg")
            if let dest = CGImageDestinationCreateWithURL(
                fileURL as CFURL, "public.jpeg" as CFString, 1, nil
            ) {
                CGImageDestinationAddImage(dest, image, jpegOptions as CFDictionary)
                CGImageDestinationFinalize(dest)
            }

            // Backward compat: also save active display as latest.jpg
            if index == activeDisplayIndex {
                let compatURL = snapshotDir.appendingPathComponent("latest.jpg")
                if let dest = CGImageDestinationCreateWithURL(
                    compatURL as CFURL, "public.jpeg" as CFString, 1, nil
                ) {
                    CGImageDestinationAddImage(dest, image, jpegOptions as CFDictionary)
                    CGImageDestinationFinalize(dest)
                }
            }
        }

        // Write metadata with all displays
        let metaURL = snapshotDir.appendingPathComponent("latest.json")
        let meta: [String: Any] = [
            "ts": Date().timeIntervalSince1970,
            "displays": displayMetas,
            "active_display": activeDisplayIndex,
            "app": appName,
            "bundle_id": bundleId,
        ]
        if let data = try? JSONSerialization.data(withJSONObject: meta),
           let str = String(data: data, encoding: .utf8) {
            try? str.write(to: metaURL, atomically: true, encoding: .utf8)
        }

        // Emit event so enrichment pipeline knows screenshots are available
        let activeImage = capturedImages.first { $0.index == activeDisplayIndex }
        manager?.emit(RawEvent(
            eventType: .screenshot,
            app: appName,
            bundleId: bundleId,
            data: [
                "path": snapshotDir.appendingPathComponent("latest.jpg").path,
                "width": activeImage?.image.width ?? 0,
                "height": activeImage?.image.height ?? 0,
                "display_count": capturedImages.count,
                "active_display": activeDisplayIndex,
            ]
        ))
    }

    /// Determine which display the frontmost window is on by comparing
    /// the window's bounds against each display's bounds.
    private func findActiveDisplay(pid: pid_t, displays: [CGDirectDisplayID]) -> Int {
        // Get the frontmost window's bounds
        guard let windowList = CGWindowListCopyWindowInfo(
            [.optionOnScreenOnly, .excludeDesktopElements],
            kCGNullWindowID
        ) as? [[String: Any]] else { return 0 }

        var windowBounds: CGRect?
        for window in windowList {
            guard let ownerPID = window[kCGWindowOwnerPID as String] as? pid_t,
                  ownerPID == pid,
                  let layer = window[kCGWindowLayer as String] as? Int,
                  layer == 0,
                  let boundsDict = window[kCGWindowBounds as String] as? [String: Any],
                  let rect = CGRect(dictionaryRepresentation: boundsDict as CFDictionary)
            else { continue }

            // Use the first normal-layer window we find
            if rect.width > 200 && rect.height > 200 {
                windowBounds = rect
                break
            }
        }

        guard let wBounds = windowBounds else { return 0 }

        // Find the display with the most overlap
        let windowCenter = CGPoint(x: wBounds.midX, y: wBounds.midY)
        for (i, displayID) in displays.enumerated() {
            let displayBounds = CGDisplayBounds(displayID)
            if displayBounds.contains(windowCenter) {
                return i
            }
        }

        return 0  // Default to first display
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
