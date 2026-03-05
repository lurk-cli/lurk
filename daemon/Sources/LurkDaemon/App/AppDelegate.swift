import AppKit
import ApplicationServices

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var database: Database?
    private var eventWriter: EventWriter?
    private var observerManager: ObserverManager?
    private var socketServer: SocketServer?
    private let menuBar = MenuBarController()

    func applicationDidFinishLaunching(_ notification: Notification) {
        print("[lurk] Starting lurk-daemon...")

        // Initialize storage
        do {
            let db = try Database()
            database = db
            eventWriter = EventWriter(database: db)
            print("[lurk] Database ready at ~/.lurk/store.db")
        } catch {
            print("[lurk] FATAL: Failed to initialize database: \(error)")
            NSApplication.shared.terminate(nil)
            return
        }

        guard let writer = eventWriter else { return }

        // Initialize observer manager
        let manager = ObserverManager(eventWriter: writer)
        observerManager = manager

        // Set up menu bar
        menuBar.setup(manager: manager) { [weak self] in
            self?.shutdown()
        }

        // Register observers
        manager.register(WorkspaceObserver(manager: manager))
        manager.register(TitleObserver(manager: manager))
        manager.register(InputObserver(manager: manager))
        manager.register(MonitorObserver(manager: manager))
        manager.register(CalendarObserver(manager: manager))
        manager.register(PerformanceMonitor(manager: manager))

        // Check accessibility permission
        checkAccessibility()

        // Start socket server for IPC
        let socket = SocketServer()
        socket.start()
        socketServer = socket

        // Start all observers
        manager.startAll()
        menuBar.setState(.active)

        print("[lurk] Observer started — capturing context")
        print("[lurk] MCP server ready (use 'lurk serve-mcp' to connect)")
    }

    func applicationWillTerminate(_ notification: Notification) {
        shutdown()
    }

    private func shutdown() {
        observerManager?.stopAll()
        socketServer?.stop()
        eventWriter?.flush()
        print("[lurk] Daemon stopped")
    }

    private func checkAccessibility() {
        if !AXIsProcessTrusted() {
            print("[lurk] Accessibility permission required for window title capture.")
            print("[lurk] → Opening System Settings > Privacy & Security > Accessibility")

            // Prompt for access — this opens System Settings
            let options = [kAXTrustedCheckOptionPrompt.takeRetainedValue(): true] as CFDictionary
            AXIsProcessTrustedWithOptions(options)

            // Poll until granted
            DispatchQueue.global(qos: .utility).async {
                while !AXIsProcessTrusted() {
                    Thread.sleep(forTimeInterval: 2.0)
                }
                print("[lurk] ✓ Accessibility permission granted")
                DispatchQueue.main.async { [weak self] in
                    // Restart observers that need AX
                    self?.observerManager?.stopAll()
                    self?.observerManager?.startAll()
                }
            }
        } else {
            print("[lurk] ✓ Accessibility permission granted")
        }
    }
}
