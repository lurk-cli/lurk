import AppKit
import ApplicationServices

final class TitleObserver: Observer {
    let name = "Title"
    private(set) var isRunning = false
    private weak var manager: ObserverManager?
    private var timer: DispatchSourceTimer?
    private let axTimeout: TimeInterval = 0.5
    private let axQueue = DispatchQueue(label: "com.lurk.ax", qos: .utility)
    private var lastTitle: String?
    private var lastApp: String?
    private let healthTracker = AppHealthTracker()

    init(manager: ObserverManager) {
        self.manager = manager
    }

    func start() {
        guard AXIsProcessTrusted() else {
            print("[lurk] Accessibility permission not granted — title capture disabled")
            return
        }

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
        guard let frontApp = NSWorkspace.shared.frontmostApplication,
              let pid = Optional(frontApp.processIdentifier) else {
            return
        }

        let appName = frontApp.localizedName ?? "Unknown"
        let bundleId = frontApp.bundleIdentifier ?? "unknown"

        // Check health tracker — skip if app is in backoff
        guard healthTracker.shouldPoll(bundleId: bundleId) else {
            return
        }

        // Run AX call on dedicated queue with timeout
        var title: String?
        let semaphore = DispatchSemaphore(value: 0)

        axQueue.async {
            title = self.getWindowTitle(pid: pid)
            semaphore.signal()
        }

        let result = semaphore.wait(timeout: .now() + axTimeout)
        if result == .timedOut {
            healthTracker.recordTimeout(bundleId: bundleId, appName: appName)
            return
        }

        // AX call succeeded — reset backoff
        healthTracker.recordSuccess(bundleId: bundleId)

        guard let capturedTitle = title else { return }

        // Only emit if title or app changed
        if capturedTitle != lastTitle || appName != lastApp {
            lastTitle = capturedTitle
            lastApp = appName
            manager?.emit(RawEvent(
                eventType: .titleChange,
                app: appName,
                bundleId: bundleId,
                title: capturedTitle
            ))
        }
    }

    private func getWindowTitle(pid: pid_t) -> String? {
        let appRef = AXUIElementCreateApplication(pid)

        var windowValue: CFTypeRef?
        let windowResult = AXUIElementCopyAttributeValue(
            appRef,
            kAXFocusedWindowAttribute as CFString,
            &windowValue
        )
        guard windowResult == .success, let window = windowValue else {
            return nil
        }

        var titleValue: CFTypeRef?
        let titleResult = AXUIElementCopyAttributeValue(
            window as! AXUIElement,
            kAXTitleAttribute as CFString,
            &titleValue
        )
        guard titleResult == .success, let title = titleValue as? String else {
            return nil
        }

        return title
    }
}
