import AppKit

enum DaemonState {
    case starting
    case active
    case paused
}

final class MenuBarController {
    private var statusItem: NSStatusItem?
    private var state: DaemonState = .starting
    private weak var manager: ObserverManager?
    private var onQuit: (() -> Void)?

    func setup(manager: ObserverManager, onQuit: @escaping () -> Void) {
        self.manager = manager
        self.onQuit = onQuit

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        updateIcon()
        buildMenu()
    }

    func setState(_ newState: DaemonState) {
        state = newState
        updateIcon()
        buildMenu()
    }

    private func updateIcon() {
        guard let button = statusItem?.button else { return }

        switch state {
        case .starting:
            button.title = "◌"
        case .active:
            button.title = "◉"
        case .paused:
            button.title = "○"
        }
    }

    private func buildMenu() {
        let menu = NSMenu()

        // Status line
        let statusText: String
        switch state {
        case .starting:
            statusText = "Starting..."
        case .active:
            let context = contextOneLiner()
            statusText = "Observing · \(context)"
        case .paused:
            statusText = "Paused"
        }
        let statusItem = NSMenuItem(title: statusText, action: nil, keyEquivalent: "")
        statusItem.isEnabled = false
        menu.addItem(statusItem)

        menu.addItem(NSMenuItem.separator())

        // Pause/Resume
        if state == .active {
            menu.addItem(NSMenuItem(title: "Pause", action: #selector(pauseClicked), keyEquivalent: "p"))
            menu.items.last?.target = self
        } else if state == .paused {
            menu.addItem(NSMenuItem(title: "Resume", action: #selector(resumeClicked), keyEquivalent: "p"))
            menu.items.last?.target = self
        }

        menu.addItem(NSMenuItem.separator())

        // Quit
        menu.addItem(NSMenuItem(title: "Quit lurk", action: #selector(quitClicked), keyEquivalent: "q"))
        menu.items.last?.target = self

        self.statusItem?.menu = menu
    }

    private func contextOneLiner() -> String {
        guard let manager = manager else { return "" }
        var parts: [String] = []
        if let app = manager.currentApp {
            parts.append(app)
        }
        if let title = manager.currentTitle {
            // Show first meaningful segment of title
            let segments = title.components(separatedBy: " — ")
            if let first = segments.first, !first.isEmpty {
                parts.append(first)
            }
        }
        return parts.joined(separator: " · ")
    }

    @objc private func pauseClicked() {
        manager?.pause()
        setState(.paused)
    }

    @objc private func resumeClicked() {
        manager?.resume()
        setState(.active)
    }

    @objc private func quitClicked() {
        onQuit?()
        NSApplication.shared.terminate(nil)
    }
}
