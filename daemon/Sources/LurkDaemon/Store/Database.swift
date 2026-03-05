import Foundation
import SQLite3

final class Database {
    private var db: OpaquePointer?
    private let path: String
    private let queue = DispatchQueue(label: "com.lurk.database", qos: .utility)

    init() throws {
        let lurkDir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".lurk")
        try FileManager.default.createDirectory(at: lurkDir, withIntermediateDirectories: true)
        self.path = lurkDir.appendingPathComponent("store.db").path
        try open()
        try createSchema()
    }

    private func open() throws {
        let flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX
        let result = sqlite3_open_v2(path, &db, flags, nil)
        guard result == SQLITE_OK else {
            let msg = String(cString: sqlite3_errmsg(db))
            throw DatabaseError.openFailed(msg)
        }
        // Enable WAL mode for concurrent read/write
        try execute("PRAGMA journal_mode=WAL")
        try execute("PRAGMA synchronous=NORMAL")
        // Reasonable busy timeout for concurrent access
        sqlite3_busy_timeout(db, 5000)
    }

    private func createSchema() throws {
        try execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                event_type TEXT NOT NULL,
                app TEXT,
                bundle_id TEXT,
                title TEXT,
                data TEXT,
                enriched INTEGER DEFAULT 0
            )
        """)
        try execute("""
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)
        """)
        try execute("""
            CREATE INDEX IF NOT EXISTS idx_events_unenriched
            ON events(enriched) WHERE enriched = 0
        """)
    }

    func insertEvent(_ event: RawEvent) {
        queue.async { [weak self] in
            self?.doInsert(event)
        }
    }

    func insertEvents(_ events: [RawEvent]) {
        queue.async { [weak self] in
            guard let self = self else { return }
            try? self.execute("BEGIN TRANSACTION")
            for event in events {
                self.doInsert(event)
            }
            try? self.execute("COMMIT")
        }
    }

    private func doInsert(_ event: RawEvent) {
        let sql = """
            INSERT INTO events (ts, event_type, app, bundle_id, title, data, enriched)
            VALUES (?, ?, ?, ?, ?, ?, 0)
        """
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        defer { sqlite3_finalize(stmt) }

        sqlite3_bind_double(stmt, 1, event.ts)
        sqlite3_bind_text(stmt, 2, (event.eventType.rawValue as NSString).utf8String, -1, nil)
        bindOptionalText(stmt, 3, event.app)
        bindOptionalText(stmt, 4, event.bundleId)
        bindOptionalText(stmt, 5, event.title)
        bindOptionalText(stmt, 6, event.dataJSON)

        sqlite3_step(stmt)
    }

    private func bindOptionalText(_ stmt: OpaquePointer?, _ index: Int32, _ value: String?) {
        if let value = value {
            sqlite3_bind_text(stmt, index, (value as NSString).utf8String, -1, nil)
        } else {
            sqlite3_bind_null(stmt, index)
        }
    }

    @discardableResult
    private func execute(_ sql: String) throws -> Bool {
        var error: UnsafeMutablePointer<CChar>?
        let result = sqlite3_exec(db, sql, nil, nil, &error)
        if result != SQLITE_OK {
            let msg = error.map { String(cString: $0) } ?? "Unknown error"
            sqlite3_free(error)
            throw DatabaseError.queryFailed(msg)
        }
        return true
    }

    deinit {
        sqlite3_close(db)
    }
}

enum DatabaseError: Error {
    case openFailed(String)
    case queryFailed(String)
}
