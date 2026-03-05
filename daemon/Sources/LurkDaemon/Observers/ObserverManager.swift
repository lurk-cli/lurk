import Foundation

final class ObserverManager {
    private var observers: [Observer] = []
    private let eventWriter: EventWriter
    private var isPaused = false

    // Adaptive polling timer — shared across observers
    let adaptiveTimer = AdaptiveTimer()

    // Shared state updated by observers, read by others
    private(set) var currentApp: String?
    private(set) var currentBundleId: String?
    private(set) var currentTitle: String?
    private(set) var currentInputState: InputState = .idle

    init(eventWriter: EventWriter) {
        self.eventWriter = eventWriter
    }

    func register(_ observer: Observer) {
        observers.append(observer)
    }

    func startAll() {
        for observer in observers {
            do {
                observer.start()
                log("[\(observer.name)] Started")
            }
        }
    }

    func stopAll() {
        for observer in observers {
            observer.stop()
            log("[\(observer.name)] Stopped")
        }
    }

    func pause() {
        isPaused = true
        stopAll()
        log("[ObserverManager] Paused")
    }

    func resume() {
        isPaused = false
        startAll()
        log("[ObserverManager] Resumed")
    }

    func emit(_ event: RawEvent) {
        guard !isPaused else { return }

        // Update shared state and feed adaptive timer
        switch event.eventType {
        case .appSwitch:
            currentApp = event.app
            currentBundleId = event.bundleId
            if let bundleId = event.bundleId {
                adaptiveTimer.recordAppSwitch(bundleId: bundleId)
            }
        case .titleChange:
            currentTitle = event.title
        case .inputState:
            if let stateStr = event.data?["state"] as? String,
               let state = InputState(rawValue: stateStr) {
                currentInputState = state
                adaptiveTimer.recordInputState(stateStr)
            }
        default:
            break
        }

        eventWriter.write(event)
    }

    private func log(_ message: String) {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss"
        print("[lurk \(formatter.string(from: Date()))] \(message)")
    }
}
