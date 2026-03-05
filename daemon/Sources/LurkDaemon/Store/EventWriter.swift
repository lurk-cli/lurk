import Foundation

final class EventWriter {
    private let database: Database
    private var buffer: [RawEvent] = []
    private let bufferQueue = DispatchQueue(label: "com.lurk.eventwriter")
    private var flushTimer: DispatchSourceTimer?
    private let flushInterval: TimeInterval = 5.0
    private let maxBufferSize = 10
    private let maxBufferCapacity = 500

    init(database: Database) {
        self.database = database
        startFlushTimer()
    }

    func write(_ event: RawEvent) {
        // Sanitize title before buffering
        let sanitized: RawEvent
        if let title = event.title {
            sanitized = RawEvent(
                eventType: event.eventType,
                app: event.app,
                bundleId: event.bundleId,
                title: TitleSanitizer.sanitize(title),
                data: event.data
            )
        } else {
            sanitized = event
        }

        bufferQueue.async { [weak self] in
            guard let self = self else { return }

            // Hard cap: drop oldest events if buffer is overflowing
            if self.buffer.count >= self.maxBufferCapacity {
                let dropCount = self.buffer.count - self.maxBufferCapacity + 1
                self.buffer.removeFirst(dropCount)
                let formatter = DateFormatter()
                formatter.dateFormat = "HH:mm:ss"
                print("[lurk \(formatter.string(from: Date()))] [EventWriter] Buffer overflow — dropped \(dropCount) oldest events")
            }

            self.buffer.append(sanitized)
            if self.buffer.count >= self.maxBufferSize {
                self.flush()
            }
        }
    }

    func flush() {
        bufferQueue.async { [weak self] in
            guard let self = self, !self.buffer.isEmpty else { return }
            let events = self.buffer
            self.buffer.removeAll(keepingCapacity: true)
            self.database.insertEvents(events)
        }
    }

    private func startFlushTimer() {
        let timer = DispatchSource.makeTimerSource(queue: bufferQueue)
        timer.schedule(deadline: .now() + flushInterval, repeating: flushInterval)
        timer.setEventHandler { [weak self] in
            self?.flush()
        }
        timer.resume()
        flushTimer = timer
    }

    deinit {
        flushTimer?.cancel()
        // Flush remaining events synchronously
        bufferQueue.sync {
            if !buffer.isEmpty {
                database.insertEvents(buffer)
                buffer.removeAll()
            }
        }
    }
}
