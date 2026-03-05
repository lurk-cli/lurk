import ApplicationServices
import CoreGraphics
import Foundation

final class InputObserver: Observer {
    let name = "Input"
    private(set) var isRunning = false
    private weak var manager: ObserverManager?

    private var eventTap: CFMachPort?
    private var runLoopSource: CFRunLoopSource?

    // Atomic timestamps for input cadence
    private var lastKeyTime: Double = 0
    private var lastMouseTime: Double = 0
    private var currentState: InputState = .idle

    // Thresholds
    private let typingTimeout: TimeInterval = 2.0
    private let idleTimeout: TimeInterval = 120.0

    // State check timer
    private var stateTimer: DispatchSourceTimer?
    private let stateCheckInterval: TimeInterval = 3.0

    init(manager: ObserverManager) {
        self.manager = manager
    }

    func start() {
        guard AXIsProcessTrusted() else {
            print("[lurk] Accessibility permission not granted — input observer disabled")
            return
        }

        let eventMask: CGEventMask = (1 << CGEventType.keyDown.rawValue)
            | (1 << CGEventType.mouseMoved.rawValue)
            | (1 << CGEventType.leftMouseDown.rawValue)

        // Create a listen-only event tap
        guard let tap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .tailAppendEventTap,
            options: .listenOnly,
            eventsOfInterest: eventMask,
            callback: { _, type, event, refcon -> Unmanaged<CGEvent>? in
                guard let refcon = refcon else { return Unmanaged.passRetained(event) }
                let observer = Unmanaged<InputObserver>.fromOpaque(refcon).takeUnretainedValue()
                observer.handleEvent(type: type)
                return Unmanaged.passRetained(event)
            },
            userInfo: Unmanaged.passUnretained(self).toOpaque()
        ) else {
            print("[lurk] Failed to create event tap — input observer disabled")
            return
        }

        eventTap = tap
        runLoopSource = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), runLoopSource, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)

        // Timer to check and emit state transitions
        let timer = DispatchSource.makeTimerSource(queue: .global(qos: .utility))
        timer.schedule(deadline: .now() + stateCheckInterval, repeating: stateCheckInterval)
        timer.setEventHandler { [weak self] in
            self?.checkState()
        }
        timer.resume()
        stateTimer = timer

        isRunning = true
    }

    func stop() {
        if let tap = eventTap {
            CGEvent.tapEnable(tap: tap, enable: false)
        }
        if let source = runLoopSource {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes)
        }
        stateTimer?.cancel()
        stateTimer = nil
        eventTap = nil
        runLoopSource = nil
        isRunning = false
    }

    private func handleEvent(type: CGEventType) {
        let now = Date().timeIntervalSince1970
        switch type {
        case .keyDown:
            lastKeyTime = now
        case .mouseMoved, .leftMouseDown:
            lastMouseTime = now
        default:
            break
        }
    }

    private func checkState() {
        let now = Date().timeIntervalSince1970
        let sinceKey = now - lastKeyTime
        let sinceMouse = now - lastMouseTime
        let sinceAny = min(sinceKey, sinceMouse)

        let newState: InputState
        if sinceAny > idleTimeout {
            newState = .idle
        } else if sinceKey < typingTimeout {
            newState = .typing
        } else {
            newState = .reading
        }

        if newState != currentState {
            currentState = newState
            manager?.emit(RawEvent(
                eventType: .inputState,
                data: ["state": newState.rawValue]
            ))
        }
    }
}
