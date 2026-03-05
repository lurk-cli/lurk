import Foundation

enum EventType: String {
    case appSwitch = "app_switch"
    case titleChange = "title_change"
    case inputState = "input_state"
    case monitorState = "monitor_state"
    case calendarContext = "calendar_context"
    case screenshot = "screenshot"
}

enum InputState: String {
    case typing
    case reading
    case idle
}

struct MonitorWindow {
    let app: String
    let title: String
    let monitorId: Int
}

struct RawEvent {
    let ts: Double
    let eventType: EventType
    let app: String?
    let bundleId: String?
    let title: String?
    let data: [String: Any]?

    init(
        eventType: EventType,
        app: String? = nil,
        bundleId: String? = nil,
        title: String? = nil,
        data: [String: Any]? = nil
    ) {
        self.ts = Date().timeIntervalSince1970
        self.eventType = eventType
        self.app = app
        self.bundleId = bundleId
        self.title = title
        self.data = data
    }

    var dataJSON: String? {
        guard let data = data else { return nil }
        guard let jsonData = try? JSONSerialization.data(withJSONObject: data) else { return nil }
        return String(data: jsonData, encoding: .utf8)
    }
}
