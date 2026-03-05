import EventKit
import Foundation

final class CalendarObserver: Observer {
    let name = "Calendar"
    private(set) var isRunning = false
    private weak var manager: ObserverManager?
    private var timer: DispatchSourceTimer?
    private let pollInterval: TimeInterval = 60.0
    private let eventStore = EKEventStore()
    private var hasAccess = false

    init(manager: ObserverManager) {
        self.manager = manager
    }

    func start() {
        requestAccess { [weak self] granted in
            guard let self = self, granted else {
                print("[lurk] Calendar access not granted — calendar observer disabled")
                return
            }
            self.hasAccess = true
            let timer = DispatchSource.makeTimerSource(queue: .global(qos: .utility))
            timer.schedule(deadline: .now(), repeating: self.pollInterval)
            timer.setEventHandler { [weak self] in
                self?.poll()
            }
            timer.resume()
            self.timer = timer
            self.isRunning = true
        }
    }

    func stop() {
        timer?.cancel()
        timer = nil
        isRunning = false
    }

    private func requestAccess(completion: @escaping (Bool) -> Void) {
        eventStore.requestAccess(to: .event) { granted, _ in
            completion(granted)
        }
    }

    private func poll() {
        guard hasAccess else { return }

        let now = Date()
        let lookahead = now.addingTimeInterval(30 * 60) // 30 minutes ahead
        let predicate = eventStore.predicateForEvents(withStart: now, end: lookahead, calendars: nil)
        let events = eventStore.events(matching: predicate)

        guard !events.isEmpty else { return }

        let upcoming: [[String: Any]] = events.prefix(5).map { event in
            var info: [String: Any] = [
                "title": event.title ?? "Untitled",
                "start": event.startDate.timeIntervalSince1970,
                "is_meeting": event.hasAttendees,
                "minutes_until": max(0, Int(event.startDate.timeIntervalSince(now) / 60)),
            ]
            if event.startDate <= now && event.endDate >= now {
                info["in_progress"] = true
            }
            return info
        }

        manager?.emit(RawEvent(
            eventType: .calendarContext,
            data: ["upcoming_events": upcoming]
        ))
    }
}
