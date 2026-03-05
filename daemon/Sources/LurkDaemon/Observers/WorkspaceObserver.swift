import AppKit

final class WorkspaceObserver: Observer {
    let name = "Workspace"
    private(set) var isRunning = false
    private weak var manager: ObserverManager?

    init(manager: ObserverManager) {
        self.manager = manager
    }

    func start() {
        NSWorkspace.shared.notificationCenter.addObserver(
            self,
            selector: #selector(appDidActivate(_:)),
            name: NSWorkspace.didActivateApplicationNotification,
            object: nil
        )
        isRunning = true

        // Capture initial state
        if let frontApp = NSWorkspace.shared.frontmostApplication {
            emitAppSwitch(frontApp)
        }
    }

    func stop() {
        NSWorkspace.shared.notificationCenter.removeObserver(self)
        isRunning = false
    }

    @objc private func appDidActivate(_ notification: Notification) {
        guard let app = notification.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication else {
            return
        }
        emitAppSwitch(app)
    }

    private func emitAppSwitch(_ app: NSRunningApplication) {
        let appName = app.localizedName ?? "Unknown"
        let bundleId = app.bundleIdentifier ?? "unknown"

        manager?.emit(RawEvent(
            eventType: .appSwitch,
            app: appName,
            bundleId: bundleId
        ))
    }
}
