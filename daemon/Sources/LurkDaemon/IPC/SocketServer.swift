import Foundation

final class SocketServer {
    private let socketPath: String
    private var serverSocket: Int32 = -1
    private var clients: [Int32] = []
    private let queue = DispatchQueue(label: "com.lurk.socket", qos: .utility)
    private var listening = false

    init() {
        let lurkDir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".lurk")
        socketPath = lurkDir.appendingPathComponent("lurk.sock").path
    }

    func start() {
        queue.async { [weak self] in
            self?.listen()
        }
    }

    func stop() {
        listening = false
        for client in clients {
            close(client)
        }
        clients.removeAll()
        if serverSocket >= 0 {
            close(serverSocket)
            serverSocket = -1
        }
        unlink(socketPath)
    }

    func broadcast(_ event: RawEvent) {
        guard !clients.isEmpty else { return }

        var dict: [String: Any] = [
            "ts": event.ts,
            "event_type": event.eventType.rawValue,
        ]
        if let app = event.app { dict["app"] = app }
        if let bundleId = event.bundleId { dict["bundle_id"] = bundleId }
        if let title = event.title { dict["title"] = title }
        if let data = event.data { dict["data"] = data }

        guard let jsonData = try? JSONSerialization.data(withJSONObject: dict),
              var jsonString = String(data: jsonData, encoding: .utf8) else {
            return
        }
        jsonString += "\n"

        queue.async { [weak self] in
            guard let self = self else { return }
            let data = Array(jsonString.utf8)
            self.clients = self.clients.filter { fd in
                let written = send(fd, data, data.count, MSG_NOSIGNAL)
                return written >= 0
            }
        }
    }

    private func listen() {
        // Remove existing socket file
        unlink(socketPath)

        serverSocket = socket(AF_UNIX, SOCK_STREAM, 0)
        guard serverSocket >= 0 else {
            print("[lurk] Failed to create socket")
            return
        }

        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        socketPath.withCString { ptr in
            withUnsafeMutablePointer(to: &addr.sun_path) { pathPtr in
                let bound = pathPtr.withMemoryRebound(to: CChar.self, capacity: 104) { dest in
                    strncpy(dest, ptr, 103)
                    return true
                }
                _ = bound
            }
        }

        let bindResult = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockPtr in
                bind(serverSocket, sockPtr, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }

        guard bindResult == 0 else {
            print("[lurk] Failed to bind socket: \(String(cString: strerror(errno)))")
            return
        }

        Darwin.listen(serverSocket, 5)
        listening = true
        print("[lurk] Socket server listening at \(socketPath)")

        while listening {
            let clientFd = accept(serverSocket, nil, nil)
            if clientFd >= 0 {
                clients.append(clientFd)
            }
        }
    }
}

// MSG_NOSIGNAL is not available on macOS, use SO_NOSIGPIPE instead
#if !canImport(Glibc)
private let MSG_NOSIGNAL: Int32 = 0
#endif
