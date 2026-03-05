import Foundation

/// Adaptive polling timer that adjusts intervals based on user activity.
/// Three modes: activeSwitching (rapid app changes), focused (stable work), idle (no input).
/// Uses hysteresis (3 consecutive readings) to prevent mode thrashing.
final class AdaptiveTimer {

    enum Mode: String {
        case activeSwitching  // 1.0s — rapid app switching detected
        case focused          // 5.0s — stable work in one app
        case idle             // 15.0s — no meaningful input

        var interval: TimeInterval {
            switch self {
            case .activeSwitching: return 1.0
            case .focused:         return 5.0
            case .idle:            return 15.0
            }
        }
    }

    /// Current polling interval (accounts for throttle multiplier).
    var currentInterval: TimeInterval {
        return currentMode.interval * throttleMultiplier
    }

    /// Callback fired when the effective interval changes.
    var onIntervalChanged: ((TimeInterval) -> Void)?

    private(set) var currentMode: Mode = .focused
    private var candidateMode: Mode = .focused
    private var candidateCount: Int = 0
    private let hysteresisThreshold = 3

    private var throttleMultiplier: Double = 1.0

    // App switch tracking — detect rapid switching
    private var recentSwitches: [Date] = []
    private let switchWindow: TimeInterval = 10.0  // look back 10s
    private let rapidSwitchThreshold = 3  // 3+ switches in window = rapid
    private var lastBundleId: String?

    // Input state tracking
    private var lastInputState: String = "idle"

    // MARK: - Public API

    /// Record an app switch event.
    func recordAppSwitch(bundleId: String) {
        guard bundleId != lastBundleId else { return }
        lastBundleId = bundleId

        let now = Date()
        recentSwitches.append(now)

        // Prune old entries
        let cutoff = now.addingTimeInterval(-switchWindow)
        recentSwitches.removeAll { $0 < cutoff }

        evaluateMode()
    }

    /// Record current input state from InputObserver.
    func recordInputState(_ state: String) {
        lastInputState = state
        evaluateMode()
    }

    /// Apply a throttle multiplier (used by PerformanceMonitor when CPU is high).
    func applyThrottle(multiplier: Double) {
        let old = currentInterval
        throttleMultiplier = max(1.0, multiplier)
        let new = currentInterval
        if old != new {
            onIntervalChanged?(new)
        }
    }

    /// Reset throttle back to normal.
    func resetThrottle() {
        applyThrottle(multiplier: 1.0)
    }

    // MARK: - Mode Evaluation

    private func evaluateMode() {
        let proposed = proposedMode()

        if proposed == candidateMode {
            candidateCount += 1
        } else {
            candidateMode = proposed
            candidateCount = 1
        }

        if candidateCount >= hysteresisThreshold && candidateMode != currentMode {
            let oldInterval = currentInterval
            currentMode = candidateMode
            let newInterval = currentInterval
            if oldInterval != newInterval {
                onIntervalChanged?(newInterval)
            }
        }
    }

    private func proposedMode() -> Mode {
        // Idle takes priority — no input means we can slow down
        if lastInputState == "idle" {
            return .idle
        }

        // Rapid app switching
        if recentSwitches.count >= rapidSwitchThreshold {
            return .activeSwitching
        }

        // Default: user is focused in one app
        return .focused
    }
}
