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
            // Attendee extraction for stakeholder tracking
            if let attendees = event.attendees {
                info["attendee_count"] = attendees.count
                info["attendees"] = attendees.map { participant in
                    var attendee: [String: Any] = [
                        "name": participant.name ?? participant.url.absoluteString,
                    ]
                    switch participant.participantRole {
                    case .required: attendee["role"] = "required"
                    case .optional: attendee["role"] = "optional"
                    case .chair: attendee["role"] = "chair"
                    case .nonParticipant: attendee["role"] = "non_participant"
                    @unknown default: attendee["role"] = "unknown"
                    }
                    switch participant.participantStatus {
                    case .accepted: attendee["status"] = "accepted"
                    case .declined: attendee["status"] = "declined"
                    case .tentative: attendee["status"] = "tentative"
                    case .pending: attendee["status"] = "pending"
                    @unknown default: attendee["status"] = "unknown"
                    }
                    return attendee
                }
            }
            // Meeting description/agenda
            if let notes = event.notes, !notes.isEmpty {
                info["description"] = String(notes.prefix(300))
            }
            // Location (room name or video link)
            if let location = event.location, !location.isEmpty {
                info["location"] = location
            }
            return info
        }

        manager?.emit(RawEvent(
            eventType: .calendarContext,
            data: ["upcoming_events": upcoming]
        ))
    }
}
