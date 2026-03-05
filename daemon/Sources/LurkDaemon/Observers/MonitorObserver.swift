import AppKit
import CoreGraphics

final class MonitorObserver: Observer {
    let name = "Monitor"
    private(set) var isRunning = false
    private weak var manager: ObserverManager?
    private var timer: DispatchSourceTimer?
    private var lastActiveMonitor: Int = -1

    init(manager: ObserverManager) {
        self.manager = manager
    }

    func start() {
        isRunning = true
        scheduleNext()
    }

    func stop() {
        timer?.cancel()
        timer = nil
        isRunning = false
    }

    /// One-shot timer that polls then re-schedules at the adaptive interval.
    private func scheduleNext() {
        guard isRunning else { return }

        let interval = manager?.adaptiveTimer.currentInterval ?? 3.0
        let timer = DispatchSource.makeTimerSource(queue: .global(qos: .utility))
        timer.schedule(deadline: .now() + interval)
        timer.setEventHandler { [weak self] in
            // Must read mouse location on main thread
            DispatchQueue.main.async {
                self?.poll()
            }
            self?.scheduleNext()
        }
        timer.resume()
        self.timer = timer
    }

    private func poll() {
        let mouseLocation = NSEvent.mouseLocation
        let screens = NSScreen.screens

        // Determine active monitor
        var activeMonitor = 0
        for (index, screen) in screens.enumerated() {
            if NSMouseInRect(mouseLocation, screen.frame, false) {
                activeMonitor = index
                break
            }
        }

        // Get visible windows across monitors
        let windows = getVisibleWindows(screens: screens)

        let windowData: [[String: Any]] = windows.map { w in
            [
                "app": w.app,
                "title": TitleSanitizer.sanitize(w.title),
                "monitor_id": w.monitorId
            ]
        }

        manager?.emit(RawEvent(
            eventType: .monitorState,
            data: [
                "active_monitor": activeMonitor,
                "monitor_count": screens.count,
                "windows": windowData
            ]
        ))

        lastActiveMonitor = activeMonitor
    }

    private func getVisibleWindows(screens: [NSScreen]) -> [MonitorWindow] {
        guard let windowInfoList = CGWindowListCopyWindowInfo(
            [.optionOnScreenOnly, .excludeDesktopElements],
            kCGNullWindowID
        ) as? [[String: Any]] else {
            return []
        }

        var result: [MonitorWindow] = []

        for info in windowInfoList {
            // Skip windows without a name or owner
            guard let ownerName = info[kCGWindowOwnerName as String] as? String,
                  let name = info[kCGWindowName as String] as? String,
                  !name.isEmpty else {
                continue
            }

            // Skip system UI elements
            let layer = info[kCGWindowLayer as String] as? Int ?? 0
            guard layer == 0 else { continue }

            // Determine which monitor this window is on
            let monitorId: Int
            if let bounds = info[kCGWindowBounds as String] as? [String: CGFloat],
               let x = bounds["X"], let y = bounds["Y"],
               let width = bounds["Width"], let height = bounds["Height"] {
                let windowCenter = CGPoint(x: x + width / 2, y: y + height / 2)
                monitorId = monitorForPoint(windowCenter, screens: screens)
            } else {
                monitorId = 0
            }

            result.append(MonitorWindow(app: ownerName, title: name, monitorId: monitorId))
        }

        return result
    }

    private func monitorForPoint(_ point: CGPoint, screens: [NSScreen]) -> Int {
        // CGWindowList uses top-left origin; NSScreen uses bottom-left
        // Convert by finding the screen whose frame contains the point
        // using CG coordinates (flip Y axis)
        let mainHeight = screens.first?.frame.height ?? 0

        for (index, screen) in screens.enumerated() {
            let frame = screen.frame
            // Convert NSScreen frame to CG coordinates
            let cgFrame = CGRect(
                x: frame.origin.x,
                y: mainHeight - frame.origin.y - frame.height,
                width: frame.width,
                height: frame.height
            )
            if cgFrame.contains(point) {
                return index
            }
        }
        return 0
    }
}
