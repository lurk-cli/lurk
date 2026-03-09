import AppKit
import CoreGraphics

final class TitleObserver: Observer {
    let name = "Title"
    private(set) var isRunning = false
    private weak var manager: ObserverManager?
    private var timer: DispatchSourceTimer?
    private var lastTitle: String?
    private var lastApp: String?

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
            self?.poll()
            self?.scheduleNext()
        }
        timer.resume()
        self.timer = timer
    }

    private func poll() {
        guard let frontApp = NSWorkspace.shared.frontmostApplication else {
            return
        }

        let appName = frontApp.localizedName ?? "Unknown"
        let bundleId = frontApp.bundleIdentifier ?? "unknown"
        let pid = frontApp.processIdentifier

        // Use CGWindowList to get window title — only needs Screen Recording permission
        let title = getWindowTitle(pid: pid) ?? appName

        // Only emit if title or app changed
        if title != lastTitle || appName != lastApp {
            lastTitle = title
            lastApp = appName
            manager?.emit(RawEvent(
                eventType: .titleChange,
                app: appName,
                bundleId: bundleId,
                title: title
            ))
        }
    }

    private func getWindowTitle(pid: pid_t) -> String? {
        // CGWindowListCopyWindowInfo requires Screen Recording permission (not Accessibility)
        guard let windowList = CGWindowListCopyWindowInfo(
            [.optionOnScreenOnly, .excludeDesktopElements],
            kCGNullWindowID
        ) as? [[String: Any]] else {
            return nil
        }

        // Find the frontmost window owned by this PID
        for window in windowList {
            guard let ownerPID = window[kCGWindowOwnerPID as String] as? pid_t,
                  ownerPID == pid,
                  let layer = window[kCGWindowLayer as String] as? Int,
                  layer == 0  // normal windows only
            else { continue }

            if let name = window[kCGWindowName as String] as? String, !name.isEmpty {
                return name
            }
        }

        return nil
    }
}
