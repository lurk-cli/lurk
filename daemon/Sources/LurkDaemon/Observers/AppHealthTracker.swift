import Foundation

/// Tracks per-app AXUIElement health to back off from apps that repeatedly timeout.
/// After 3 consecutive timeouts, exponential backoff: skip 3→9→27 polls (capped at 30).
final class AppHealthTracker {

    struct AppHealth {
        var consecutiveTimeouts: Int = 0
        var skipUntilPoll: Int = 0      // skip this many polls before retrying
        var pollsSinceBackoff: Int = 0  // counts polls while in backoff
        var totalTimeouts: Int = 0
    }

    private var health: [String: AppHealth] = [:]  // keyed by bundleId
    private let backoffThreshold = 3
    private let maxSkip = 30
    private var globalPollCount: Int = 0

    // MARK: - Public API

    /// Check if we should poll this app. Returns false if it's in backoff.
    func shouldPoll(bundleId: String) -> Bool {
        guard var entry = health[bundleId] else {
            return true  // unknown app, always poll
        }

        if entry.skipUntilPoll > 0 {
            entry.pollsSinceBackoff += 1
            if entry.pollsSinceBackoff >= entry.skipUntilPoll {
                // Backoff expired — allow one retry
                entry.pollsSinceBackoff = 0
                health[bundleId] = entry
                return true
            }
            health[bundleId] = entry
            return false
        }

        return true
    }

    /// Record a successful AX call — resets all counters for this app.
    func recordSuccess(bundleId: String) {
        if health[bundleId] != nil {
            health[bundleId]?.consecutiveTimeouts = 0
            health[bundleId]?.skipUntilPoll = 0
            health[bundleId]?.pollsSinceBackoff = 0
        }
    }

    /// Record an AX timeout — triggers backoff after threshold.
    func recordTimeout(bundleId: String, appName: String) {
        if health[bundleId] == nil {
            health[bundleId] = AppHealth()
        }
        health[bundleId]!.consecutiveTimeouts += 1
        health[bundleId]!.totalTimeouts += 1

        let consecutive = health[bundleId]!.consecutiveTimeouts

        if consecutive >= backoffThreshold {
            // Exponential backoff: 3^(consecutive - threshold + 1), capped
            let exponent = consecutive - backoffThreshold + 1
            let skip = min(Int(pow(3.0, Double(exponent))), maxSkip)
            health[bundleId]!.skipUntilPoll = skip
            health[bundleId]!.pollsSinceBackoff = 0

            let formatter = DateFormatter()
            formatter.dateFormat = "HH:mm:ss"
            print("[lurk \(formatter.string(from: Date()))] [Health] \(appName) (\(bundleId)) timed out \(consecutive)x — backing off \(skip) polls")
        }
    }

    /// Diagnostics summary for debugging.
    func diagnostics() -> [String: [String: Any]] {
        var result: [String: [String: Any]] = [:]
        for (bundleId, entry) in health where entry.totalTimeouts > 0 {
            result[bundleId] = [
                "consecutiveTimeouts": entry.consecutiveTimeouts,
                "totalTimeouts": entry.totalTimeouts,
                "skipUntilPoll": entry.skipUntilPoll,
                "pollsSinceBackoff": entry.pollsSinceBackoff,
            ]
        }
        return result
    }
}
